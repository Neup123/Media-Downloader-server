from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.security import validate_extra_args

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]


PROGRESS_TEMPLATE = (
    "download:__DL_PROGRESS__"
    "%(progress.downloaded_bytes)s\t"
    "%(progress.total_bytes)s\t"
    "%(progress.total_bytes_estimate)s\t"
    "%(progress.speed)s\t"
    "%(progress.eta)s\t"
    "%(info.filename)s"
)


def _number(value: str) -> float | None:
    if value in {"NA", "None", "", "null"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_progress_line(line: str) -> dict[str, Any] | None:
    marker = "__DL_PROGRESS__"
    if marker not in line:
        return None
    raw = line.split(marker, 1)[1]
    parts = raw.split("\t", 5)
    if len(parts) < 5:
        return None
    downloaded = _number(parts[0])
    total = _number(parts[1]) or _number(parts[2])
    speed = _number(parts[3])
    eta = _number(parts[4])
    filename = parts[5] if len(parts) > 5 else None
    percent = (downloaded / total * 100) if downloaded is not None and total and total > 0 else None
    return {
        "state": "downloading",
        "downloaded_bytes": int(downloaded) if downloaded is not None else None,
        "total_bytes": int(total) if total is not None else None,
        "percent": round(min(100.0, max(0.0, percent)), 2) if percent is not None else None,
        "speed_bytes_per_second": int(speed) if speed is not None else None,
        "eta_seconds": int(eta) if eta is not None else None,
        "filename": filename,
    }


def _effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = {
        "format": "bv*+ba/b",
        "subtitles_lang": "",
        "write_subtitles": False,
        "embed_subtitles": False,
        "extra_args": [],
        "allow_playlist": False,
        "playlist_items": "",
    }
    result.update(global_settings or {})
    result.update(overrides or {})
    fmt = result.get("format", "")
    if not isinstance(fmt, str) or len(fmt) > 256 or "\x00" in fmt:
        raise ValueError("Invalid yt-dlp format selector")
    lang = result.get("subtitles_lang", "")
    if not isinstance(lang, str) or len(lang) > 100 or "\x00" in lang:
        raise ValueError("Invalid subtitles language")
    result["extra_args"] = validate_extra_args(result.get("extra_args") or [])
    playlist_items = result.get("playlist_items", "")
    if not isinstance(result.get("allow_playlist"), bool):
        raise ValueError("allow_playlist must be a boolean")
    if not isinstance(playlist_items, str) or len(playlist_items) > 512:
        raise ValueError("Invalid playlist items")
    if playlist_items and not __import__("re").fullmatch(r"[1-9]\d{0,3}(?:-[1-9]\d{0,3})?(?:,[1-9]\d{0,3}(?:-[1-9]\d{0,3})?)*", playlist_items):
        raise ValueError("playlist_items must be comma-separated indices or index ranges")
    if playlist_items and not result["allow_playlist"]:
        raise ValueError("playlist_items requires allow_playlist=true")
    return result


async def run_ytdlp(
    *,
    executable: str,
    url: str,
    workdir: Path,
    global_settings: dict[str, Any],
    overrides: dict[str, Any],
    cookie_dir: Path,
    on_progress: ProgressCallback,
    on_log: LogCallback,
    is_cancelled: CancelCallback,
) -> list[Path]:
    effective = _effective_settings(global_settings, overrides)
    workdir.mkdir(parents=True, exist_ok=True)
    argv = [
        executable,
        "--ignore-config",
        "--yes-playlist" if effective["allow_playlist"] else "--no-playlist",
        "--newline",
        "--progress-template",
        PROGRESS_TEMPLATE,
        "-P",
        str(workdir),
        "-o",
        "%(title).200s-%(id)s.%(ext)s",
        "--print",
        "after_move:__DL_FILE__%(filepath)s",
    ]
    if effective["playlist_items"]:
        argv.extend(["--playlist-items", effective["playlist_items"]])
    if effective["format"]:
        argv.extend(["-f", effective["format"]])
    if effective["write_subtitles"]:
        argv.append("--write-subs")
    if effective["embed_subtitles"]:
        argv.append("--embed-subs")
    if effective["subtitles_lang"]:
        argv.extend(["--sub-langs", effective["subtitles_lang"]])
    cookie_name = overrides.get("cookies_file") or global_settings.get("cookies_file")
    if cookie_name:
        if not isinstance(cookie_name, str) or Path(cookie_name).name != cookie_name or cookie_name in {".", ".."}:
            raise ValueError("cookies_file must be a filename inside the service cookies directory")
        cookie_path = (cookie_dir / cookie_name).resolve()
        if cookie_path.parent != cookie_dir.resolve() or not cookie_path.is_file():
            raise ValueError("The selected cookies file does not exist in the service cookies directory")
        argv.extend(["--cookies", str(cookie_path)])
    argv.extend(effective["extra_args"])
    argv.append(url)

    await on_log("Starting yt-dlp")
    await on_log("Started yt-dlp with service-managed output and progress options")
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    reported_files: list[Path] = []
    assert process.stdout is not None
    try:
        while True:
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=0.8)
            except asyncio.TimeoutError:
                if await is_cancelled():
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                    raise asyncio.CancelledError
                if process.returncode is not None:
                    break
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            if line.startswith("__DL_FILE__"):
                candidate = Path(line[len("__DL_FILE__"):]).expanduser().resolve()
                try:
                    candidate.relative_to(workdir.resolve())
                    if candidate.is_file():
                        reported_files.append(candidate)
                except ValueError:
                    await on_log("Ignored an output path outside the job directory")
                continue
            progress = parse_progress_line(line)
            if progress is not None:
                await on_progress(progress)
            else:
                await on_log(_redact(line))
            if await is_cancelled():
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                raise asyncio.CancelledError
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(f"yt-dlp exited with status {return_code}")

    files = sorted(set(reported_files))
    if not files:
        files = [p for p in workdir.rglob("*") if p.is_file() and not p.name.endswith((".part", ".ytdl", ".json"))]
    if not files:
        raise RuntimeError("yt-dlp exited successfully but no output file was found")
    return files


def _redact(line: str) -> str:
    import re
    line = re.sub(r"(?i)(authorization|cookie|set-cookie)(\s*[:=]\s*)[^\s]+", r"\1\2[REDACTED]", line)
    line = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", line)
    line = re.sub(r"([?&](?:token|access_token|api_key|key|secret|auth|password|signature|sig|session|cookie)=)[^&#\s]+", r"\1[REDACTED]", line, flags=re.IGNORECASE)
    return line[:4000]
