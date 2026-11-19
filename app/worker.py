from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.adapters.aria2 import Aria2Rpc
from app.adapters.gallery_dl import run_gallery_dl
from app.adapters.generic import run_generic
from app.adapters.you_get import run_you_get
from app.adapters.svtplay import run_svtplay
from app.adapters.get_sauce import run_get_sauce
from app.adapters.yt_dlp import run_ytdlp
from app.completion import WorkspaceRecoveryState, inspect_workspace_recovery, save_completed_files
from app.config import Settings
from app.db import EngineSetting, Job, append_log, get_queued_jobs, utcnow
from app.job_states import JobStatus, TERMINAL_STATUSES, ACTIVE_STATUSES, ENGINE_PROGRESS_STATUSES, InvalidJobTransition, transition_job
from app.delivery import deliver_files
from app.redaction import _redact
from app.engine_registry import ENGINE_DEFAULTS, ENGINE_DESCRIPTIONS
from app.user_failures import user_failure_message
from app.security import preflight_source_dns


LOGGER = logging.getLogger(__name__)


def delivery_completion_status(delivered: list[dict[str, Any]]) -> JobStatus:
    """One derived final outcome: an existing Files collision is not delivery."""
    return (JobStatus.SKIPPED if any(item.get("skipped") is True for item in delivered)
            else JobStatus.COMPLETED)


