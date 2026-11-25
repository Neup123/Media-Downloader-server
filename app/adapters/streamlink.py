"""Streamlink recording adapter (not registered for public job submission yet).

Uses pinned Streamlink's native recording CLI. The existing shared CLI runner
owns process cancellation and graceful stop; no second subprocess state machine.
Cookie/identity resolution and durable Stop action are separate integration work.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.adapters.common_cli import run_cli
from app.adapters.process_env import downloader_environment
from app.discovery import read_bounded_preview, terminate_preview_process

QUALITY = re.compile(r"(?:best|worst|[1-9][0-9]{1,3}p(?:[1-9][0-9]?)?)(?:,(?:best|worst|[1-9][0-9]{1,3}p(?:[1-9][0-9]?)?)){0,4}\Z")
RECORDING_NAME = "recording.media"
FORMAT_SUFFIXES = {"mpegts": ".ts", "matroska": ".mkv", "webm": ".mkv", "mp4": ".mp4", "mov": ".mp4"}


def streamlink_command(executable: str, url: str, quality: str, output: Path) -> list[str]:
    if not isinstance(quality, str) or not QUALITY.fullmatch(quality):
        raise ValueError("Invalid Streamlink quality; choose a resolution or best/worst")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Streamlink requires a validated HTTP(S) source")
    # Direct HLS protocol is used by the local deterministic adapter fixture.
    # Page/source validation and engine routing remain the caller's responsibility.
    source = "hls://" + url if parsed.path.lower().endswith(".m3u8") else url
    return [executable, "--no-config", "--no-plugin-cache", "--no-plugin-sideloading",
            "--auto-version-check", "no", "--webbrowser", "no", "--http-ignore-env",
            "--quiet", "--progress", "no", "--ffmpeg-fout", "mpegts",
            "--skip", "--output", str(output), "--", source, quality]


async def validate_recording(path: Path) -> Path:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise RuntimeError("Recording contains no media")
    # Read only a small local interval and never enable network protocols.
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-read_intervals", "%+1", "-count_packets", "-show_entries",
        "format=format_name:stream=codec_type,nb_read_packets", "-of", "json", str(path),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=downloader_environment(),
    )
    try:
        raw = await read_bounded_preview(process, 20.0)
        if await asyncio.wait_for(process.wait(), 3.0) != 0:
            raise RuntimeError("Recording could not be parsed")
        data = json.loads(raw)
        formats = data.get("format", {}).get("format_name", "").split(",")
        suffix = next((FORMAT_SUFFIXES[name] for name in formats if name in FORMAT_SUFFIXES), None)
        if not suffix or not any(
            stream.get("codec_type") in {"audio", "video"}
            and str(stream.get("nb_read_packets", "")).isdigit()
            and int(stream["nb_read_packets"]) > 0
            for stream in data.get("streams", [])
        ):
            raise RuntimeError("Recording has no supported playable media packets")
    finally:
        await terminate_preview_process(process)
    result = path.with_suffix(suffix)
    if result.exists():
        raise RuntimeError("Recording output already exists")
    path.rename(result)
    return result


async def run_streamlink(
    *, executable: str, url: str, workdir: Path, quality: str,
    on_progress, on_log, is_cancelled, is_stopped,
) -> list[Path]:
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    output = workdir / RECORDING_NAME
    if output.exists():
        raise RuntimeError("Recording requires a fresh workspace")
    argv = streamlink_command(executable, url, quality, output)
    files = await run_cli(
        name="streamlink", argv=argv, workdir=workdir, on_progress=on_progress,
        on_log=on_log, is_cancelled=is_cancelled, is_stopped=is_stopped,
    )
    if files != [output]:
        raise RuntimeError("Unexpected recording output; no files will be delivered")
    if await is_cancelled():
        raise asyncio.CancelledError
    return [await validate_recording(output)]
