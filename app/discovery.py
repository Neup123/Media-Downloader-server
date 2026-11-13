"""Bounded yt-dlp metadata preview; does not download media."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.adapters.process_env import downloader_environment

MAX_PREVIEW_OUTPUT = 2_000_000
PREVIEW_TIMEOUT_SECONDS = 45


REASON_CODES = frozenset({
    "malformed_link", "unsupported_domain", "item_not_found", "not_media", "could_not_check",
})


class PreviewError(RuntimeError):
    def __init__(self, detail: str, reason_code: str = "could_not_check") -> None:
        super().__init__(detail)
        self.reason_code = reason_code if reason_code in REASON_CODES else "could_not_check"


def _optional_text(value: Any, limit: int = 500) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _optional_number(value: Any) -> float | int | None:
    # A bool is not a duration or a Unix timestamp.
    return value if type(value) in (int, float) and value >= 0 else None


def _thumbnail(data: dict[str, Any]) -> str | None:
    """Only expose explicit HTTP(S) image locations, never invent a placeholder."""
    from urllib.parse import urlsplit

    candidates = [data.get("thumbnail")]
    thumbnails = data.get("thumbnails")
    if isinstance(thumbnails, list):
        candidates.extend(item.get("url") for item in reversed(thumbnails) if isinstance(item, dict))
    for candidate in candidates:
        if not isinstance(candidate, str) or not 0 < len(candidate) <= 4096:
            continue
        parsed = urlsplit(candidate)
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
            return candidate
    return None


def _media_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "thumbnail": _thumbnail(data),
        "duration": _optional_number(data.get("duration")),
        "timestamp": _optional_number(data.get("timestamp")),
        "release_timestamp": _optional_number(data.get("release_timestamp")),
        "upload_date": _optional_text(data.get("upload_date"), 16),
        "live_status": _optional_text(data.get("live_status"), 32),
        "is_live": data.get("is_live") if type(data.get("is_live")) is bool else None,
    }


def summarize_metadata(data: dict[str, Any], max_entries: int, offset: int = 0) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PreviewError("yt-dlp did not return a media metadata object")
    raw_entries = data.get("entries")
    is_playlist = isinstance(raw_entries, list)
    entries = []
    if is_playlist:
        for index, entry in enumerate(raw_entries[:max_entries], start=offset + 1):
            if not isinstance(entry, dict):
                continue
            entries.append({
                "index": index,
                "id": str(entry.get("id") or "")[:256],
                "title": str(entry.get("title") or entry.get("id") or "Untitled")[:500],
                **_media_fields(entry),
            })
    formats = []
    if not is_playlist:
        for item in (data.get("formats") or [])[:200]:
            if not isinstance(item, dict):
                continue
            formats.append({
                "format_id": str(item.get("format_id") or "")[:128],
                "ext": str(item.get("ext") or "")[:32],
                "resolution": str(item.get("resolution") or "")[:80],
                "vcodec": str(item.get("vcodec") or "")[:100],
                "acodec": str(item.get("acodec") or "")[:100],
                "filesize": item.get("filesize") if type(item.get("filesize")) is int else None,
            })
    count = data.get("playlist_count")
    if type(count) is not int or count < 0:
        count = data.get("n_entries")
    if type(count) is not int or count < 0:
        count = None
    has_more = is_playlist and (
        len(raw_entries) > max_entries or (count is not None and count > offset + len(raw_entries))
    )
    return {
        "title": str(data.get("title") or "Untitled")[:500],
        "id": str(data.get("id") or "")[:256],
        "extractor": str(data.get("extractor_key") or "")[:128],
        "kind": "playlist" if is_playlist else "video",
        "entry_count": count if is_playlist else 0,
        "entries": entries,
        "formats": formats,
        "subtitle_languages": sorted(str(k)[:50] for k in (data.get("subtitles") or {}).keys())[:100],
        **_media_fields(data),
        "offset": offset,
        "max_entries": max_entries,
        "ordering": "source",
        "has_more": bool(has_more),
        "next_offset": offset + len(raw_entries[:max_entries]) if has_more else None,
    }


async def inspect_ytdlp(
    url: str,
    *,
    max_entries: int = 50,
    offset: int = 0,
    executable: str = "yt-dlp",
    timeout_seconds: int = PREVIEW_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not 1 <= max_entries <= 100:
        raise ValueError("max_entries must be between 1 and 100")
    if not 0 <= offset <= 100_000:
        raise ValueError("offset must be between 0 and 100000")
    # The default yt-dlp path uses its Python API in a separate bounded child
    # so typed extractor/network exceptions can be reported as reason codes.
    # Preserve the explicit executable hook for existing CLI callers/tests.
    structured = executable == "yt-dlp"
    argv = (
        [sys.executable, "-m", "app.preview_probe", str(max_entries), str(offset), url]
        if structured else [
            executable, "--ignore-config", "--dump-single-json",
            "--flat-playlist", "--playlist-start", str(offset + 1),
            "--playlist-end", str(offset + max_entries + 1),
            "--skip-download", "--no-warnings", url,
        ]
    )
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if structured else asyncio.subprocess.DEVNULL,
        env=downloader_environment(),
    )
    assert process.stdout is not None

    async def read_bounded() -> bytes:
        deadline = time.monotonic() + timeout_seconds
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PreviewError("Media preview timed out")
            try:
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise PreviewError("Media preview timed out") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PREVIEW_OUTPUT:
                raise PreviewError("Media preview exceeded the output limit")
            chunks.append(chunk)
        return b"".join(chunks)

    try:
        raw = await read_bounded()
        code = await asyncio.wait_for(process.wait(), timeout=3)
        if code != 0:
            reason_code = "could_not_check"
            if structured:
                assert process.stderr is not None
                try:
                    # The child writes only one bounded JSON diagnostic, no raw
                    # URL, HTTP response body, extractor message, or credentials.
                    diagnostic = await asyncio.wait_for(process.stderr.read(512), timeout=1)
                    supplied = json.loads(diagnostic).get("reason_code")
                    if supplied in REASON_CODES:
                        reason_code = supplied
                except (asyncio.TimeoutError, ValueError, AttributeError, TypeError):
                    pass
            raise PreviewError(f"yt-dlp metadata inspection failed (exit {code})", reason_code)
        try:
            metadata = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewError("yt-dlp returned invalid metadata JSON") from exc
        return summarize_metadata(metadata, max_entries, offset)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
