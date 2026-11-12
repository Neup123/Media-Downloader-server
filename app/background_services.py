"""Core-owned foreground subprocesses. Only persisted mode grants permission.

No Docker API, detached processes, or PID-only killing: an occupied port is
reported explicitly rather than killing a process whose identity is unknown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.adapters.process_env import downloader_environment
from app.db import EngineSetting, Job, append_log, utcnow
from app.redaction import _redact

ENGINES = frozenset({"pyload", "cobalt"})
MODES = frozenset({"off", "always", "on-demand"})
logger = logging.getLogger(__name__)


def service_mode(session, name):
    row = session.get(EngineSetting, name)
    mode = (row.settings or {}).get("mode", "off") if row else "off"
    return mode if isinstance(mode, str) and mode in MODES else "off"


@dataclass
class Service:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    process: object = None
    state: str = "stopped"
    error: str | None = None
    users: set = field(default_factory=set)
    logs: set = field(default_factory=set)
    idle: asyncio.Task | None = None
    reader: asyncio.Task | None = None
    retry_after: float = 0
    applied_mode: str = "off"


class BackgroundServices:
    def __init__(self, session_factory, settings, *, grace=180, readiness_timeout=30, stop_timeout=10):
        self.sessions = session_factory
        self.settings = settings
        self.services = {name: Service() for name in ENGINES}
        self.grace = grace
        self.readiness_timeout = readiness_timeout
        self.stop_timeout = stop_timeout
        self.closed = False

    def mode(self, name):
        with self.sessions() as session:
            return service_mode(session, name)

    def status(self, name):
        service = self.services[name]
        state = service.state
        if service.process and service.process.returncode is not None and state == "running":
            state = "stopped"
            service.error = f"Service exited unexpectedly ({service.process.returncode})"
        return {"state": state, "service_error": service.error}

    def phase(self, job_id, name, phase):
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job:
                progress = dict(job.progress or {})
                if phase is None:
                    progress.pop("phase", None)
                    progress.pop("service", None)
                else:
                    progress.update(phase=phase, service=name)
                job.progress = progress
                job.updated_at = utcnow()
                session.commit()

    def log(self, name, message):
        safe = _redact(f"[{name} service] {message}")
        logger.info(safe)
        with self.sessions() as session:
            for job_id in self.services[name].logs:
                job = session.get(Job, job_id)
                if job:
                    append_log(job, safe)
            session.commit()

    def endpoint(self, name):
        endpoint = self.settings.pyload_api_url if name == "pyload" else self.settings.cobalt_api_url
        parsed = urlsplit(endpoint or "")
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise RuntimeError(f"{name} service requires a loopback HTTP API URL")
        return endpoint.rstrip("/"), parsed.hostname, parsed.port or 80

    async def ready(self, name):
        base, _, _ = self.endpoint(name)
        headers = {"X-API-Key": self.settings.pyload_api_key or ""} if name == "pyload" else {}
        route = "/api/get_server_version" if name == "pyload" else "/"
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                response = await client.get(base + route, headers=headers)
                return response.is_success
        except httpx.HTTPError:
            return False

    def command(self, name):
        raw = self.settings.pyload_command if name == "pyload" else self.settings.cobalt_command
        if raw:
            argv = json.loads(raw)
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
                raise ValueError("Service command must be a nonempty JSON argv array")
            return argv
        if name == "pyload":
            return ["pyload", "--userdir", str(self.settings.data_dir / "services" / "pyload"),
                    "--pidfile", "", "--storagedir", str(self.settings.pyload_shared_root or self.settings.data_dir / "pyload-downloads")]
        return ["node", "/opt/cobalt/api/src/cobalt.js"]

    async def _capture(self, name, process):
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            self.log(name, chunk.decode("utf-8", errors="replace").rstrip())

    async def _start(self, name):
        service = self.services[name]
        if self.closed or self.mode(name) == "off":
            raise RuntimeError(f"{name} service is off")
        if service.process and service.process.returncode is None:
            if service.state == "running" and service.error is None:
                return
            # A failed readiness cleanup may retain a live child. Never treat
            # that handle as a successful boot on the following job.
            await self._stop(name)
            if service.process and service.process.returncode is None:
                raise RuntimeError(f"The {'pyLoad' if name == 'pyload' else name} service did not start: previous process could not be stopped; see service_error")
        service.state = "starting"
        service.error = None
        try:
            base, host, port = self.endpoint(name)
            # An unowned listener may be a stale daemon. Never adopt it or
            # kill an arbitrary process using a stale/reused PID file.
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 1)
            except (OSError, TimeoutError):
                pass
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"port {port} is occupied by an unowned or stale process; free it before retrying")
            directory = self.settings.data_dir / "services" / name
            directory.mkdir(parents=True, exist_ok=True)
            # We run foreground, never read or trust daemon PID/lock files.
            env = downloader_environment()
            if name == "cobalt":
                env.update(API_URL=base + "/", API_PORT=str(port), API_LISTEN_ADDRESS=host)
            service.process = await asyncio.create_subprocess_exec(
                *self.command(name), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=env,
                start_new_session=os.name != "nt",
            )
            service.reader = asyncio.create_task(self._capture(name, service.process))
            async with asyncio.timeout(self.readiness_timeout):
                while not await self.ready(name):
                    if self.closed or self.mode(name) == "off":
                        raise RuntimeError("start revoked by owner")
                    if service.process.returncode is not None:
                        raise RuntimeError(f"process exited ({service.process.returncode}); check the job service log and daemon configuration/locks")
                    await asyncio.sleep(.2)
            if service.process.returncode is not None:
                raise RuntimeError("process exited during readiness")
            service.state = "running"
            service.retry_after = 0
            self.log(name, "API ready")
        except BaseException as exc:
            await self._stop(name)
            service.error = f"The {'pyLoad' if name == 'pyload' else name} service did not start: {_redact(str(exc)) or 'readiness timed out'}"
            service.retry_after = time.monotonic() + 30
            self.log(name, service.error)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(service.error) from exc

    async def _stop(self, name, job_id=None):
        service = self.services[name]
        process = service.process
        if not process:
            service.state = "stopped"
            return
        service.state = "stopping"
        if job_id:
            self.phase(job_id, name, "stopping_service")
        try:
            if process.returncode is None:
                try:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), self.stop_timeout)
                except TimeoutError:
                    service.error = "Service required forced termination after stop timeout"
                    self.log(name, service.error)
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    await asyncio.wait_for(process.wait(), self.stop_timeout)
                except ProcessLookupError:
                    pass
            if service.reader:
                await asyncio.wait_for(asyncio.shield(service.reader), self.stop_timeout)
            service.process = None
            service.state = "stopped"
        except Exception as exc:
            service.error = f"Service stop failed: {_redact(str(exc))}"
            self.log(name, service.error)
            # Keep the process handle: the next stop/acquire can retry safely.
            service.state = "running" if process.returncode is None else "stopped"
        finally:
            if job_id:
                self.phase(job_id, name, None)

    async def acquire(self, name, job_id):
        service = self.services[name]
        # Publish before waiting for the lock/readiness, including shared boots.
        if service.state != "running" or not service.process or service.process.returncode is not None:
            self.phase(job_id, name, "starting_service")
        service.logs.add(job_id)
        async with service.lock:
            if self.closed or self.mode(name) == "off":
                raise RuntimeError(f"{name} service is off")
            if service.idle:
                service.idle.cancel()
                service.idle = None
            service.logs.add(job_id)
            await self._start(name)
            service.users.add(job_id)

    async def release(self, name, job_id):
        service = self.services[name]
        async with service.lock:
            service.users.discard(job_id)
            if self.mode(name) == "always":
                service.logs.discard(job_id)
            if not service.users and self.mode(name) == "on-demand" and not self.closed:
                if service.idle:
                    service.idle.cancel()
                service.idle = asyncio.create_task(self._idle_stop(name, job_id))

    async def _idle_stop(self, name, job_id):
        await asyncio.sleep(self.grace)
        service = self.services[name]
        async with service.lock:
            if not service.users and self.mode(name) == "on-demand":
                await self._stop(name, job_id)
                service.logs.clear()
            service.idle = None

    async def apply(self, name):
        service = self.services[name]
        async with service.lock:
            mode = self.mode(name)
            if mode == "on-demand" and service.applied_mode == mode:
                # An unchanged autosave must preserve the existing idle grace.
                return
            if service.idle:
                service.idle.cancel()
                service.idle = None
            service.applied_mode = mode
            if mode == "always":
                await self._start(name)
            elif mode == "off" or not service.users:
                await self._stop(name)
                service.logs.clear()

    async def reconcile(self):
        # Captcha jobs retain their lease across worker invocations. Release
        # only when cancelled/terminal; queued captcha resumes reuse the lease.
        for name, service in self.services.items():
            for job_id in list(service.users):
                with self.sessions() as session:
                    job = session.get(Job, job_id)
                    done = job is None or job.status in {"completed", "failed", "cancelled"}
                if done:
                    await self.release(name, job_id)
            # Always-on and captcha-held services survive unexpected child
            # exits. Bound failed background retries rather than spinning.
            if (not self.closed and self.mode(name) != "off"
                    and (self.mode(name) == "always" or service.users)
                    and (not service.process or service.process.returncode is not None)
                    and time.monotonic() >= service.retry_after):
                async with service.lock:
                    try:
                        await self._start(name)
                    except RuntimeError:
                        pass  # Specific error already retained in live state/logs.

    async def close(self):
        self.closed = True
        for name, service in self.services.items():
            if service.idle:
                service.idle.cancel()
            async with service.lock:
                await self._stop(name)
