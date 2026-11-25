"""Encrypted cookie-vault storage and short-lived CLI cookie materialization."""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
from urllib.parse import urlsplit
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cryptography.fernet import Fernet, InvalidToken


def _cipher(encryption_key: str | None) -> Fernet:
    if not encryption_key:
        raise ValueError("APP_ENCRYPTION_KEY is required before storing cookie files")
    try:
        return Fernet(encryption_key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt_cookie(content: bytes, encryption_key: str | None) -> bytes:
    """Never write an unencrypted upload to the vault."""
    return _cipher(encryption_key).encrypt(content)


def _looks_like_fernet_token(content: bytes) -> bool:
    """Avoid passing a stored Fernet token to an engine when the key is absent."""
    try:
        binary = base64.b64decode(content, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return False
    return (len(binary) >= 73 and binary[0] == 0x80
            and (len(binary) - 57) % 16 == 0)


def read_cookie_file(path: Path, encryption_key: str | None) -> bytes:
    content = path.read_bytes()
    # Current Fernet tokens can have no '=' padding. Never treat a token
    # encrypted with an old/different key as a legacy plaintext export.
    encrypted = _looks_like_fernet_token(content) or content.startswith(b"gAAAA")
    if not encryption_key:
        if encrypted:
            raise ValueError("APP_ENCRYPTION_KEY is required before reading encrypted cookie files")
        return content
    try:
        return _cipher(encryption_key).decrypt(content)
    except InvalidToken as exc:
        if encrypted:
            raise ValueError("Stored cookies cannot be decrypted; re-upload with the configured key") from exc
        # Pre-existing plaintext files remain plaintext until explicitly re-uploaded.
        return content


@contextmanager
def materialize_cookie_file(
    source: Path, workdir: Path, encryption_key: str | None,
) -> Iterator[Path]:
    """Expose a 0600 plaintext copy only for the lifetime of an engine run.

    The directory is a unique per-job sibling of the output workdir, so CLI
    output enumeration never mistakes the temporary cookie for a download.
    """
    content = read_cookie_file(source, encryption_key)
    with tempfile.TemporaryDirectory(
        prefix=f".downloader-cookie-{workdir.name}-", dir=workdir.parent,
    ) as directory:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix="cookies-", suffix=".txt", delete=False,
        ) as stream:
            os.chmod(stream.name, 0o600)
            stream.write(content)
            stream.flush()
        yield Path(stream.name)


def store_cookie_file(directory: Path, filename: str, content: bytes, encryption_key: str | None) -> None:
    """One atomic encrypted writer for personal and instance-provided cookie files."""
    encrypted = encrypt_cookie(content, encryption_key)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("Cookie directory must not be a symlink")
    if Path(filename).name != filename or filename in {".", ".."} or "\\" in filename:
        raise ValueError("Invalid cookie filename")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=".downloader-cookie-",
            suffix=".part", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(stream.name, 0o600)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def provided_hostname(value: str) -> str:
    """Explicit exact hostname, not a wildcard or URL; no implicit site expansion."""
    if not isinstance(value, str) or not value or len(value) > 253 or value != value.strip():
        raise ValueError("Provide an exact public DNS hostname")
    try:
        host = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Provide an exact public DNS hostname") from exc
    if (not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host)
            or host.endswith((".local", ".localhost", ".internal"))
            or host.rsplit(".", 1)[-1].isdigit()):
        raise ValueError("Provide an exact public DNS hostname")
    from app.security import validate_target_url
    validate_target_url("https://" + host, "yt-dlp")
    return host


def validate_provided_cookie_export(content: bytes, hostname: str) -> None:
    """Require a real Netscape export without unrelated-site cookies.

    Cookie-jar rules still govern which request paths/domains receive cookies.
    An admin must explicitly provision every source hostname; this never
    treats public suffixes or parent domains as wildcard provisioning rules.
    """
    hostname = provided_hostname(hostname)
    try:
        data = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Provide a valid Netscape cookies.txt export") from exc
    if not 1 <= len(content) <= 1_000_000 or "\x00" in data:
        raise ValueError("Provide a valid Netscape cookies.txt export")
    lines = data.splitlines()
    if not lines or lines[0] not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
        raise ValueError("Provide a valid Netscape cookies.txt export")
    count = 0
    for line in lines[1:]:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("Provide a valid Netscape cookies.txt export")
        domain, include_subdomains, path, secure, expiry, name, _value = fields
        domain = provided_hostname(domain.lstrip("."))
        if not (hostname == domain or hostname.endswith("." + domain)
                or domain.endswith("." + hostname)):
            raise ValueError("Cookie export contains an unrelated hostname")
        if (include_subdomains not in {"TRUE", "FALSE"} or secure not in {"TRUE", "FALSE"}
                or not path.startswith("/") or not re.fullmatch(r"[0-9]{1,19}", expiry)
                or not name or any(ord(ch) < 32 or ord(ch) == 127 for field in fields for ch in field)):
            raise ValueError("Provide a valid Netscape cookies.txt export")
        count += 1
    if not count:
        raise ValueError("Cookie export contains no cookies")


def provided_cookie_dir(data_dir: Path) -> Path:
    return data_dir / "provided-cookies"


def provided_cookie_for_url(data_dir: Path, url: str) -> Path | None:
    """Read only exact-host instance provisioning; user selection happens first."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = provided_hostname(parsed.hostname or "")
    except ValueError:
        return None
    directory = provided_cookie_dir(data_dir)
    if directory.is_symlink():
        raise ValueError("Provided cookie directory must not be a symlink")
    target = directory / (host + ".txt")
    if target.is_symlink():
        raise ValueError("Provided cookie file must not be a symlink")
    return target if target.is_file() else None
