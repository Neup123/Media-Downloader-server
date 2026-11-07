from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import secrets
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.completion import _file_sha256
from app.config import Settings
from app.db import DeliverySetting, Job
from app.security import UnsafeUrlError, resolve_http_target, validate_target_url


class DeliveryError(RuntimeError):
    pass


def _validate_webdav_destination(url: str, settings: Settings) -> None:
    """Apply a delivery-specific network policy, independent of media sources."""
    try:
        validate_target_url(url, "generic", settings.allow_private_webdav_targets)
    except UnsafeUrlError as exc:
        raise DeliveryError(f"WebDAV destination is not allowed: {exc}") from exc
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        raise DeliveryError("WebDAV destination URL cannot contain a query or fragment")
    if parts.scheme == "http" and not settings.allow_insecure_webdav:
        raise DeliveryError(
            "Plain HTTP WebDAV would expose the app password; "
            "use HTTPS, or explicitly set ALLOW_INSECURE_WEBDAV=true only on a trusted network"
        )


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



def _nextcloud_file_id(payload: bytes, target_url: str) -> str | None:
    """Read the stable Nextcloud file ID only from this file's successful DAV propstat.

    File IDs survive rename/move within Nextcloud, unlike WebDAV URL paths.
    Return None for generic WebDAV servers without the Nextcloud extension.
    """
    if len(payload) > 1_000_000:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    ns = "{DAV:}"
    nc = "{http://owncloud.org/ns}"
    if root.tag != ns + "multistatus":
        return None
    expected = urlsplit(target_url)
    ids: list[str] = []
    for response in root.findall(ns + "response"):
        href = response.findtext(ns + "href")
        if not href:
            continue
        found = urlsplit(urljoin(target_url, href.strip()))
        if (found.scheme != expected.scheme or found.netloc != expected.netloc
                or unquote(found.path) != unquote(expected.path)
                or found.query or found.fragment):
            continue
        for propstat in response.findall(ns + "propstat"):
            status = (propstat.findtext(ns + "status") or "").strip()
            if re.match(r"^HTTP/\d+(?:\.\d+)?\s+200(?:\s|$)", status) is None:
                continue
            prop = propstat.find(ns + "prop")
            file_id = prop.findtext(nc + "fileid") if prop is not None else None
            if file_id and file_id.isdecimal() and len(file_id) <= 32:
                ids.append(file_id)
    return ids[0] if len(ids) == 1 else None


def _webdav_is_collection(payload: bytes, target_url: str) -> bool:
    """Verify a successful DAV:resourcetype collection property for this URL.

    MKCOL 405 is ambiguous: the name may be occupied by a regular file,
    or the endpoint may refuse MKCOL entirely. Only a matching collection
    with a successful property status is safe to use as a parent folder.
    """
    if len(payload) > 1_000_000:
        return False
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return False
    ns = "{DAV:}"
    if root.tag != ns + "multistatus":
        return False
    expected = urlsplit(target_url)
    successful_collections = 0
    for response in root.findall(ns + "response"):
        href = response.findtext(ns + "href")
        if not href:
            continue
        found = urlsplit(urljoin(target_url.rstrip("/") + "/", href.strip()))
        if (
            found.scheme != expected.scheme
            or found.netloc != expected.netloc
            or unquote(found.path).rstrip("/") != unquote(expected.path).rstrip("/")
            or found.query or found.fragment
        ):
            continue
        for propstat in response.findall(ns + "propstat"):
            status = (propstat.findtext(ns + "status") or "").strip()
            if re.match(r"^HTTP/\d+(?:\.\d+)?\s+200(?:\s|$)", status) is None:
                continue
            prop = propstat.find(ns + "prop")
            resource_type = prop.find(ns + "resourcetype") if prop is not None else None
            if resource_type is not None and resource_type.find(ns + "collection") is not None:
                successful_collections += 1
    # A 207 response can contain multiple entries for the same href. Do not
    # treat duplicate successful collection properties as unambiguous proof.
    return successful_collections == 1


async def _ensure_webdav_collection(client: httpx.AsyncClient, url: str, label: str) -> None:
    """Create a WebDAV folder or verify the already-occupied path is a folder."""
    response = await client.request("MKCOL", url)
    if response.status_code == 201:
        return
    if response.status_code != 405:
        raise _delivery_failure(response.status_code, label)
    verify = await client.request(
        "PROPFIND", url.rstrip("/") + "/",
        headers={"Depth": "0", "Content-Type": "application/xml"},
        content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>',
    )
    if verify.status_code != 207 or not _webdav_is_collection(verify.content, url):
        raise DeliveryError(f"{label}: MKCOL returned 405 but the resource is not a verified collection")


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


