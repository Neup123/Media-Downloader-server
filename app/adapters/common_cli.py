"""Reusable subprocess lifecycle for CLI download engines."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.failure_messages import CliFailed, LastErrorLine, UserFacingFailure
from app.adapters.process_env import downloader_environment
from app.redaction import _redact

Callback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]

EXCLUDED_SUFFIXES = (".part", ".download", ".ytdl", ".aria2", ".tmp")


def _output_files(workdir: Path) -> list[Path]:
    root = workdir.resolve()
    result = []
    for path in workdir.rglob("*"):
        if not path.is_file() or path.name.endswith(EXCLUDED_SUFFIXES):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.is_symlink():
            continue
        if Path(str(path) + ".aria2").exists():
            continue
        result.append(resolved)
    return sorted(result)


async def run_cli(
    *,
    name: str,
    argv: list[str],
    workdir: Path,
    on_progress: Callback,
    on_log: LogCallback,
    is_cancelled: CancelCallback,
) -> list[Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    await on_log(f"Starting {name}")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=downloader_environment(),
        )
    except FileNotFoundError as exc:
        raise UserFacingFailure(f"{name} is not installed on the server.",
                                "Contact the administrator / reinstall the engine.") from exc
    errors = LastErrorLine()
    assert process.stdout is not None
    previous_size = 0
    try:
        while True:
            if await is_cancelled():
                raise asyncio.CancelledError
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = None
            if raw == b"":
                break
            if raw:
                line = _redact(raw.decode("utf-8", errors="replace").rstrip())
                errors.capture(line)
                await on_log(line)
            files = _output_files(workdir)
            downloaded = sum(path.stat().st_size for path in files if path.exists())
            await on_progress({
                "state": "downloading",
                "downloaded_bytes": downloaded,
                "total_bytes": None, "percent": None,
                "speed_bytes_per_second": max(0, downloaded - previous_size),
                "eta_seconds": None, "filename": files[-1].name if files else None,
            })
            previous_size = downloaded
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    code = await process.wait()
    if code != 0:
        raise CliFailed(name, code, errors.value)
    files = _output_files(workdir)
    if not files:
        raise RuntimeError(f"{name} completed without any output file")
    return files