class JobWorker:
    def __init__(self, session_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.aria2: Aria2Rpc | None = None
        self._task: asyncio.Task | None = None
        self._active: set[asyncio.Task] = set()
        # Explicit identity mapping: names are diagnostic only, never an
        # authority for deciding which DB jobs to cancel at shutdown.
        self._active_jobs: dict[str, asyncio.Task] = {}
        self._active_engines: dict[str, str] = {}
        self._engine_deadlines: dict[str, asyncio.Timeout] = {}
        self._paused_remaining: dict[str, float] = {}
        self._stopping = False
        self._scheduler_consecutive_errors = 0
        self._last_scheduler_success: float | None = None
        self._last_scheduler_warning: float | None = None

    def readiness(self) -> tuple[bool, str]:
        """Dispatcher readiness (does not prove individual engine or destination health)."""
        if self._scheduler_consecutive_errors >= 3:
            return False, "scheduler_errors"
        if self._stopping:
            return False, "scheduler_stopping"
        if self._task is None:
            return False, "scheduler_not_started"
        if self._task.done():
            return False, "scheduler_stopped"
        if self._last_scheduler_success is None:
            return False, "scheduler_starting"
        if time.monotonic() - self._last_scheduler_success > 10.0:
            return False, "scheduler_stalled"
        return True, "ready"

    def get_aria2(self) -> Aria2Rpc:
        """Use one daemon for manifests and downloads, not competing RPC ports."""
        if self.aria2 is None:
            self.aria2 = Aria2Rpc(self.settings.data_dir, self.settings.aria2_rpc_port)
        return self.aria2

    async def pause_job(self, job_id: str) -> None:
        if self.aria2 is None or not self.aria2.can_control(job_id):
            raise RuntimeError("This job has no active aria2 transfer to pause")
        await self.aria2.pause(job_id)
        deadline = self._engine_deadlines.get(job_id)
        if deadline is not None and deadline.when() is not None:
            self._paused_remaining[job_id] = max(0.0, deadline.when() - asyncio.get_running_loop().time())
            deadline.reschedule(None)

    async def resume_job(self, job_id: str) -> None:
        if self.aria2 is None or not self.aria2.can_control(job_id):
            raise RuntimeError("This job has no paused aria2 transfer to resume")
        await self.aria2.resume(job_id)
        remaining = self._paused_remaining.pop(job_id, None)
        deadline = self._engine_deadlines.get(job_id)
        if deadline is not None and remaining is not None:
            deadline.reschedule(asyncio.get_running_loop().time() + remaining)

    async def start(self) -> None:
        self.settings.prepare_directories()
        self._recover_jobs()
        self._stopping = False
        self._scheduler_consecutive_errors = 0
        self._last_scheduler_success = None
        self._last_scheduler_warning = None
        self._task = asyncio.create_task(self._run_loop(), name="downloader-job-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        active_ids = list(self._active_jobs)
        if active_ids:
            with self.session_factory() as session:
                for job_id in active_ids:
                    current = session.get(Job, job_id)
                    if current and current.status not in TERMINAL_STATUSES:
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
            running = session.scalars(select(Job).where(Job.status.in_([s.value for s in ACTIVE_STATUSES if s != JobStatus.QUEUED]))).all()
            for job in running:
                transition_job(job, JobStatus.QUEUED)
                job.cancel_requested = False
                append_log(job, "Service restarted; job returned to the queue for recovery.")
            # An unclean shutdown can strand queued work with a stale cancellation flag.
            # A deliberately cancelled queued job has status "cancelled".
            stranded = session.scalars(
                select(Job).where(Job.status == "queued", Job.cancel_requested.is_(True))
            ).all()
            for job in stranded:
                job.cancel_requested = False
                append_log(job, "Cleared stale shutdown cancellation flag; job returned to the queue.")
            session.commit()

    def _select_queued_jobs(self, queued_jobs: list[Job], slots: int) -> list[Job]:
        engine_counts: dict[str, int] = {}
        for engine_name in self._active_engines.values():
            engine_counts[engine_name] = engine_counts.get(engine_name, 0) + 1

        selected: list[Job] = []
        for job in queued_jobs:
            if len(selected) >= slots:
                break
            limit = self.settings.max_concurrent_per_engine.get(job.engine, 1)
            if engine_counts.get(job.engine, 0) >= limit:
                continue
            selected.append(job)
            engine_counts[job.engine] = engine_counts.get(job.engine, 0) + 1
        return selected

    def _task_done(self, job_id: str, task: asyncio.Task) -> None:
        if task.done() and not task.cancelled():
            failure = task.exception()
            if failure is not None:
                # Never log raw engine output, URLs or credentials.
                LOGGER.error("download_job_task_unhandled exception_type=%s",
                             type(failure).__name__)
        self._active.discard(task)
        # A delayed callback must not erase a newer attempt with the same ID.
        if self._active_jobs.get(job_id) is task:
            self._active_jobs.pop(job_id, None)
            self._active_engines.pop(job_id, None)

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                slots = max(0, self.settings.max_concurrent_downloads - len(self._active))
                if slots:
                    active_counts: dict[str, int] = {}
                    for engine_name in self._active_engines.values():
                        active_counts[engine_name] = active_counts.get(engine_name, 0) + 1
                    saturated_engines = {
                        engine_name
                        for engine_name, engine_limit in self.settings.max_concurrent_per_engine.items()
                        if active_counts.get(engine_name, 0) >= engine_limit
                    }
                    # Filter saturated engines in SQL *before* applying LIMIT;
                    # otherwise 100 queued items of a busy engine can hide a
                    # runnable job of another engine.
                    with self.session_factory() as session:
                        queued = get_queued_jobs(session, max(100, slots), saturated_engines)
                    for job in self._select_queued_jobs(queued, slots):
                        job_id = job.id
                        task = asyncio.create_task(self._execute(job_id), name=f"download-{job_id}")
                        self._active.add(task)
                        self._active_jobs[job_id] = task
                        self._active_engines[job_id] = job.engine
                        task.add_done_callback(
                            lambda done, identifier=job_id: self._task_done(identifier, done)
                        )
                self._last_scheduler_success = time.monotonic()
                self._scheduler_consecutive_errors = 0
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._scheduler_consecutive_errors += 1
                now = time.monotonic()
                if self._last_scheduler_warning is None or now - self._last_scheduler_warning >= 60:
                    LOGGER.warning(
                        "download_scheduler_failure consecutive=%d exception_type=%s",
                        self._scheduler_consecutive_errors, type(exc).__name__,
                    )
                    self._last_scheduler_warning = now
                await asyncio.sleep(1.0)

    async def _execute(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if not job or job.status != "queued":
                return
            transition_job(job, JobStatus.STARTING)
            # Preserve previously verified delivery records on crash recovery.
            # The explicit retry API clears these records itself; if the
            # completed-download manifest is missing we clear them below.
            job.updated_at = utcnow()
            append_log(job, f"Job started with engine {job.engine}")
            url, engine, overrides = job.url, job.engine, dict(job.overrides or {})
            engine_row = session.get(EngineSetting, engine)
            global_settings = {**ENGINE_DEFAULTS.get(engine, {}), **(engine_row.settings if engine_row else {})}
            session.commit()

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
                    state = data.get("state", JobStatus.DOWNLOADING.value)
                    if state not in ENGINE_PROGRESS_STATUSES:
                        raise InvalidJobTransition("Engine emitted an unsupported progress state")
                    # RPC polling may briefly report an old active state after pause.
                    if current.status == JobStatus.PAUSED and state == JobStatus.DOWNLOADING and job_id in self._paused_remaining:
                        return
                    transition_job(current, state, progress={**data, "state": state})
                    current.updated_at = utcnow()
                    session.commit()

        async def is_cancelled() -> bool:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                return not current or current.cancel_requested

        async def on_delivered(item: dict[str, Any]) -> None:
            # Keep verified partial results if cancellation interrupts a
            # multi-file delivery; remote writes cannot be rolled back safely.
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    # Re-delivery after a restart may verify the same file
                    # again. Replace the old record rather than duplicating it.
                    existing = [
                        old for old in (current.output_files or [])
                        if not (
                            isinstance(old, dict)
                            and old.get("path") == item.get("path")
                            and old.get("delivery") == item.get("delivery")
                        )
                    ]
                    current.output_files = [*existing, item]
                    session.commit()

        try:
            # A workspace error must pass through the normal FAILED transition.
            workdir = (self.settings.data_dir / "jobs" / job_id).resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            recovery = inspect_workspace_recovery(workdir)
            existing_files = list(recovery.files)
            if recovery.state != WorkspaceRecoveryState.VERIFIED_COMPLETION:
                # The only durable authority to preserve previous delivery
                # records is a verified complete-download manifest.
                with self.session_factory() as session:
                    current = session.get(Job, job_id)
                    if current and current.output_files:
                        current.output_files = []
                        append_log(
                            current,
                            "Cleared prior delivery records: no verified completed-download manifest survived recovery",
                        )
                        session.commit()
            if recovery.state == WorkspaceRecoveryState.DISCARD_UNVERIFIED:
                # Treat partial files (with or without an invalid manifest)
                # explicitly as interrupted output, never reusable media.
                shutil.rmtree(workdir)
                workdir.mkdir(parents=True, exist_ok=True)
                await on_log("Discarded unverified output left by an interrupted download")
            if not existing_files and not self.settings.allow_private_targets and engine != "generic":
                # Check every A/AAAA answer before the engine starts. This is
                # defense in depth, not DNS pinning: an egress firewall is
                # still required for redirects and engine-discovered URLs.
                await asyncio.to_thread(preflight_source_dns, url)
            # Deadline covers the engine invocation, not verified delivery or
            # checksum readback; an expired engine fails with a distinct error.
            async with asyncio.timeout(self.settings.job_timeout_seconds) as deadline:
                self._engine_deadlines[job_id] = deadline
                if existing_files:
                    await on_log(f"Recovery verified {len(existing_files)} completed output file(s); skipping another download")
                    files = existing_files
                elif engine == "yt-dlp":
                    files = await run_ytdlp(
                        executable="yt-dlp", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        cookie_dir=self.settings.data_dir / "cookies",
                        encryption_key=self.settings.encryption_key,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "gallery-dl":
                    files = await run_gallery_dl(
                        executable="gallery-dl", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        cookie_dir=self.settings.data_dir / "cookies",
                        encryption_key=self.settings.encryption_key,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "generic":
                    files = await run_generic(
                        executable="curl", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                        allow_private_targets=self.settings.allow_private_targets,
                    )
                elif engine == "you-get":
                    files = await run_you_get(
                        executable="you-get", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        cookie_dir=self.settings.data_dir / "cookies",
                        encryption_key=self.settings.encryption_key,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "get-sauce":
                    files = await run_get_sauce(
                        executable="get-sauce", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "svtplay-dl":
                    files = await run_svtplay(
                        executable="svtplay-dl", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "aria2c":
                    files = await self.get_aria2().download(
                        url=url, job_id=job_id, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                else:
                    raise ValueError(f"Unsupported engine: {engine}")
            if await is_cancelled():
                raise asyncio.CancelledError
            if not existing_files:
                save_completed_files(workdir, files)
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    transition_job(current, JobStatus.DELIVERING)
                    append_log(current, "Download complete; delivering output files")
                    session.commit()
            delivered = await deliver_files(
                files, job_id, self.session_factory, self.settings,
                is_cancelled=is_cancelled, on_delivered=on_delivered,
            )
            if await is_cancelled():
                raise asyncio.CancelledError
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    outcome = delivery_completion_status(delivered)
                    transition_job(current, outcome)
                    current.output_files = delivered
                    current.progress = {**(current.progress or {}), "state": outcome.value, "percent": 100}
                    current.finished_at = utcnow()
                    if outcome == JobStatus.SKIPPED:
                        append_log(current, "Delivery skipped: existing destination item kept")
                    else:
                        append_log(current, f"Delivery verified for {len(delivered)} file(s)")
                    session.commit()
        except asyncio.CancelledError:
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    if self._stopping:
                        transition_job(current, JobStatus.QUEUED)
                        current.cancel_requested = False
                        append_log(current, "Service is stopping; unfinished job returned to the queue")
                    else:
                        was_delivering = current.status == "delivering"
                        transition_job(current, JobStatus.CANCELLED)
                        if was_delivering and current.output_files:
                            current.progress = {**(current.progress or {}),
                                                "partial_delivery": True,
                                                "verified_file_count": len(current.output_files)}
                        if was_delivering:
                            append_log(
                                current,
                                "Delivery cancelled; some files may already exist at the destination. "
                                "Verified completed uploads remain listed in output_files.",
                            )
                        else:
                            append_log(current, "Job cancelled")
                    session.commit()
        except Exception as exc:
            message = _redact(str(exc))
            with self.session_factory() as session:
                current = session.get(Job, job_id)
                if current:
                    was_delivering = current.status == JobStatus.DELIVERING
                    transition_job(current, JobStatus.FAILED)
                    if was_delivering and current.output_files:
                        current.progress = {**(current.progress or {}),
                                            "partial_delivery": True,
                                            "verified_file_count": len(current.output_files)}
                    current.error = user_failure_message(exc, during_delivery=was_delivering)
                    current.finished_at = utcnow()
                    append_log(current, f"Job failed: {message}")
                    session.commit()

        finally:
            self._engine_deadlines.pop(job_id, None)
            self._paused_remaining.pop(job_id, None)
