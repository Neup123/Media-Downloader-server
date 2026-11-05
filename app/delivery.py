from __future__ import annotations

import asyncio
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import DeliverySetting


class DeliveryError(RuntimeError):
    pass


def _relative_output(source: Path, job_id: str, settings: Settings) -> Path:
    """Keep source directory structure and reject outputs outside the job workspace."""
    root = (settings.data_dir / "jobs" / job_id).resolve()
    original = source.absolute()
    try:
        raw_relative = original.relative_to(root)
        for depth in range(1, len(raw_relative.parts) + 1):
            if root.joinpath(*raw_relative.parts[:depth]).is_symlink():
                raise DeliveryError("Symlinked output paths are not allowed")
        relative = source.resolve(strict=True).relative_to(root)
    except (ValueError, OSError) as exc:
        raise DeliveryError("Output file is outside its job directory or unavailable") from exc
    if not relative.parts or not source.is_file():
        raise DeliveryError("Output is not a regular file")
    return relative



def _webdav_uploaded_size(payload: bytes, target_url: str) -> int | None:
    """Read getcontentlength only for the requested resource's 200 propstat.

    RFC 4918 returns property-level statuses inside 207 Multi-Status. A 207
    alone does not establish that the uploaded resource exists or that its
    getcontentlength property was retrieved successfully.
    """
    if len(payload) > 1_000_000:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    ns = "{DAV:}"
    if root.tag != ns + "multistatus":
        return None
    expected = urlsplit(target_url)
    sizes: list[int] = []
    for response in root.findall(ns + "response"):
        href = response.findtext(ns + "href")
        if not href:
            continue
        found = urlsplit(urljoin(target_url, href.strip()))
        if (
            found.scheme != expected.scheme
            or found.netloc != expected.netloc
            or unquote(found.path) != unquote(expected.path)
            or found.query
            or found.fragment
        ):
            continue
        for propstat in response.findall(ns + "propstat"):
            status = (propstat.findtext(ns + "status") or "").strip()
            if re.match(r"^HTTP/\d+(?:\.\d+)?\s+200(?:\s|$)", status) is None:
                continue
            prop = propstat.find(ns + "prop")
            length = prop.findtext(ns + "getcontentlength") if prop is not None else None
            if length is None:
                continue
            try:
                size = int(length)
            except ValueError:
                continue
            if size >= 0:
                sizes.append(size)
    return sizes[0] if len(sizes) == 1 else None


def _fernet(settings: Settings) -> Fernet:
    if not settings.encryption_key:
        raise DeliveryError("APP_ENCRYPTION_KEY is required before storing a WebDAV app password")
    try:
        return Fernet(settings.encryption_key.encode("ascii"))
    except Exception as exc:
        raise DeliveryError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def decrypt_password(encrypted: str | None, settings: Settings) -> str:
    if not encrypted:
        raise DeliveryError("WebDAV app password has not been configured")
    try:
        return _fernet(settings).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise DeliveryError("Stored WebDAV app password cannot be decrypted; verify APP_ENCRYPTION_KEY") from exc


def serialize_delivery(setting: DeliverySetting | None) -> dict[str, Any]:
    if not setting:
        return {"mode": "local", "folder": "Downloader", "password_configured": False}
    return {
        "mode": setting.mode,
        "webdav_url": setting.webdav_url,
        "username": setting.username,
        "folder": setting.folder,
        "password_configured": bool(setting.password_encrypted),
    }


def set_delivery_settings(session: Session, payload, settings: Settings) -> dict[str, Any]:
    row = session.get(DeliverySetting, 1)
    if row is None:
        row = DeliverySetting(id=1, mode=payload.mode, folder=payload.folder)
        session.add(row)
    row.mode = payload.mode
    row.folder = payload.folder
    row.webdav_url = payload.webdav_url.strip().rstrip("/") if payload.webdav_url else None
    row.username = payload.username.strip() if payload.username else None
    if payload.app_password:
        row.password_encrypted = _fernet(settings).encrypt(payload.app_password.encode("utf-8")).decode("ascii")
    if payload.mode == "webdav" and not row.password_encrypted:
        raise DeliveryError("Enter the WebDAV app password before enabling WebDAV delivery")
    session.commit()
    session.refresh(row)
    return serialize_delivery(row)


