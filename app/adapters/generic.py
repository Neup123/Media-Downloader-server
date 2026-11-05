"""Generic direct HTTP(S) engine backed by the existing curl CLI.

CLI curl is used rather than reimplementing a downloader protocol. The service
manages the output path and reports observed bytes without fabricating totals.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlsplit

from app.adapters.process_env import downloader_environment
from app.security import UnsafeUrlError, resolve_http_target

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")


def effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    settings = {
        "filename": "",
        "follow_redirects": False,
        "max_redirects": 5,
        "retry_count": 2,
        "user_agent": "",
    }
    allowed = frozenset(settings)
    for raw_source in (global_settings, overrides):
        if raw_source is not None and not isinstance(raw_source, dict):
            raise ValueError("Generic engine settings must be objects")
        source = raw_source if raw_source is not None else {}
        unknown = set(source) - allowed
        if unknown:
            raise ValueError(f"Unknown generic setting(s): {', '.join(sorted(unknown))}")
        settings.update(source)
    name = settings["filename"]
    if not isinstance(name, str) or (name and (
        not _SAFE_FILENAME.fullmatch(name) or name in {".", ".."} or name.startswith(".")
    )):
        raise ValueError("filename must be a simple file name without directories")
    for key, max_value in (("max_redirects", 10), ("retry_count", 10)):
        value = settings[key]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= max_value:
            raise ValueError(f"{key} must be an integer from 0 to {max_value}")
    if not isinstance(settings["follow_redirects"], bool):
        raise ValueError("follow_redirects must be a boolean")
    if not isinstance(settings["user_agent"], str) or len(settings["user_agent"]) > 400 or "\r" in settings["user_agent"] or "\n" in settings["user_agent"]:
        raise ValueError("Invalid user_agent")
    return settings


def output_filename(url: str, preferred: str) -> str:
    if preferred:
        return preferred
    parsed = urlsplit(url)
    candidate = unquote(parsed.path.rsplit("/", 1)[-1])
    candidate = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate)[:180]
    if not candidate or candidate in {".", ".."} or candidate.startswith("."):
        return "download.bin"
    return candidate


async def run_generic(
    *,
    executable: str, url: str, workdir: Path,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: ProgressCallback, on_log: LogCallback,
    is_cancelled: CancelCallback,
    allow_private_targets: bool = False,
) -> list[Path]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Generic direct downloads require HTTP(S) URLs")
    options = effective_settings(global_settings, overrides)
    if options["follow_redirects"] and not allow_private_targets:
        raise UnsafeUrlError(
            "Generic direct-download redirects are disabled without a trusted network egress policy"
        )
    # Pin the initial TCP destination to the validated DNS answers.
    # This prevents subsequent DNS rebinding in curl, but is not a network
    # firewall and does not constrain separate downloader engines.
    pin = await asyncio.to_thread(resolve_http_target, url, allow_private_targets)
    workdir.mkdir(parents=True, exist_ok=True)
    filename = output_filename(url, options["filename"])
    destination = workdir / filename
    argv = [
        executable, "--disable", "--fail", "--silent", "--show-error",
        "--globoff", "--proto", "=http,https", "--proto-redir", "=http,https",
        "--connect-timeout", "15", "--max-time", "3600",
        "--retry", str(options["retry_count"]),
        "--noproxy", "*", "--proxy", "",
        "--output", str(destination),
    ]
    if pin:
        argv.extend(["--resolve", pin])
    if options["follow_redirects"]:
        argv.extend(["--location", "--max-redirs", str(options["max_redirects"])])
    if options["user_agent"]:
        argv.extend(["--user-agent", options["user_agent"]])
    argv.append(url)

    await on_log("Starting curl direct download")
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=downloader_environment(),
    )
    previous_bytes = 0
    assert process.stdout is not None
    try:
        while process.returncode is None:
            if await is_cancelled():
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                raise asyncio.CancelledError
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            downloaded = destination.stat().st_size if destination.is_file() else 0
            await on_progress({
                "state": "downloading", "downloaded_bytes": downloaded,
                "total_bytes": None, "percent": None,
                "speed_bytes_per_second": max(0, downloaded - previous_bytes),
                "eta_seconds": None, "filename": filename,
            })
            previous_bytes = downloaded
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    output = (await process.stdout.read()).decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(f"curl failed with status {process.returncode}: {output[:500]}")
    if not destination.is_file():
        raise RuntimeError("curl completed without producing a file")
    if output:
        await on_log(output[:500])
    return [destination]
