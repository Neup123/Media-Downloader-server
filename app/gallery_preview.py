"""Metadata-only gallery-dl preview primitive; no job or download submission.

Uses the pinned gallery-dl CLI's documented --dump-json and --range flags.
Only flat direct-file message streams are supported. Child extractors are
not recursively resolved: chapter selection needs its own stable contract.
No public endpoint enables this primitive until submission routing agrees.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

from gallery_dl.extractor.message import Message

from app.adapters.process_env import downloader_environment
from app.discovery import (
    PREVIEW_TIMEOUT_SECONDS, PreviewError, _optional_bytes, _optional_text,
    read_bounded_preview, summarize_metadata, terminate_preview_process,
)

from app.gallery_selection import MAX_GALLERY_OFFSET, MAX_GALLERY_PAGE


def _validate_window(max_entries: int, offset: int) -> None:
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_GALLERY_PAGE:
        raise ValueError(f"max_entries must be between 1 and {MAX_GALLERY_PAGE}")
    if type(offset) is not int or not 0 <= offset <= MAX_GALLERY_OFFSET:
        raise ValueError(f"gallery preview offset must be between 0 and {MAX_GALLERY_OFFSET}")


def _safe_title(value: object, limit: int = 500) -> str | None:
    title = _optional_text(value, limit)
    if title is None or "://" in title or any(ord(c) < 32 for c in title):
        return None
    return title


def _public_thumbnail(value: object) -> str | None:
    # Never substitute the actual download URL for a thumbnail, or return
    # query-bearing/signed URLs. Only an explicitly named thumbnail is used.
    url = _optional_text(value, 4096)
    if not url or url != value:
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            return None
        from app.security import validate_target_url
        return validate_target_url(url, "gallery-dl", allow_private=False)
    except ValueError:
        return None


def summarize_gallery_messages(messages: object, max_entries: int, offset: int = 0) -> dict:
    _validate_window(max_entries, offset)
    if not isinstance(messages, list):
        raise PreviewError("Invalid gallery metadata response")
    entries = []
    title = None
    for message in messages:
        if not isinstance(message, list) or not message or type(message[0]) is not int:
            raise PreviewError("Invalid gallery metadata message")
        kind = message[0]
        if kind == Message.Directory:
            if len(message) != 2 or not isinstance(message[1], dict):
                raise PreviewError("Invalid gallery directory metadata")
            title = title or _safe_title(message[1].get("title"))
            continue
        if kind == Message.Queue:
            raise PreviewError("Nested gallery or chapter selection is not supported yet")
        if kind != Message.Url or len(message) != 3 or not isinstance(message[2], dict):
            # DataJob can report an embedded error despite exiting zero.
            raise PreviewError("Gallery metadata extraction did not complete")
        data = message[2]
        if len(entries) >= max_entries + 1:
            raise PreviewError("Gallery extractor exceeded the requested range")
        index = offset + len(entries) + 1
        # Do not expose arbitrary source IDs (they may be URLs), actual file
        # URLs, metadata dictionaries, tokens, cookies or HTTP headers.
        entries.append({
            "id": str(index),
            "title": _safe_title(data.get("title")) or _safe_title(data.get("filename"))
                     or f"Item {index}",
            "thumbnail": _public_thumbnail(data.get("thumbnail")),
            "filesize": _optional_bytes(data.get("filesize")),
        })
    if not entries and offset == 0:
        raise PreviewError("No selectable gallery files were reported")
    return summarize_metadata({
        "title": title or "Image gallery",
        "extractor_key": "gallery-dl",
        "entries": entries,
    }, max_entries, offset)


async def inspect_gallery_dl(
    url: str, *, max_entries: int = 50, offset: int = 0,
    executable: str = "gallery-dl", timeout_seconds: float = PREVIEW_TIMEOUT_SECONDS,
) -> dict:
    """Only metadata; no config, authentication, external child resolution or files."""
    _validate_window(max_entries, offset)
    from app.security import validate_target_url
    url = validate_target_url(url, "gallery-dl", allow_private=False)
    # Caller must apply existing public DNS/egress enforcement before exposure.
    argv = [
        executable, "--config-ignore", "--no-input", "--no-colors",
        "--dump-json", "--range", f"{offset + 1}-{offset + max_entries + 1}",
        "-o", "extractor.retries=0", "--", url,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=downloader_environment(),
        )
    except OSError as exc:
        raise PreviewError("Gallery metadata engine is unavailable") from exc
    try:
        raw = await read_bounded_preview(process, timeout_seconds)
        try:
            code = await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError as exc:
            raise PreviewError("Gallery metadata inspection timed out") from exc
        if code != 0:
            raise PreviewError("Gallery metadata extraction failed")
        try:
            messages = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PreviewError("Gallery engine returned invalid metadata") from exc
        return summarize_gallery_messages(messages, max_entries, offset)
    finally:
        await terminate_preview_process(process)
