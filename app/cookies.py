"""Encrypted cookie-vault storage and short-lived CLI cookie materialization."""

from __future__ import annotations

import base64
import binascii
import os
import tempfile
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
        binary = base64.urlsafe_b64decode(content)
    except (binascii.Error, ValueError):
        return False
    return (content.endswith(b"=") and len(binary) >= 73 and binary[0] == 0x80
            and (len(binary) - 57) % 16 == 0)


def read_cookie_file(path: Path, encryption_key: str | None) -> bytes:
    content = path.read_bytes()
    if not encryption_key:
        if _looks_like_fernet_token(content):
            raise ValueError("APP_ENCRYPTION_KEY is required before reading encrypted cookie files")
        return content
    try:
        return _cipher(encryption_key).decrypt(content)
    except InvalidToken:
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