async def deliver_files(
    files: list[Path],
    job_id: str,
    session_factory,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    on_delivered: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    async def check_cancelled() -> None:
        if is_cancelled is not None and await is_cancelled():
            raise asyncio.CancelledError("Delivery cancelled")

    async def record(item: dict[str, Any]) -> None:
        if on_delivered is not None:
            await on_delivered(item)
    with session_factory() as session:
        setting = session.get(DeliverySetting, 1)
        if setting is None or setting.mode == "local":
            target_dir = settings.output_dir / job_id
            target_dir.mkdir(parents=True, exist_ok=True)
            output: list[dict[str, Any]] = []
            for source in files:
                await check_cancelled()
                relative = _relative_output(source, job_id, settings)
                destination = target_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Publish the local file only after all bytes are copied.
                temporary: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=destination.parent, prefix=".downloader-", suffix=".part", delete=False
                    ) as handle:
                        temporary = Path(handle.name)
                        with source.open("rb") as stream:
                            while True:
                                await check_cancelled()
                                chunk = await asyncio.to_thread(stream.read, 1024 * 1024)
                                if not chunk:
                                    break
                                await asyncio.to_thread(handle.write, chunk)
                    await check_cancelled()
                    if temporary.stat().st_size != source.stat().st_size:
                        raise DeliveryError(f"Local copy was truncated: {source.name}")
                    os.replace(temporary, destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                item = {
                    "filename": destination.name,
                    "size_bytes": destination.stat().st_size,
                    "path": str(destination),
                    "delivery": "local",
                    "verified": destination.is_file() and destination.stat().st_size == source.stat().st_size,
                }
                if not item["verified"]:
                    raise DeliveryError("Local delivery verification failed")
                output.append(item)
                await record(item)
            await check_cancelled()
            return output
        data = {
            "url": setting.webdav_url,
            "username": setting.username,
            "password": decrypt_password(setting.password_encrypted, settings),
            "folder": setting.folder,
        }

    if not data["url"] or not data["username"]:
        raise DeliveryError("WebDAV URL and username are not configured")
    base_parts = urlsplit(data["url"])
    if base_parts.scheme not in {"https", "http"} or not base_parts.netloc or base_parts.username or base_parts.password:
        raise DeliveryError("WebDAV URL must be an absolute HTTP(S) URL without embedded credentials")
    base_url = data["url"].rstrip("/")
    folder_parts = [p for p in data["folder"].split("/") if p]
    if any(part in {".", ".."} or "\\x00" in part for part in folder_parts):
        raise DeliveryError("WebDAV destination folder must not contain dot segments or NUL bytes")
    prefix_parts = folder_parts + [job_id]
    output = []
    client_options = {
        "timeout": httpx.Timeout(60.0, connect=15.0),
        "auth": (data["username"], data["password"]),
        "follow_redirects": False,
    }
    if transport is not None:
        client_options["transport"] = transport
    async with httpx.AsyncClient(**client_options) as client:
        current = base_url
        for segment in prefix_parts:
            await check_cancelled()
            current += "/" + quote(segment, safe="")
            response = await client.request("MKCOL", current)
            if response.status_code not in {201, 405}:
                raise DeliveryError(f"WebDAV directory creation failed ({response.status_code})")
        created_folders = set()
        for source in files:
            await check_cancelled()
            relative = _relative_output(source, job_id, settings)
            parent_url = current
            for segment in relative.parts[:-1]:
                await check_cancelled()
                parent_url += "/" + quote(segment, safe="")
                if parent_url not in created_folders:
                    response = await client.request("MKCOL", parent_url)
                    if response.status_code not in {201, 405}:
                        raise DeliveryError(f"WebDAV nested directory creation failed ({response.status_code})")
                    created_folders.add(parent_url)
            target = parent_url + "/" + quote(relative.name, safe="")

            async def file_chunks(path: Path):
                with path.open("rb") as stream:
                    while True:
                        await check_cancelled()
                        chunk = await asyncio.to_thread(stream.read, 1024 * 1024)
                        if not chunk:
                            break
                        yield chunk

            response = await client.put(
                target,
                content=file_chunks(source),
                headers={"Content-Type": "application/octet-stream"},
            )
            if response.status_code not in {200, 201, 204}:
                raise DeliveryError(f"WebDAV PUT failed for {source.name} ({response.status_code})")
            await check_cancelled()
            verify = await client.request(
                "PROPFIND",
                target,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>',
            )
            if verify.status_code != 207:
                raise DeliveryError(f"WebDAV verification failed for {source.name} ({verify.status_code})")
            remote_size = _webdav_uploaded_size(verify.content, target)
            expected_size = source.stat().st_size
            if remote_size != expected_size:
                raise DeliveryError(f"WebDAV size verification failed for {source.name}: expected {expected_size}, got {remote_size}")
            item = {
                "filename": source.name,
                "size_bytes": expected_size,
                "path": target,
                "delivery": "webdav",
                "verified": True,
            }
            output.append(item)
            await record(item)
    await check_cancelled()
    return output
