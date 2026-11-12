from __future__ import annotations

import asyncio
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.adapters.process_env import downloader_environment
from app.cookies import materialize_cookie_file

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]

# gallery-dl's terminal progress line reports percent, bytes downloaded, and
# current rate. It does not provide a reliable total size in this format.
_PROGRESS_RE = re.compile(
    r"^\s*(?:\[gallery-dl\]\s*)?"
    r"(?P<percent>\d+(?:\.\d+)?)%\s+"
    r"(?P<downloaded>\d+(?:\.\d+)?)\s*(?P<download_unit>[kMGT]?B)\s+"
    r"(?P<speed>\d+(?:\.\d+)?)\s*(?P<speed_unit>[kMGT]?B)/s",
    re.IGNORECASE,
)

_SERVICE_MANAGED_OPTIONS = {
    "output.directory",
    "output.base-directory",
    "output.mode",
    "output.shorten",
    "extractor.base-directory",
}


def _unit_bytes(value: str, unit: str) -> int:
    multipliers = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
    }
    return int(float(value) * multipliers[unit.upper()])


def parse_progress_line(line: str) -> dict[str, Any] | None:
    match = _PROGRESS_RE.match(line)
    if not match:
        return None
    downloaded = _unit_bytes(match.group("downloaded"), match.group("download_unit"))
    speed = _unit_bytes(match.group("speed"), match.group("speed_unit"))
    percent = float(match.group("percent"))
    return {
        "state": "downloading",
        "downloaded_bytes": downloaded,
        "total_bytes": None,
        "percent": round(min(100.0, max(0.0, percent)), 2),
        "speed_bytes_per_second": speed,
        "eta_seconds": None,
        "filename": None,
    }


def _effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "options": {},
        "cookies_file": "",
        "proxy": "",
    }
    for source in (global_settings or {}, overrides or {}):
        for key, value in source.items():
            if key == "options":
                current = dict(result["options"])
                current.update(value or {})
                result["options"] = current
            else:
                result[key] = value

    options = result["options"]
    if not isinstance(options, dict):
        raise ValueError("gallery-dl options must be an object")
    for key, value in options.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", key):
            raise ValueError(f"Invalid gallery-dl option name: {key!r}")
        if key in _SERVICE_MANAGED_OPTIONS:
            raise ValueError(f"gallery-dl option {key} is managed by the service")
        if isinstance(value, (dict, list)) or value is None:
            raise ValueError(f"gallery-dl option {key} must have a scalar value")
        if len(str(value)) > 2048 or "\x00" in str(value):
            raise ValueError(f"gallery-dl option value is too long or invalid: {key}")
        if key.rsplit(".", 1)[-1] in {"filename", "directory"} and any(
            marker in str(value) for marker in ("..", "/", "\\")
        ):
            raise ValueError("gallery-dl output templates cannot escape the service working directory")

    for field in ("cookies_file", "proxy"):
        value = result.get(field, "")
        if not isinstance(value, str) or len(value) > 2048 or "\x00" in value:
            raise ValueError(f"Invalid gallery-dl {field}")
    return result


async def run_gallery_dl(
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
    encryption_key: str | None = None,
) -> list[Path]:
    effective = _effective_settings(global_settings, overrides)
    workdir.mkdir(parents=True, exist_ok=True)

    argv = [
        executable,
        "--config-ignore",
        "--no-input",
        "--no-colors",
        "-d",
        str(workdir),
        "-o",
        "output.mode=terminal",
        "-o",
        "output.shorten=false",
    ]

    if effective["proxy"]:
        argv.extend(["--proxy", effective["proxy"]])

    cookie_name = effective.get("cookies_file") or ""
    cookie_source: Path | None = None
    if cookie_name:
        if Path(cookie_name).name != cookie_name or cookie_name in {".", ".."} or "\\" in cookie_name:
            raise ValueError("gallery-dl cookies_file must be a filename inside the service cookies directory")
        cookie_path = (cookie_dir / cookie_name).resolve()
        if cookie_path.parent != cookie_dir.resolve() or not cookie_path.is_file():
            raise ValueError("The selected cookies file does not exist in the service cookies directory")
        cookie_source = cookie_path

    context = materialize_cookie_file(cookie_source, workdir, encryption_key) if cookie_source else nullcontext(None)
    with context as decrypted:
        if decrypted is not None:
            argv.extend(["--cookies", str(decrypted)])
        for key, value in effective["options"].items():
            argv.extend(["-o", f"{key}={value}"])
        argv.append(url)
        return await _execute_gallery_dl(argv, workdir, on_progress, on_log, is_cancelled)


async def _execute_gallery_dl(
    argv: list[str], workdir: Path,
    on_progress: ProgressCallback, on_log: LogCallback, is_cancelled: CancelCallback,
) -> list[Path]:
    await on_log("Starting gallery-dl")
    await on_log("Started gallery-dl with service-managed output and progress options")

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=downloader_environment(),
    )
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
    except BaseException:
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
        raise RuntimeError(f"gallery-dl exited with status {return_code}")

    ignored_suffixes = (".part", ".tmp", ".ytdl", ".aria2")
    files = sorted(
        path for path in workdir.rglob("*")
        if path.is_file()
        and not path.name.endswith(ignored_suffixes)
        and not Path(str(path) + ".aria2").exists()
    )
    if not files:
        raise RuntimeError("gallery-dl exited successfully but no output file was found")
    return files


def _redact(line: str) -> str:
    line = re.sub(
        r"(?i)(authorization|cookie|set-cookie|password|token|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        line,
    )
    line = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", line)
    line = re.sub(
        r"([?&](?:token|access_token|api_key|key|secret|auth|password|signature|sig|session|cookie)=)[^&#\s]+",
        r"\1[REDACTED]",
        line,
        flags=re.IGNORECASE,
    )
    return line[:4000]
