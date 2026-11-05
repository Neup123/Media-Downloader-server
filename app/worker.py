from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.adapters.aria2 import Aria2Rpc
from app.adapters.yt_dlp import run_ytdlp
from app.config import Settings
from app.db import EngineSetting, Job, append_log, get_queued_ids, utcnow
from app.delivery import deliver_files


ENGINE_DEFAULTS: dict[str, dict[str, Any]] = {
    "yt-dlp": {
        "format": "bv*+ba/b",
        "subtitles_lang": "",
        "write_subtitles": False,
        "embed_subtitles": False,
        "extra_args": [],
    },
    "aria2c": {
        "options": {
            "max-connection-per-server": "4",
            "split": "4",
            "max-tries": "5",
            "retry-wait": "5",
        },
    },
}

ENGINE_DESCRIPTIONS = [
    {"name": "yt-dlp", "label": "yt-dlp", "role": "Site extraction and media downloads", "priority": "P0", "available": True},
    {"name": "aria2c", "label": "aria2c", "role": "Direct downloads, BitTorrent, and magnet links", "priority": "P0", "available": True},
]


class JobWorker:
    def __init__(self, session_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.aria2: Aria2Rpc | None = None
        self._task: asyncio.Task | None = None
        self._active: set[asyncio.Task] = set()
        self._stopping = False

    async def start(self) -> None:
        self.settings.prepare_directories()
        self._recover_jobs()
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop(), name="downloader-job-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        active_ids = []
        for task in list(self._active):
            name = task.get_name()
            if name.startswith("download-"):
                active_ids.append(name.removeprefix("download-"))
        if active_ids:
            with self.session_factory() as session:
                for job_id in active_ids:
                    current = session.get(Job, job_id)
                    if current and current.status not in {"completed", "failed", "cancelled"}:
                        current.cancel_requested = True
                session.commit()
        if self._active:
            try:
                await asyncio.wait_for(asyncio.gather(*list(self._active), return_exceptions=True), timeout=12)
            except asyncio.TimeoutError:
                for task in list(self._active):
                    task.cancel()
                await asyncio.gather(*list(self._active), return_exceptions=True)
        if self.aria2 is not None:
            await self.aria2.close()
            self.aria2 = None

    def _recover_jobs(self) -> None:
        with self.session_factory() as session:
            running = session.scalars(select(Job).where(Job.status.in_(["starting", "extracting", "downloading", "post-processing", "delivering"]))).all()
            for job in running:
                job.status = "queued"
                job.cancel_requested = False
                append_log(job, "Service restarted; job returned to the queue for recovery.")
            session.commit()

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                slots = max(0, self.settings.max_concurrent_downloads - len(self._active))
                if slots:
                    with self.session_factory() as session:
                        job_ids = get_queued_ids(session, slots)
                    for job_id in job_ids:
                        task = asyncio.create_task(self._execute(job_id), name=f"download-{job_id}")
                        self._active.add(task)
                        task.add_done_callback(self._active.discard)
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1.0)

    async def _execute(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if not job or job.status != "queued":
                return
            job.status = "starting"
            job.updated_at = utcnow()
            append_log(job, f"Job started with engine {job.engine}")
            url, engine, overrides = job.url, job.engine, dict(job.overrides or {})
            engine_row = session.get(EngineSetting, engine)
            global_settings = {**ENGINE_DEFAULTS.get(engine, {}), **(engine_row.settings if engine_row else {})}
            session.commit()

        workdir = (self.settings.data_dir / "jobs" / job_id).resolve()
        workdir.mkdir(parents=True, exist_ok=True)

        async def on_log(message: str) -> None:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    safe = _redact(message)
                    append_log(current, safe)
                    session.commit()

        async def on_progress(data: dict[str, Any]) -> None:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    current.status = data.get("state", "downloading")
                    current.progress = data
                    current.updated_at = utcnow()
                    session.commit()

        async def is_cancelled() -> bool:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                return not current or current.cancel_requested

        try:
            existing_files = _completed_files(workdir)
            if existing_files:
                await on_log(f"Recovery found {len(existing_files)} completed output file(s); skipping another download")
                files = existing_files
            elif engine == "yt-dlp":
                files = await run_ytdlp(
                    executable="yt-dlp", url=url, workdir=workdir,
                    global_settings=global_settings, overrides=overrides,
                    cookie_dir=self.settings.data_dir / "cookies",
                    on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                )
            elif engine == "aria2c":
                if self.aria2 is None:
                    self.aria2 = Aria2Rpc(self.settings.data_dir, self.settings.aria2_rpc_port)
                files = await self.aria2.download(
                    url=url, job_id=job_id, workdir=workdir,
                    global_settings=global_settings, overrides=overrides,
                    on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                )
            else:
                raise ValueError(f"Unsupported engine: {engine}")
            if await is_cancelled():
                raise asyncio.CancelledError
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    current.status = "delivering"
                    append_log(current, "Download complete; delivering output files")
                    session.commit()
            delivered = await deliver_files(files, job_id, self.session_factory, self.settings)
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    current.status = "completed"
                    current.output_files = delivered
                    current.progress = {**(current.progress or {}), "state": "completed", "percent": 100}
                    current.finished_at = utcnow()
                    append_log(current, f"Delivery verified for {len(delivered)} file(s)")
                    session.commit()
        except asyncio.CancelledError:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    if self._stopping:
                        current.status = "queued"
                        current.cancel_requested = False
                        append_log(current, "Service is stopping; unfinished job returned to the queue")
                    else:
                        current.status = "cancelled"
                        current.finished_at = utcnow()
                        append_log(current, "Job cancelled")
                    session.commit()
        except Exception as exc:
            message = _redact(str(exc))
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    current.status = "failed"
                    current.error = message[:2000]
                    current.finished_at = utcnow()
                    append_log(current, f"Job failed: {message}")
                    session.commit()


def _redact(message: str) -> str:
    message = re.sub(r"(?i)(authorization|cookie|set-cookie|app_password|rpc-secret)(\s*[:=]\s*)[^\s,;]+", r"\1\2[REDACTED]", message)
    message = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", message)
    message = re.sub(r"([?&](?:token|access_token|api_key|key|secret|auth|password|signature|sig|session|cookie)=)[^&#\s]+", r"\1[REDACTED]", message, flags=re.IGNORECASE)
    return message[:4000]


def _completed_files(workdir: Path) -> list[Path]:
    ignored_suffixes = (".part", ".ytdl", ".aria2", ".tmp")
    output = []
    for path in workdir.rglob("*"):
        if not path.is_file() or path.name.endswith(ignored_suffixes):
            continue
        if Path(str(path) + ".aria2").exists():
            continue
        output.append(path)
    return sorted(output)
