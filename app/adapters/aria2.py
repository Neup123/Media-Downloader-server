from __future__ import annotations

import asyncio
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.adapters.process_env import downloader_environment

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
CancelCallback = Callable[[], Awaitable[bool]]

ARIA2_OPTION_ALLOWLIST = {
    "max-connection-per-server", "split", "min-split-size", "max-tries", "retry-wait",
    "connect-timeout", "timeout", "lowest-speed-limit", "max-download-limit",
    "user-agent", "referer", "header", "allow-overwrite", "auto-file-renaming",
    "check-integrity", "seed-time", "seed-ratio", "bt-max-peers", "bt-stop-timeout",
    "select-file",
}


def _effective_options(global_options: dict[str, Any], overrides: dict[str, Any]) -> dict[str, str]:
    values = {"max-connection-per-server": "4", "split": "4", "continue": "true", "max-tries": "5", "retry-wait": "5"}
    for source in (global_options or {}, overrides or {}):
        options = source.get("options", {}) if isinstance(source, dict) else {}
        if not isinstance(options, dict):
            raise ValueError("aria2 options must be an object")
        for key, value in options.items():
            if key not in ARIA2_OPTION_ALLOWLIST:
                raise ValueError(f"aria2 option is not allowed through the settings API: {key}")
            text = str(value)
            if len(text) > 1024 or "\x00" in text:
                raise ValueError(f"Invalid value for aria2 option {key}")
            values[key] = text
    # Core policy: a job override cannot silently re-enable seeding.
    values["seed-time"] = "0"
    return values


