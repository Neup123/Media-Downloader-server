"""Bounded yt-dlp metadata preview; does not download media."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

MAX_PREVIEW_OUTPUT = 2_000_000
PREVIEW_TIMEOUT_SECONDS = 45


class PreviewError(RuntimeError):
    pass


def summarize_metadata(data: dict[str, Any], max_entries: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise PreviewError("yt-dlp did not return a media metadata object")
    raw_entries = data.get("entries")
    is_playlist = isinstance(raw_entries, list)
    entries = []
    if is_playlist:
        for index, entry in enumerate(raw_entries[:max_entries], start=1):
            if not isinstance(entry, dict):
                continue
            entries.append({
                "index": index,
                "id": str(entry.get("id") or "")[:256],
                "title": str(entry.get("title") or entry.get("id") or "Untitled")[:500],
                "duration": entry.get("duration") if isinstance(entry.get("duration"), (int, float)) else None,
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
                "filesize": item.get("filesize") if isinstance(item.get("filesize"), int) else None,
            })
    return {
        "title": str(data.get("title") or "Untitled")[:500],
        "id": str(data.get("id") or "")[:256],
        "extractor": str(data.get("extractor_key") or "")[:128],
        "kind": "playlist" if is_playlist else "video",
        "entry_count": data.get("playlist_count") if isinstance(data.get("playlist_count"), int) else len(entries),
        "entries": entries,
        "formats": formats,
        "subtitle_languages": sorted(str(k)[:50] for k in (data.get("subtitles") or {}).keys())[:100],
    }


async def inspect_ytdlp(
    url: str,
    *,
    max_entries: int = 50,
    executable: str = "yt-dlp",
    timeout_seconds: int = PREVIEW_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not 1 <= max_entries <= 100:
        raise ValueError("max_entries must be between 1 and 100")
    argv = [
        executable, "--ignore-config", "--dump-single-json",
        "--flat-playlist", "--playlist-end", str(max_entries),
        "--skip-download", "--no-warnings", url,
    ]
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
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
            raise PreviewError(f"yt-dlp metadata inspection failed (exit {code})")
        try:
            metadata = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewError("yt-dlp returned invalid metadata JSON") from exc
        return summarize_metadata(metadata, max_entries)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
