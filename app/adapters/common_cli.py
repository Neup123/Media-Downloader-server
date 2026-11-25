"""Reusable subprocess lifecycle for CLI download engines."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.adapters.process_env import downloader_environment
from app.redaction import _redact
from app.engine_failures import EngineFailure, EngineFailureKind, classify_cli_error

Callback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]

GRACEFUL_STOP_SECONDS = 10.0
# A requested SIGINT is handled by Streamlink and reported as exit 130.
# A negative signal exit, timeout, or arbitrary nonzero exit is not success.
GRACEFUL_STOP_CODES = frozenset({0, 130})

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


async def _drain_and_wait(process: asyncio.subprocess.Process, timeout: float) -> None:
    async def drain():
        if process.stdout is not None:
            while await process.stdout.read(65536):
                pass
        await process.wait()
    await asyncio.wait_for(drain(), timeout)


async def _terminate_cli(process: asyncio.subprocess.Process, isolated: bool) -> None:
    # On Linux the optional recording path owns a process group, including
    # muxer children. Ordinary CLI adapters retain their existing lifecycle.
    def terminate(sig):
        try:
            if isolated:
                os.killpg(process.pid, sig)
            elif process.returncode is None:
                process.send_signal(sig)
        except ProcessLookupError:
            pass

    terminate(signal.SIGTERM)
    try:
        await _drain_and_wait(process, 5.0)
    except asyncio.TimeoutError:
        terminate(signal.SIGKILL)
        await _drain_and_wait(process, 5.0)


async def run_cli(
    *,
    name: str,
    argv: list[str],
    workdir: Path,
    on_progress: Callback,
    on_log: LogCallback,
    is_cancelled: CancelCallback,
    is_stopped: CancelCallback | None = None,
) -> list[Path]:
    """Run a CLI and return files only on success.

    Local recording lifecycle: running -> stopping (one requested SIGINT) ->
    normal/handled exit; cancellation wins from either state; timeout or any
    other exit fails. No persisted job status or progress vocabulary changes.
    The recording adapter must independently validate the returned media.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    await on_log(f"Starting {name}")
    isolated = is_stopped is not None and os.name == 'posix'
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=str(workdir), stdin=asyncio.subprocess.DEVNULL,
            start_new_session=isolated,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=downloader_environment(),
        )
    except FileNotFoundError as exc:
        raise EngineFailure(EngineFailureKind.MISSING_BINARY, "Download executable not found") from exc
    assert process.stdout is not None
    previous_size = 0
    failure_kind = EngineFailureKind.UNKNOWN
    stop_deadline = None
    loop = asyncio.get_running_loop()
    try:
        while True:
            if await is_cancelled():
                raise asyncio.CancelledError
            if is_stopped is not None and stop_deadline is None and await is_stopped():
                if process.returncode is None:
                    try:
                        process.send_signal(signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    else:
                        stop_deadline = loop.time() + GRACEFUL_STOP_SECONDS
                        await on_log("Stopping recording and finalizing media")
            if stop_deadline is not None and loop.time() >= stop_deadline:
                raise RuntimeError(f"{name} did not stop gracefully")
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = None
            if raw == b"":
                break
            if raw:
                line = raw.decode("utf-8", errors="replace").rstrip()
                category = classify_cli_error(line)
                if category is not None:
                    failure_kind = category
                await on_log(_redact(line))
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
        if stop_deadline is not None:
            code = await asyncio.wait_for(process.wait(), max(0.01, stop_deadline - loop.time()))
        else:
            code = await process.wait()
        if await is_cancelled():
            raise asyncio.CancelledError
    except BaseException:
        if process.returncode is None or isolated:
            await _terminate_cli(process, isolated)
        raise
    if code != 0 and not (stop_deadline is not None and code in GRACEFUL_STOP_CODES):
        raise EngineFailure(failure_kind, f"{name} exited with status {code}")
    files = _output_files(workdir)
    if not files:
        raise EngineFailure(EngineFailureKind.NO_OUTPUT, f"{name} completed without any output file")
    return files