class Aria2Rpc:
    """Lifecycle-managed, loopback-only aria2 JSON-RPC daemon."""

    def __init__(self, data_dir: Path, port: int, executable: str = "aria2c") -> None:
        self.data_dir = data_dir
        self.port = port
        self.executable = executable
        self.secret = secrets.token_urlsafe(32)
        self.process: subprocess.Popen | None = None
        self.client = httpx.AsyncClient(timeout=5.0, trust_env=False)
        self.endpoint = f"http://127.0.0.1:{port}/jsonrpc"
        self._counter = 0
        self._job_gids: dict[str, str] = {}
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._start_lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        if self.process and self.process.poll() is None:
            # Process existence is not proof of a live authenticated RPC
            # channel; fail closed instead of silently accepting a hung daemon.
            try:
                await self.call("aria2.getGlobalOption", [])
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                raise RuntimeError("aria2 process is running but JSON-RPC is unavailable") from exc
            return
        args = [
            self.executable,
            "--enable-rpc=true",
            "--rpc-listen-all=false",
            "--rpc-listen-port", str(self.port),
            "--rpc-secret", self.secret,
            "--rpc-allow-origin-all=false",
            "--dir", str(self.data_dir / "jobs"),
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=false",
            "--check-integrity=true",
            "--seed-time=0",
            "--rpc-save-upload-metadata=false",
            "--max-concurrent-downloads=8",
            "--console-log-level=warn",
            "--summary-interval=0",
        ]
        self.process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, env=downloader_environment(),
        )
        for _ in range(40):
            if self.process.poll() is not None:
                raise RuntimeError("aria2c exited during startup; check that aria2c is installed and the RPC port is available")
            try:
                await self.call("aria2.getGlobalOption", [])
                return
            except (httpx.HTTPError, RuntimeError, ValueError):
                await asyncio.sleep(0.25)
        self.stop()
        raise RuntimeError("aria2 JSON-RPC did not become ready on 127.0.0.1")

    async def call(self, method: str, params: list[Any], include_token: bool = True) -> Any:
        self._counter += 1
        actual = ([f"token:{self.secret}"] + params) if include_token else params
        response = await self.client.post(self.endpoint, json={
            "jsonrpc": "2.0", "id": f"job-{self._counter}", "method": method, "params": actual,
        })
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            error = payload["error"]
            raise RuntimeError(f"aria2 RPC {method} failed ({error.get('code')}): {error.get('message', 'unknown error')}")
        return payload.get("result")

    async def inspect_torrent(
        self, *, url: str | None = None, torrent_base64: str | None = None,
        timeout_seconds: float = 45,
    ) -> dict[str, Any]:
        """Read an aria2 torrent manifest with no payload transfer or persisted job.

        Magnets fetch only peer metadata; uploaded .torrent bytes are added paused.
        pause-metadata prevents subsequent payload transfers, even if aria2
        creates a follow-on GID from a metadata download.
        """
        if (url is None) == (torrent_base64 is None):
            raise ValueError("Exactly one torrent source is required")
        await self.start()
        tracked: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="torrent-preview-", dir=self.data_dir) as temporary:
            root = Path(temporary).resolve()
            options = {
                "dir": str(root),
                "seed-time": "0",
                "pause-metadata": "true",
                "bt-save-metadata": "false",
            }
            try:
                if torrent_base64 is not None:
                    # A decoded .torrent already contains its complete manifest.
                    # It must never enter the active payload queue.
                    options["pause"] = "true"
                    gid = await self.call("aria2.addTorrent", [torrent_base64, [], options])
                else:
                    if not url or not url.startswith("magnet:?"):
                        raise ValueError("Manifest URL must be a magnet link")
                    # Metadata-only stops after fetching the metadata and never
                    # creates the follow-on GID that exposes the file list.
                    # pause-metadata keeps that follow-on paused instead.
                    gid = await self.call("aria2.addUri", [[url], options])
                tracked.add(str(gid))
                deadline = asyncio.get_running_loop().time() + timeout_seconds
                while asyncio.get_running_loop().time() < deadline:
                    for current_gid in tuple(tracked):
                        status = await self.call("aria2.tellStatus", [current_gid, [
                            "status", "files", "bittorrent", "followedBy", "errorCode", "errorMessage",
                        ]])
                        tracked.update(str(child) for child in status.get("followedBy") or [])
                        if status.get("status") == "error":
                            raise RuntimeError("aria2 could not retrieve torrent metadata")
                        files = status.get("files") or []
                        # The initial magnet GID can expose a synthetic metadata
                        # file before its paused payload GID becomes available.
                        if url is not None and current_gid == str(gid):
                            if status.get("followedBy") or any(
                                str(item.get("path", "")).startswith("[METADATA]") for item in files
                            ):
                                continue
                        if status.get("bittorrent") and files:
                            # Follow-on GIDs may use the daemon's global dir instead
                            # of inheriting the initial magnet's preview directory.
                            fallback_root = self.data_dir / "jobs" if current_gid != str(gid) else None
                            entries = _manifest_entries(files, root, fallback_root=fallback_root)
                            return {"files": entries, "file_count": len(entries)}
                    await asyncio.sleep(0.25)
                raise TimeoutError("Torrent metadata was not available before the deadline")
            finally:
                # Include all spawned metadata/follow-on GIDs. Force removal is
                # safe even when a GID has already completed or been removed.
                for current_gid in tuple(tracked):
                    try:
                        await self.call("aria2.forceRemove", [current_gid])
                    except (RuntimeError, httpx.HTTPError):
                        pass
                    try:
                        await self.call("aria2.removeDownloadResult", [current_gid])
                    except (RuntimeError, httpx.HTTPError):
                        pass

    def can_control(self, job_id: str) -> bool:
        return job_id in self._job_gids

    async def pause(self, job_id: str) -> None:
        gid = self._job_gids.get(job_id)
        if not gid:
            raise RuntimeError("This job has no active aria2 transfer to pause")
        await self.call("aria2.pause", [gid])

    async def resume(self, job_id: str) -> None:
        gid = self._job_gids.get(job_id)
        if not gid:
            raise RuntimeError("This job has no paused aria2 transfer to resume")
        await self.call("aria2.unpause", [gid])

    async def download(
        self,
        *,
        url: str,
        job_id: str,
        workdir: Path,
        global_settings: dict[str, Any],
        overrides: dict[str, Any],
        on_progress: ProgressCallback,
        on_log: LogCallback,
        is_cancelled: CancelCallback,
    ) -> list[Path]:
        await self.start()
        workdir.mkdir(parents=True, exist_ok=True)
        root = workdir.resolve()
        options = _effective_options(global_settings, overrides)
        options["dir"] = str(root)
        options["check-integrity"] = "true"
        source = url.split("?", 1)[0].lower()
        source_is_container = (
            url.lower().startswith("magnet:?") or
            source.endswith((".torrent", ".metalink", ".meta4"))
        )
        if source_is_container:
            options["pause-metadata"] = "true"
        gid = str(await self.call("aria2.addUri", [[url], options]))
        tracked = {gid}
        active = gid
        followon_deadline: float | None = None
        succeeded = False
        self._job_gids[job_id] = gid
        status_keys = [
            "status", "totalLength", "completedLength", "downloadSpeed",
            "errorCode", "errorMessage", "files", "followedBy", "bittorrent",
        ]
        try:
            await on_log(f"Queued with aria2 JSON-RPC (GID {gid})")
            while True:
                if await is_cancelled():
                    raise asyncio.CancelledError
                try:
                    status = await self.call("aria2.tellStatus", [active, status_keys])
                except RuntimeError:
                    # aria2 may publish followedBy before the new GID is queryable.
                    if active != gid and followon_deadline is not None and (
                        asyncio.get_running_loop().time() < followon_deadline
                    ):
                        await asyncio.sleep(0.25)
                        continue
                    raise
                children = [str(child) for child in status.get("followedBy") or []]
                tracked.update(children)
                current = status.get("status")
                files = status.get("files") or []
                metadata_file = any(
                    Path(str(item.get("path") or "")).name.startswith("[METADATA]")
                    for item in files
                )
                # Parent GIDs for magnets, remote torrents and Metalinks can
                # finish before their payload GID is available in aria2.
                metadata_phase = active == gid and (metadata_file or children or source_is_container)
                parent_complete = metadata_phase and current == "complete"
                if children:
                    # Follow every chain, not just the first metadata successor.
                    child = children[0]
                    if child != active:
                        active = child
                        followon_deadline = asyncio.get_running_loop().time() + 10.0
                        # Do not advertise a child for pause/resume until its
                        # destination has been verified.
                        await asyncio.sleep(0.25)
                        continue
                if current == "error":
                    raise RuntimeError(f"aria2 download failed ({status.get('errorCode')}): {status.get('errorMessage')}")
                if current == "removed":
                    raise RuntimeError("aria2 download was removed")
                if metadata_phase:
                    if parent_complete and followon_deadline is None:
                        followon_deadline = asyncio.get_running_loop().time() + 10.0
                    if parent_complete and asyncio.get_running_loop().time() >= followon_deadline:
                        raise RuntimeError("aria2 completed metadata without a payload GID")
                    await on_progress({
                        "state": "downloading", "engine_status": "active",
                        "downloaded_bytes": None, "total_bytes": None,
                        "percent": None, "speed_bytes_per_second": None,
                        "eta_seconds": None, "filename": None,
                    })
                    await asyncio.sleep(0.25)
                    continue
                if active != gid and self._job_gids.get(job_id) != active:
                    # Some aria2 follow-ons silently fall back to --dir. Do not
                    # deliver or keep downloading files outside this job's root.
                    child_options = await self.call("aria2.getOption", [active])
                    child_dir = Path(str(child_options.get("dir") or "")).resolve()
                    if child_dir != root:
                        await self.call("aria2.changeOption", [active, {"dir": str(root)}])
                        child_options = await self.call("aria2.getOption", [active])
                        if Path(str(child_options.get("dir") or "")).resolve() != root:
                            raise RuntimeError("aria2 follow-on transfer directory is outside the job directory")
                    self._job_gids[job_id] = active
                    if source_is_container and current == "paused":
                        await self.call("aria2.unpause", [active])
                        await asyncio.sleep(0.25)
                        continue
                total = _int_or_none(status.get("totalLength"))
                completed = _int_or_none(status.get("completedLength"))
                speed = _int_or_none(status.get("downloadSpeed"))
                percent = round(completed / total * 100, 2) if total and completed is not None else None
                selected = [item for item in files if str(item.get("selected", "true")).lower() != "false"]
                filename = next((Path(str(item.get("path") or "")).name for item in selected
                                 if item.get("path") and not Path(str(item["path"])).name.startswith("[METADATA]")), None)
                await on_progress({
                    "state": "paused" if current == "paused" else "downloading",
                    "engine_status": current,
                    "downloaded_bytes": completed,
                    "total_bytes": total,
                    "percent": percent,
                    "speed_bytes_per_second": speed,
                    "eta_seconds": int((total - completed) / speed) if total and completed is not None and speed and speed > 0 else None,
                    "filename": filename,
                })
                if current == "complete":
                    result = []
                    for item in selected:
                        raw_path = item.get("path")
                        if not isinstance(raw_path, str) or not raw_path:
                            continue
                        path = Path(raw_path).resolve()
                        if path.is_relative_to(root) and path.is_file():
                            result.append(path)
                    if not result:
                        raise RuntimeError("aria2 reported completion but no output file was found")
                    succeeded = True
                    return result
                if current == "paused":
                    # User pause is a live state, not a failure.
                    await asyncio.sleep(1.0)
                    continue
                await asyncio.sleep(1.0)
        finally:
            self._job_gids.pop(job_id, None)
            if not succeeded:
                # Refresh the chain before removing the parent. The child can
                # appear between the final poll and cancellation/failure.
                pending = list(tracked)
                seen = set()
                while pending:
                    current_gid = pending.pop()
                    if current_gid in seen:
                        continue
                    seen.add(current_gid)
                    try:
                        status = await self.call("aria2.tellStatus", [current_gid, ["followedBy"]])
                        pending.extend(str(child) for child in status.get("followedBy") or [])
                    except (RuntimeError, httpx.HTTPError):
                        pass
                for current_gid in seen:
                    try:
                        await self.call("aria2.forceRemove", [current_gid])
                    except (RuntimeError, httpx.HTTPError):
                        pass
                    try:
                        await self.call("aria2.removeDownloadResult", [current_gid])
                    except (RuntimeError, httpx.HTTPError):
                        pass

    async def close(self) -> None:
        try:
            if self.process and self.process.poll() is None:
                await self.call("aria2.shutdown", [])
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
        except Exception:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        await self.client.aclose()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manifest_entries(
    files: list[dict[str, Any]], root: Path, *, fallback_root: Path | None = None,
) -> list[dict[str, Any]]:
    if len(files) > 10_000:
        raise ValueError("Torrent contains too many files for manifest preview")
    result = []
    for item in files:
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("Torrent metadata contains an invalid path")
        path = Path(raw_path)
        allowed_root = root.resolve()
        try:
            relative = path.relative_to(allowed_root)
        except ValueError as exc:
            if fallback_root is None:
                raise ValueError("Torrent metadata contains a path outside its preview directory") from exc
            try:
                allowed_root = fallback_root.resolve()
                relative = path.relative_to(allowed_root)
            except ValueError as fallback_exc:
                raise ValueError("Torrent metadata contains a path outside its preview directory") from fallback_exc
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("Torrent metadata contains an invalid relative path")
        if not path.resolve().is_relative_to(allowed_root):
            raise ValueError("Torrent metadata contains a path outside its preview directory")
        size = _int_or_none(item.get("length"))
        index = _int_or_none(item.get("index"))
        if size is None or size < 0 or index is None or index < 1:
            raise ValueError("Torrent metadata contains invalid file size or index")
        result.append({
            "index": index, "path": relative.as_posix(),
            "name": relative.name, "size_bytes": size,
        })
    return result