def encrypt_job_delivery(payload, owner_id: str, settings: Settings) -> str:
    """Snapshot a Nextcloud user's WebDAV destination per job, encrypted at rest."""
    if payload.mode != "webdav" or not payload.app_password:
        raise DeliveryError("Per-user jobs require an individual WebDAV app password")
    parts = urlsplit(payload.webdav_url or "")
    # The WebDAV root MUST be this owner's Files, even if a compromised gateway
    # submits a different username/URL. The core API token remains server-only.
    if (
        payload.username != owner_id
        or not parts.path.endswith("/remote.php/dav/files/" + quote(owner_id, safe=""))
    ):
        raise DeliveryError("Per-user WebDAV credentials must target that user's own Files")
    _validate_webdav_destination(payload.webdav_url, settings)
    data = {
        "url": payload.webdav_url.strip().rstrip("/"),
        "username": payload.username,
        "password": payload.app_password,
        "folder": payload.folder,
    }
    return _fernet(settings).encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def _delivery_failure(status: int, label: str) -> DeliveryError:
    reasons = {
        507: "Nextcloud storage quota exceeded; free space in Files and retry",
        403: "Nextcloud destination is not writable; select a writable folder or request access and retry",
        404: "Nextcloud destination folder is missing; recreate or select the folder and retry",
    }
    return DeliveryError(reasons.get(status, f"{label} ({status})"))


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
    if payload.mode == "webdav":
        _validate_webdav_destination(payload.webdav_url or "", settings)
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


