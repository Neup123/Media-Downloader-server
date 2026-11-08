from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.adapters.aria2 import Aria2Rpc
from app.adapters.gallery_dl import run_gallery_dl
from app.adapters.generic import run_generic
from app.adapters.you_get import run_you_get
from app.adapters.svtplay import run_svtplay
from app.adapters.get_sauce import run_get_sauce, DEFAULTS as GET_SAUCE_DEFAULTS
from app.adapters.yt_dlp import run_ytdlp
from app.completion import load_completed_files, save_completed_files
from app.config import Settings
from app.db import EngineSetting, Job, append_log, get_queued_jobs, utcnow
from app.job_states import JobStatus, TERMINAL_STATUSES, ACTIVE_STATUSES, ENGINE_PROGRESS_STATUSES, InvalidJobTransition, transition_job
from app.delivery import deliver_files
from app.security import preflight_source_dns


ENGINE_DEFAULTS: dict[str, dict[str, Any]] = {
    "generic": {"filename": "", "follow_redirects": False, "max_redirects": 5, "retry_count": 2, "user_agent": ""},
    "you-get": {"format": "", "cookies_file": "", "no_proxy": False},
    "svtplay-dl": {"resolution": "", "quality": "", "subtitles": False, "all_subtitles": False, "all_episodes": False, "output_format": "mp4"},
    "get-sauce": GET_SAUCE_DEFAULTS,
    "yt-dlp": {
        "format": "bv*+ba/b",
        "subtitles_lang": "",
        "write_subtitles": False,
        "embed_subtitles": False,
        "extra_args": [],
        "allow_playlist": False,
        "playlist_items": "",
    },
    "gallery-dl": {
        "options": {},
        "cookies_file": "",
        "proxy": "",
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
    {"name": "gallery-dl", "label": "gallery-dl", "role": "Image galleries and supported image-board extractors", "priority": "P0", "available": True},
    {"name": "generic", "label": "generic / curl", "role": "Plain direct HTTP(S) downloads", "priority": "P1", "available": True},
    {"name": "you-get", "label": "you-get", "role": "Fallback site extractor", "priority": "P1", "available": True},
    {"name": "svtplay-dl", "label": "svtplay-dl", "role": "Nordic broadcaster and video services", "priority": "P2", "available": True},
    {"name": "get-sauce", "label": "get-sauce", "role": "Specialized media and gallery extractor", "priority": "P2", "available": True},
]


class JobWorker:
    def __init__(self, session_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.aria2: Aria2Rpc | None = None
        self._task: asyncio.Task | None = None
        self._active: set[asyncio.Task] = set()
        self._active_engines: dict[str, str] = {}
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
        self._active.discard(task)
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
                        self._active_engines[job_id] = job.engine
                        task.add_done_callback(
                            lambda done, identifier=job_id: self._task_done(identifier, done)
                        )
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
                    state = data.get("state", JobStatus.DOWNLOADING.value)
                    if state not in ENGINE_PROGRESS_STATUSES:
                        raise InvalidJobTransition("Engine emitted an unsupported progress state")
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
            existing_files = load_completed_files(workdir)
            if not existing_files:
                with self.session_factory() as session:
                    current = session.get(Job, job_id)
                    if current and current.output_files:
                        current.output_files = []
                        append_log(
                            current,
                            "Cleared prior delivery records: no verified completed-download manifest survived recovery",
                        )
                        session.commit()
            if not existing_files and any(workdir.iterdir()):
                # Partial outputs may have ordinary extensions. Only a valid
                # completion manifest permits reuse; otherwise start clean.
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
            async with asyncio.timeout(self.settings.job_timeout_seconds):
                if existing_files:
                    await on_log(f"Recovery verified {len(existing_files)} completed output file(s); skipping another download")
                    files = existing_files
                elif engine == "yt-dlp":
                    files = await run_ytdlp(
                        executable="yt-dlp", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        cookie_dir=self.settings.data_dir / "cookies",
                        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
                    )
                elif engine == "gallery-dl":
                    files = await run_gallery_dl(
                        executable="gallery-dl", url=url, workdir=workdir,
                        global_settings=global_settings, overrides=overrides,
                        cookie_dir=self.settings.data_dir / "cookies",
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
                    transition_job(current, JobStatus.COMPLETED)
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
                    current.error = message[:2000]
                    current.finished_at = utcnow()
                    append_log(current, f"Job failed: {message}")
                    session.commit()


_LOG_URL = re.compile(r"""(?i)\b(?:https?|ftps?)://[^\s<>"'\x60]+""")


def _redact(message: str) -> str:
    """Remove arbitrary signed URL parameters before persisting engine logs.

    A query key allowlist cannot cover vendor-specific signed URL schemes.
    Keep the origin/path for diagnostics, but never preserve query/fragment
    content, including uncommon credential names and URL-embedded passwords.
    """
    message = re.sub(
        r"(?i)(authorization|cookie|set-cookie|app_password|rpc-secret|token|password|secret|api_key)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]", message,
    )

    def clean_url(match: re.Match[str]) -> str:
        url = match.group(0)
        # Credential-bearing authorities must never be echoed; this applies
        # to FTP(S) and HTTP(S) links that extractors may discover internally.
        scheme, rest = url.split("://", 1)
        authority, slash, path = rest.partition("/")
        if "@" in authority:
            authority = "[REDACTED]@" + authority.rsplit("@", 1)[-1]
        result = scheme + "://" + authority + (slash + path if slash else "")
        cut = min((i for i in (result.find("?"), result.find("#")) if i >= 0), default=-1)
        if cut >= 0:
            result = result[:cut] + "?[REDACTED]"
        return result

    message = _LOG_URL.sub(clean_url, message)
    # Preserve existing protection for bare key/value secrets not attached to
    # an HTTP URL, such as a credential printed by a CLI diagnostic.
    message = re.sub(
        r"([?&](?:token|access_token|api_key|key|secret|auth|password|signature|sig|session|cookie)=)[^&#\s]+",
        r"\1[REDACTED]", message, flags=re.IGNORECASE,
    )
    return message[:4000]