async def verify_delivery_destination(
    session_factory,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Verify the configured destination without creating a download job.

    Local mode writes/reads/deletes a small file. WebDAV mode uploads, PROPFINDs,
    reads back and deletes a unique small file. This is a real remote test, not
    proof of general internet or unrestricted SSRF safety: guarded host egress
    is still mandatory for untrusted inputs.
    """
    payload = b"Downloader delivery preflight\n"
    with session_factory() as session:
        row = session.get(DeliverySetting, 1)
        if row is None or row.mode == "local":
            mode = "local"
            destination = settings.output_dir
        else:
            mode = "webdav"
            base_url = row.webdav_url or ""
            username = row.username or ""
            folder = row.folder
            password = decrypt_password(row.password_encrypted, settings)
    if mode == "local":
        try:
            destination.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w+b", dir=destination, prefix=".downloader-check-", delete=True
            ) as check:
                check.write(payload)
                check.flush()
                check.seek(0)
                if check.read() != payload:
                    raise DeliveryError("Local directory readback did not match the temporary file")
                sample = Path(check.name)
            if sample.exists():
                raise DeliveryError("Local temporary file cleanup could not be verified")
            return {"mode": mode, "verified": True, "cleaned_up": True,
                    "message": "Local destination was writable and the test file was removed"}
        except OSError as exc:
            raise DeliveryError("Local destination is not writable or cannot be verified") from exc
    if not base_url or not username:
        raise DeliveryError("Configure the WebDAV URL and username before testing delivery")
    _validate_webdav_destination(base_url, settings)
    if transport is None and not settings.allow_private_webdav_targets:
        try:
            await asyncio.to_thread(resolve_http_target, base_url, False)
        except UnsafeUrlError as exc:
            raise DeliveryError("WebDAV test destination DNS is not allowed") from exc
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise DeliveryError("WebDAV test requires an absolute URL without embedded credentials")
    parts = [part for part in folder.split("/") if part]
    if any(part in {".", ".."} or "\x00" in part for part in parts):
        raise DeliveryError("WebDAV test folder contains invalid path segments")
    current = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(
            auth=(username, password), timeout=httpx.Timeout(25.0, connect=10.0),
            trust_env=False, follow_redirects=False, transport=transport,
        ) as client:
            for segment in parts:
                current += "/" + quote(segment, safe="")
                await _ensure_webdav_collection(client, current, "Could not access WebDAV test folder")
            temporary_url = current + "/.downloader-check-" + secrets.token_hex(12)
            uploaded = False
            try:
                response = await client.put(
                    temporary_url, content=payload,
                    headers={"Content-Type": "application/octet-stream"},
                )
                if response.status_code not in {200, 201, 204}:
                    raise _delivery_failure(response.status_code, "WebDAV test upload was rejected")
                uploaded = True
                info = await client.request(
                    "PROPFIND", temporary_url,
                    headers={"Depth": "0", "Content-Type": "application/xml"},
                    content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>',
                )
                if info.status_code != 207 or _webdav_uploaded_size(info.content, temporary_url) != len(payload):
                    raise DeliveryError("WebDAV test could not verify the uploaded file size")
                content = await client.get(temporary_url)
                if content.status_code != 200 or content.content != payload:
                    raise DeliveryError("WebDAV test readback did not match the uploaded bytes")
            finally:
                if uploaded:
                    try:
                        cleanup = await client.delete(temporary_url)
                    except httpx.RequestError as exc:
                        raise DeliveryError("Temporary WebDAV test file cleanup could not be verified") from exc
                    if cleanup.status_code not in {200, 202, 204}:
                        raise DeliveryError("Temporary WebDAV test file cleanup failed")
    except httpx.RequestError as exc:
        # Never echo the remote URL or credentials from exception strings.
        raise DeliveryError("Could not reach the WebDAV destination; check URL, TLS and app password") from exc
    return {"mode": mode, "verified": True, "cleaned_up": True,
            "message": "WebDAV test upload, readback and cleanup all succeeded"}


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
        job = session.get(Job, job_id)
        per_user = bool(job and job.owner_id)
        if per_user:
            if not job.delivery_token:
                raise DeliveryError("Per-user delivery configuration missing; choose your destination again")
            try:
                data = json.loads(_fernet(settings).decrypt(job.delivery_token.encode("ascii")).decode("utf-8"))
            except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
                raise DeliveryError("Per-user WebDAV credentials unavailable; reconnect Files and retry") from exc
            if data.get("username") != job.owner_id:
                raise DeliveryError("Per-user destination owner mismatch; reconnect Files")
            setting = None
        else:
            setting = session.get(DeliverySetting, 1)
        if not per_user and (setting is None or setting.mode == "local"):
            target_dir = settings.output_dir / job_id
            target_dir.mkdir(parents=True, exist_ok=True)
            output: list[dict[str, Any]] = []
            for source in files:
                await check_cancelled()
                relative = _relative_output(source, job_id, settings)
                destination = target_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Verify the actual bytes in the temporary copy before
                # atomically publishing it. Matching sizes alone cannot detect
                # same-size corruption.
                temporary: Path | None = None
                source_before = source.stat()
                source_digest = hashlib.sha256()
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
                                source_digest.update(chunk)
                                await asyncio.to_thread(handle.write, chunk)
                    await check_cancelled()
                    source_after = source.stat()
                    if (
                        source_before.st_size != source_after.st_size
                        or source_before.st_mtime_ns != source_after.st_mtime_ns
                    ):
                        raise DeliveryError(f"Source changed during local delivery: {source.name}")
                    if temporary.stat().st_size != source_before.st_size:
                        raise DeliveryError(f"Local copy was truncated: {source.name}")
                    copied_digest = await asyncio.to_thread(_file_sha256, temporary)
                    await check_cancelled()
                    if copied_digest != source_digest.hexdigest():
                        raise DeliveryError(f"Local copy checksum verification failed: {source.name}")
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
        if not per_user:
            data = {
                "url": setting.webdav_url,
                "username": setting.username,
                "password": decrypt_password(setting.password_encrypted, settings),
                "folder": setting.folder,
            }

    if not data["url"] or not data["username"]:
        raise DeliveryError("WebDAV URL and username are not configured")
    _validate_webdav_destination(data["url"], settings)
    if transport is None and not settings.allow_private_webdav_targets:
        # Defense in depth: reject current DNS answers that include private or
        # reserved targets. httpx cannot pin these answers, so DNS rebinding
        # still requires a production egress firewall.
        try:
            await asyncio.to_thread(resolve_http_target, data["url"], False)
        except UnsafeUrlError as exc:
            raise DeliveryError(f"WebDAV destination DNS check failed: {exc}") from exc
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
        "trust_env": False,
    }
    if transport is not None:
        client_options["transport"] = transport
    async with httpx.AsyncClient(**client_options) as client:
        current = base_url
        for index, segment in enumerate(prefix_parts):
            await check_cancelled()
            current += "/" + quote(segment, safe="")
            if per_user and index < len(folder_parts):
                # The user's chosen destination must exist; do not silently
                # recreate a deleted or forbidden Nextcloud folder.
                verify_folder = await client.request(
                    "PROPFIND", current.rstrip("/") + "/",
                    headers={"Depth": "0", "Content-Type": "application/xml"},
                    content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>',
                )
                if verify_folder.status_code != 207:
                    raise _delivery_failure(verify_folder.status_code, "Nextcloud folder verification failed")
                if not _webdav_is_collection(verify_folder.content, current):
                    raise DeliveryError("Nextcloud destination folder is missing; select an existing folder and retry")
            else:
                await _ensure_webdav_collection(client, current, "WebDAV directory creation failed")
        created_folders = set()
        for source in files:
            await check_cancelled()
            relative = _relative_output(source, job_id, settings)
            parent_url = current
            for segment in relative.parts[:-1]:
                await check_cancelled()
                parent_url += "/" + quote(segment, safe="")
                if parent_url not in created_folders:
                    await _ensure_webdav_collection(client, parent_url, "WebDAV nested directory creation failed")
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
                raise _delivery_failure(response.status_code, f"WebDAV PUT failed for {source.name}")
            await check_cancelled()
            verify = await client.request(
                "PROPFIND",
                target,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                content='<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:getcontentlength/><oc:fileid/></d:prop></d:propfind>',
            )
            if verify.status_code != 207:
                raise _delivery_failure(verify.status_code, f"WebDAV verification failed for {source.name}")
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
                "file_id": _nextcloud_file_id(verify.content, target) if per_user else None,
            }
            output.append(item)
            await record(item)
    await check_cancelled()
    return output
