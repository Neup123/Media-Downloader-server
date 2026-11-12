from __future__ import annotations

import asyncio
import base64
import binascii
import json
import httpx
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import select, cast, or_, Text, delete
from sqlalchemy.orm import Session

from app.adapters.aria2 import Aria2Rpc
from app.adapters.process_env import downloader_environment
from app.completion import load_completed_files
from app.config import Settings
from app.cookies import encrypt_cookie
from app.version import product_version
from app.discovery import PreviewError, inspect_ytdlp
from app.options_catalog import get_catalog
from app.db import DeliverySetting, EngineSetting, EngineRule, Preset, Job, make_engine, make_session_factory, initialize_database, append_log, requeue_job, utcnow
from app.job_states import JobStatus, TERMINAL_STATUSES, ACTIVE_STATUSES, transition_job
from app.delivery import DeliveryError, serialize_delivery, set_delivery_settings, encrypt_job_delivery, verify_delivery_destination
from app.schemas import CookieUploadIn, CookieFileOut, CookieUploadedOut, CookieDeletedOut, DeliverySettingsIn, DeliverySettingsOut, DeliveryTestOut, EngineStatusOut, EngineOptionsOut, EngineSettingsIn, EngineSettingsOut, EngineRuleInput, EngineRuleOrderInput, PresetInput, JobBatchCreate, JobCreate, JobBatchOut, JobOut, MediaPreviewIn, TorrentManifestIn
from app.security import UnsafeUrlError, preflight_source_dns, validate_target_url
from app.worker import ENGINE_DEFAULTS, ENGINE_DESCRIPTIONS, JobWorker


ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"
OPENAPI_FILE = ROOT / "openapi.yaml"


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    config = settings or Settings.from_env()
    config.prepare_directories()
    engine = make_engine(config.database_url)
    initialize_database(engine)
    session_factory = make_session_factory(engine)
    worker = JobWorker(session_factory, config) if start_worker else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not config.api_token:
            raise RuntimeError("APP_API_TOKEN is required. Generate a long random token and configure it before starting the service.")
        if worker:
            await worker.start()
        try:
            yield
        finally:
            if worker:
                await worker.stop()
            engine.dispose()

    app = FastAPI(
        title="Downloader Server API",
        version=product_version(),
        description="Standalone media download job manager. The checked-in OpenAPI YAML is the public API contract.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.worker = worker

    def db_session():
        with session_factory() as session:
            yield session

    def authenticate(x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None):
        if not x_api_key or not secrets_compare(x_api_key, config.api_token):
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required")
        return True

    @app.get("/healthz", tags=["system"])
    def healthz():
        return {"status": "ok", "service": "downloader-server"}

    @app.post("/api/v1/preview", tags=["jobs"])
    async def preview_media(payload: MediaPreviewIn, _: bool = Depends(authenticate)):
        try:
            url = validate_target_url(payload.url, "yt-dlp", config.allow_private_targets)
            if not config.allow_private_targets:
                await asyncio.to_thread(preflight_source_dns, url)
            return await inspect_ytdlp(url, max_entries=payload.max_entries, offset=payload.offset)
        except (UnsafeUrlError, ValueError, PreviewError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


    @app.post("/api/v1/torrent/manifest", tags=["jobs"])
    async def torrent_manifest(payload: TorrentManifestIn, _: bool = Depends(authenticate)):
        """Inspect magnet metadata or uploaded .torrent bytes without a download job."""
        try:
            url = None
            torrent_base64 = None
            if payload.url is not None:
                url = validate_target_url(payload.url, "aria2c", config.allow_private_targets)
                if not url.startswith("magnet:?"):
                    raise ValueError("Provide a magnet link or upload .torrent bytes as torrent_base64")
            else:
                try:
                    raw = base64.b64decode(payload.torrent_base64 or "", validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("torrent_base64 must be valid base64-encoded .torrent data") from exc
                if not 20 <= len(raw) <= 2_000_000:
                    raise ValueError(".torrent metadata must be between 20 bytes and 2 MB")
                torrent_base64 = base64.b64encode(raw).decode("ascii")

            # A real worker shares its RPC daemon with downloads; test/standalone
            # apps without a worker use a lifecycle-scoped temporary daemon.
            rpc = worker.get_aria2() if worker is not None else Aria2Rpc(config.data_dir, config.aria2_rpc_port)
            try:
                return await rpc.inspect_torrent(url=url, torrent_base64=torrent_base64)
            finally:
                if worker is None:
                    await rpc.close()
        except (UnsafeUrlError, ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/engines", response_model=list[EngineStatusOut], tags=["engines"])
    def list_engines(_: bool = Depends(authenticate)):
        result = []
        for item in ENGINE_DESCRIPTIONS:
            binary = {"yt-dlp": "yt-dlp", "aria2c": "aria2c", "gallery-dl": "gallery-dl", "generic": "curl", "you-get": "you-get", "svtplay-dl": "svtplay-dl", "get-sauce": "get-sauce"}[item["name"]]
            executable = shutil.which(binary)
            version = None
            if executable:
                try:
                    version_flag = "-v" if item["name"] == "get-sauce" else "--version"
                    completed = subprocess.run(
                        [executable, version_flag], capture_output=True, text=True,
                        timeout=4, check=False, env=downloader_environment(),
                    )
                    # get-sauce's -v can exit successfully while printing
                    # a blank line. Ignore empty lines and do not treat help,
                    # errors or whitespace as a meaningful engine version.
                    lines = (completed.stdout or "").splitlines() + (completed.stderr or "").splitlines()
                    version = next((line.strip()[:160] for line in lines if line.strip()), "unknown") \
                        if completed.returncode == 0 else "unknown"
                    if item["name"] == "get-sauce" and version == "unknown" \
                            and Path(executable) == Path("/usr/local/bin/get-sauce"):
                        # The production build records the pinned Go module
                        # version extracted from the installed binary's build
                        # info. Do not label a different PATH binary using it.
                        try:
                            module_version = (ROOT / "get-sauce.version").read_text().strip()
                            if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", module_version):
                                version = module_version + " (Go module)"
                        except OSError:
                            pass
                except (OSError, subprocess.TimeoutExpired, IndexError):
                    version = "unknown"
            result.append({
                **item, "installed": executable is not None,
                "available": executable is not None and version not in (None, "unknown"),
                "version": version,
                "defaults": ENGINE_DEFAULTS[item["name"]],
            })
        return result

    @app.get("/api/v1/engines/{engine_name}/options", response_model=EngineOptionsOut, response_model_exclude_unset=True, tags=["engines"])
    def engine_option_catalog(engine_name: str, _: bool = Depends(authenticate)):
        _require_engine(engine_name)
        return {"engine": engine_name, "fields": get_catalog(engine_name)}

    @app.get("/api/v1/engines/{engine_name}/settings", response_model=EngineSettingsOut, tags=["engines"])
    def get_engine_settings(engine_name: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        _require_engine(engine_name)
        row = session.get(EngineSetting, engine_name)
        return {"engine": engine_name, "settings": {**ENGINE_DEFAULTS[engine_name], **(row.settings if row else {})}}

    @app.put("/api/v1/engines/{engine_name}/settings", response_model=EngineSettingsOut, tags=["engines"])
    def update_engine_settings(engine_name: str, payload: EngineSettingsIn, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        _require_engine(engine_name)
        try:
            normalized = validate_engine_settings(engine_name, payload.settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        row = session.get(EngineSetting, engine_name)
        if row is None:
            row = EngineSetting(engine=engine_name, settings=normalized)
            session.add(row)
        else:
            row.settings = normalized
            row.updated_at = utcnow()
        session.commit()
        return {"engine": engine_name, "settings": {**ENGINE_DEFAULTS[engine_name], **normalized}}

    @app.get("/api/v1/cookies", response_model=list[CookieFileOut], tags=["engines"])
    def list_cookie_files(_: bool = Depends(authenticate)):
        cookie_dir = config.data_dir / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        return [
            {"filename": p.name, "size_bytes": p.stat().st_size}
            for p in sorted(cookie_dir.iterdir())
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in {".txt", ".cookies"}
        ]

    @app.put("/api/v1/cookies/{filename}", response_model=CookieUploadedOut, tags=["engines"])
    def upload_cookie_file(filename: str, payload: CookieUploadIn, _: bool = Depends(authenticate)):
        if Path(filename).name != filename or filename in {".", ".."} or Path(filename).suffix.lower() not in {".txt", ".cookies"}:
            raise HTTPException(status_code=422, detail="Cookie filename must be a simple .txt or .cookies filename")
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
            decoded = content.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Cookie file must be valid base64-encoded UTF-8 text") from exc
        if len(content) > 1_000_000 or "\x00" in decoded:
            raise HTTPException(status_code=413, detail="Cookie file is too large or contains invalid bytes")
        try:
            encrypted = encrypt_cookie(content, config.encryption_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        cookie_dir = config.data_dir / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        if cookie_dir.is_symlink():
            raise HTTPException(status_code=422, detail="Cookie directory must not be a symlink")
        target = cookie_dir / filename
        temporary: Path | None = None
        try:
            # NamedTemporaryFile is created 0600; replace the destination
            # atomically so partial writes and pre-existing symlinks cannot
            # redirect or expose credentials outside the cookie directory.
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=cookie_dir, prefix=".downloader-cookie-",
                suffix=".part", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                os.chmod(stream.name, 0o600)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Cookie file could not be stored securely") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"filename": filename, "size_bytes": len(content), "stored": True}

    @app.delete("/api/v1/cookies/{filename}", response_model=CookieDeletedOut, tags=["engines"])
    def delete_cookie_file(filename: str, _: bool = Depends(authenticate)):
        if Path(filename).name != filename or filename in {".", ".."}:
            raise HTTPException(status_code=422, detail="Invalid cookie filename")
        target = config.data_dir / "cookies" / filename
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Cookie file not found")
        target.unlink()
        return {"deleted": True, "filename": filename}

    @app.get("/api/v1/presets", tags=["settings"])
    def list_presets(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        rows = session.scalars(select(Preset).order_by(Preset.engine, Preset.name)).all()
        return [preset_to_dict(row) for row in rows]

    @app.get("/api/v1/presets/shared", tags=["settings"])
    def list_shared_presets(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        """Only deliberately shared presets may cross into Nextcloud user settings."""
        rows = session.scalars(
            select(Preset).where(Preset.is_shared.is_(True)).order_by(Preset.engine, Preset.name)
        ).all()
        return [preset_to_dict(row) for row in rows]

    @app.post("/api/v1/presets", status_code=201, tags=["settings"])
    def create_preset(payload: PresetInput, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        try:
            settings_value = validate_engine_settings(payload.engine, payload.settings, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        duplicate = session.scalar(
            select(Preset.id).where(Preset.engine == payload.engine, Preset.name == payload.name)
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A preset with this name already exists for the selected engine")
        if payload.is_shared:
            validate_shared_preset_settings(settings_value)
        row = Preset(
            name=payload.name,
            engine=payload.engine,
            settings=settings_value,
            description=payload.description,
            is_shared=payload.is_shared,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return preset_to_dict(row)

    @app.put("/api/v1/presets/{preset_id}", tags=["settings"])
    def update_preset(preset_id: str, payload: PresetInput, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        row = session.get(Preset, preset_id)
        if not row:
            raise HTTPException(status_code=404, detail="Preset not found")
        try:
            settings_value = validate_engine_settings(payload.engine, payload.settings, partial=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        duplicate = session.scalar(
            select(Preset.id).where(
                Preset.engine == payload.engine,
                Preset.name == payload.name,
                Preset.id != preset_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A preset with this name already exists for the selected engine")
        if payload.is_shared:
            validate_shared_preset_settings(settings_value)
        row.name = payload.name
        row.engine = payload.engine
        row.settings = settings_value
        row.description = payload.description
        row.is_shared = payload.is_shared
        row.updated_at = utcnow()
        session.commit()
        session.refresh(row)
        return preset_to_dict(row)

    @app.delete("/api/v1/presets/{preset_id}", tags=["settings"])
    def delete_preset(preset_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        row = session.get(Preset, preset_id)
        if not row:
            raise HTTPException(status_code=404, detail="Preset not found")
        session.delete(row)
        session.commit()
        return {"deleted": True, "id": preset_id}

    @app.get("/api/v1/engine-rules", tags=["settings"])
    def list_engine_rules(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        rows = session.scalars(select(EngineRule)).all()
        rows = sorted(rows, key=lambda item: (item.priority, len(item.host_suffix), len(item.path_prefix)), reverse=True)
        return [engine_rule_to_dict(row) for row in rows]

    @app.post("/api/v1/engine-rules", status_code=201, tags=["settings"])
    def create_engine_rule(payload: EngineRuleInput, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        duplicate = session.scalar(
            select(EngineRule.id).where(
                EngineRule.host_suffix == payload.host_suffix,
                EngineRule.path_prefix == payload.path_prefix,
                EngineRule.engine == payload.engine,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="This host/path rule already exists for the selected engine")
        row = EngineRule(
            host_suffix=payload.host_suffix,
            path_prefix=payload.path_prefix,
            engine=payload.engine,
            priority=payload.priority,
            enabled=payload.enabled,
            description=payload.description,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return engine_rule_to_dict(row)

    @app.put("/api/v1/engine-rules/order", tags=["settings"])
    def reorder_engine_rules(
        payload: EngineRuleOrderInput, _: bool = Depends(authenticate),
        session: Session = Depends(db_session),
    ):
        """Atomically assign distinct priorities in display order.

        Reordering always sends every rule ID, including disabled rules.
        Reject stale/partial snapshots before any mutation. Priorities run from
        1000 down to 0; this preserves the existing strict priority-first
        routing matcher without changing the worker or URL matching contract.
        """
        rows = session.scalars(select(EngineRule)).all()
        identities = {rule.id: rule for rule in rows}
        if len(rows) > 1001:
            raise HTTPException(status_code=422, detail="Cannot reorder more than 1001 rules")
        if len(payload.ids) != len(rows) or set(payload.ids) != set(identities):
            raise HTTPException(
                status_code=409,
                detail="The routing table has changed. Reload the rules before reordering.",
            )
        for position, rule_id in enumerate(payload.ids):
            rule = identities[rule_id]
            rule.priority = 1000 - position
            rule.updated_at = utcnow()
        session.commit()
        return [engine_rule_to_dict(identities[rule_id]) for rule_id in payload.ids]

    @app.put("/api/v1/engine-rules/{rule_id}", tags=["settings"])
    def update_engine_rule(rule_id: str, payload: EngineRuleInput, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        row = session.get(EngineRule, rule_id)
        if not row:
            raise HTTPException(status_code=404, detail="Engine rule not found")
        duplicate = session.scalar(
            select(EngineRule.id).where(
                EngineRule.host_suffix == payload.host_suffix,
                EngineRule.path_prefix == payload.path_prefix,
                EngineRule.engine == payload.engine,
                EngineRule.id != rule_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="This host/path rule already exists for the selected engine")
        row.host_suffix = payload.host_suffix
        row.path_prefix = payload.path_prefix
        row.engine = payload.engine
        row.priority = payload.priority
        row.enabled = payload.enabled
        row.description = payload.description
        row.updated_at = utcnow()
        session.commit()
        session.refresh(row)
        return engine_rule_to_dict(row)

    @app.delete("/api/v1/engine-rules/{rule_id}", tags=["settings"])
    def delete_engine_rule(rule_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        row = session.get(EngineRule, rule_id)
        if not row:
            raise HTTPException(status_code=404, detail="Engine rule not found")
        session.delete(row)
        session.commit()
        return {"deleted": True, "id": rule_id}

    @app.post("/api/v1/jobs", status_code=202, response_model=JobOut, tags=["jobs"])
    def submit_job(payload: JobCreate, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = build_job(payload, session, config)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/batch", status_code=202, response_model=JobBatchOut, tags=["jobs"])
    def submit_batch(payload: JobBatchCreate, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        jobs = []
        for index, item in enumerate(payload.items):
            try:
                jobs.append(build_job(item, session, config))
            except HTTPException as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"item": index + 1, "url": item.url, "error": exc.detail},
                ) from exc
        session.add_all(jobs)
        session.commit()
        for job in jobs:
            session.refresh(job)
        return {"created": len(jobs), "jobs": [job_to_dict(job) for job in jobs]}

    @app.get("/api/v1/jobs", response_model=list[JobOut], tags=["jobs"])
    def list_jobs(
        _: bool = Depends(authenticate), session: Session = Depends(db_session),
        limit: int = 50, status: str | None = None, owner_id: str | None = None,
        state: str = "all", q: str | None = None, offset: int = 0,
        standalone_only: bool = False,
    ):
        if not 0 <= offset <= 10000 or not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="Limit must be 1–200 and offset 0–10000")
        query = select(Job)
        if status:
            if status not in JobStatus._value2member_map_:
                raise HTTPException(status_code=422, detail="Unknown job status")
            query = query.where(Job.status == status)
        if state not in {"all", "active", "delivered", "failed"}:
            raise HTTPException(status_code=422, detail="Unknown Library state")
        if state == "active":
            query = query.where(Job.status.not_in(tuple(TERMINAL_STATUSES)))
        elif state == "delivered":
            query = query.where(Job.status == "completed")
        elif state == "failed":
            query = query.where(Job.status.in_((JobStatus.FAILED, JobStatus.CANCELLED)))
        if owner_id is not None:
            query = query.where(Job.owner_id == owner_id)
        if standalone_only:
            query = query.where(Job.owner_id.is_(None))
        if q is not None:
            searched = q.strip()
            if len(searched) > 128:
                raise HTTPException(status_code=422, detail="Search text must be at most 128 characters")
            if searched:
                escaped = searched.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                query = query.where(or_(
                    Job.url.ilike(pattern, escape="\\"),
                    cast(Job.output_files, Text).ilike(pattern, escape="\\"),
                ))
        query = query.order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit)
        return [job_to_dict(job) for job in session.scalars(query).all()]

    @app.post("/api/v1/jobs/retry-failed", tags=["jobs"])
    def retry_all_failed(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        """Retry failed standalone jobs only; never alter Nextcloud-owned jobs."""
        query = select(Job).where(
            Job.owner_id.is_(None), Job.status.in_(("failed", "cancelled"))
        ).order_by(Job.created_at.desc()).limit(1001)
        failed = session.scalars(query).all()
        if len(failed) > 1000:
            raise HTTPException(status_code=422, detail="More than 1000 failed jobs; retry in smaller batches")
        for job in failed:
            requeue_job(job, reason="Job queued for retry from Library")
        session.commit()
        return {"retried": len(failed)}

    @app.delete("/api/v1/jobs/history", tags=["jobs"])
    def clear_standalone_history(
        confirm: bool = False, _: bool = Depends(authenticate),
        session: Session = Depends(db_session),
    ):
        """Forget terminal standalone job metadata; never delete Files or user provenance."""
        if not confirm:
            raise HTTPException(status_code=422, detail="Confirm clearing standalone history explicitly")
        query = delete(Job).where(
            Job.owner_id.is_(None), Job.status.in_(tuple(TERMINAL_STATUSES))
        )
        result = session.execute(query)
        session.commit()
        return {"deleted": int(result.rowcount or 0), "files_retained": True}

    @app.get("/api/v1/provenance", tags=["jobs"])
    def file_provenance(owner_id: str, file_id: str,
                        _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        """Lookup the original download by Nextcloud's stable file ID and owner.

        Only the trusted gateway can call this route. The gateway takes the
        owner from the signed AppAPI identity, not from the browser.
        """
        if not owner_id or len(owner_id) > 255 or not file_id.isdecimal() or len(file_id) > 32:
            raise HTTPException(status_code=422, detail="Invalid file identity")
        jobs = session.scalars(
            select(Job).where(Job.owner_id == owner_id, Job.status == "completed")
            .order_by(Job.created_at.desc())
        ).all()
        for job in jobs:
            for item in job.output_files or []:
                if item.get("verified") and str(item.get("file_id") or "") == file_id:
                    return {
                        "job_id": job.id,
                        "file_id": file_id,
                        "owner_id": owner_id,
                        "source_url": job.url,
                        "engine": job.engine,
                        "created_at": job.created_at.isoformat(),
                        "filename": item.get("filename", ""),
                    }
        raise HTTPException(status_code=404, detail="This file has no download provenance")


    @app.get("/api/v1/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
    def get_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
    def cancel_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail=f"Cannot cancel a {job.status} job")
        job.cancel_requested = True
        if job.status == "queued":
            transition_job(job, JobStatus.CANCELLED)
            append_log(job, "Queued job cancelled before execution")
        else:
            append_log(job, "Cancellation requested")
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/pause", response_model=JobOut, tags=["jobs"])
    async def pause_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        capability = pause_capability(job)
        if not capability["can_pause"]:
            raise HTTPException(status_code=409, detail=capability["reason"])
        controller = app.state.worker
        if controller is None:
            raise HTTPException(status_code=409, detail="This job has no active engine transfer to pause")
        try:
            await controller.pause_job(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.refresh(job)
        if job.status == JobStatus.DOWNLOADING:
            transition_job(job, JobStatus.PAUSED)
        append_log(job, "Paused current aria2 transfer (same job and GID)")
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/resume", response_model=JobOut, tags=["jobs"])
    async def resume_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        capability = pause_capability(job)
        if not capability["can_resume"]:
            raise HTTPException(status_code=409, detail=capability["reason"])
        controller = app.state.worker
        if controller is None:
            raise HTTPException(status_code=409, detail="This job has no paused engine transfer to resume")
        try:
            await controller.resume_job(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.refresh(job)
        if job.status == JobStatus.PAUSED:
            transition_job(job, JobStatus.DOWNLOADING)
        append_log(job, "Resumed the same aria2 transfer (no restart)")
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/retry", status_code=202, response_model=JobOut, tags=["jobs"])
    def retry_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
        requeue_job(job, reason="Job queued for retry")
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/redeliver", status_code=202, response_model=JobOut, tags=["jobs"])
    def redeliver_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "completed":
            raise HTTPException(status_code=409, detail="Only completed jobs can be re-delivered")

        # The durable manifest proves the engine finished successfully and
        # that the original files have not changed since that success.
        # Never silently download a new copy through this operation.
        workdir = config.data_dir / "jobs" / job.id
        if not load_completed_files(workdir):
            raise HTTPException(
                status_code=409,
                detail="Completed download files are no longer verified or available; submit a new download",
            )
        requeue_job(
            job,
            operation="redeliver",
            reason="Re-delivery queued using verified existing files; earlier destination files are not removed",
        )
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.get("/api/v1/jobs/{job_id}/events", tags=["jobs"])
    async def job_events(job_id: str, request: Request, _: bool = Depends(authenticate)):
        with session_factory() as session:
            if not session.get(Job, job_id):
                raise HTTPException(status_code=404, detail="Job not found")

        async def event_stream():
            previous = None
            while True:
                if await request.is_disconnected():
                    break
                with session_factory() as session:
                    job = session.get(Job, job_id)
                    if not job:
                        break
                    current = job_to_dict(job)
                fingerprint = json.dumps(current, sort_keys=True, default=str)
                if fingerprint != previous:
                    yield f"event: job\ndata: {json.dumps(current, default=str)}\n\n"
                    previous = fingerprint
                if current["status"] in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/delivery", response_model=DeliverySettingsOut, response_model_exclude_unset=True, tags=["delivery"])
    def get_delivery(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        return serialize_delivery(session.get(DeliverySetting, 1))

    @app.put("/api/v1/delivery", response_model=DeliverySettingsOut, response_model_exclude_unset=True, tags=["delivery"])
    def update_delivery(payload: DeliverySettingsIn, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        try:
            return set_delivery_settings(session, payload, config)
        except DeliveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/delivery/test", response_model=DeliveryTestOut, tags=["delivery"])
    async def test_delivery(_: bool = Depends(authenticate)):
        try:
            return await verify_delivery_destination(session_factory, config)
        except DeliveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml():
        if not OPENAPI_FILE.is_file():
            raise HTTPException(status_code=404, detail="OpenAPI contract file is missing")
        return FileResponse(OPENAPI_FILE, media_type="application/yaml")

    @app.get("/api-reference", include_in_schema=False)
    def api_reference():
        reference = WEB_DIST / "api-reference.html"
        if reference.is_file():
            return FileResponse(reference, media_type="text/html")
        return HTMLResponse(
            "<h1>Downloader Server API Reference</h1>"
            "<p>The local Scalar UI build is not present. "
            "The OpenAPI contract remains available at <a href='/openapi.yaml'>/openapi.yaml</a>.</p>"
        )

    @app.get("/", include_in_schema=False)
    def index():
        index_file = WEB_DIST / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Downloader Server</h1><p>Web UI build is not present.</p>")

    if WEB_DIST.is_dir():
        from fastapi.staticfiles import StaticFiles
        assets = WEB_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app


def secrets_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _require_engine(engine_name: str) -> None:
    if engine_name not in ENGINE_DEFAULTS:
        raise HTTPException(status_code=404, detail="Engine not found")


def validate_engine_settings(engine: str, values: dict, partial: bool = False) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Engine settings must be an object")
    if engine == "yt-dlp":
        allowed = {"format", "subtitles_lang", "write_subtitles", "embed_subtitles", "extra_args", "cookies_file", "allow_playlist", "playlist_items"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown yt-dlp setting(s): {', '.join(sorted(unknown))}")
        result = {}
        for key, value in values.items():
            if key in {"format", "subtitles_lang", "cookies_file"}:
                if not isinstance(value, str) or len(value) > (256 if key == "format" else 512):
                    raise ValueError(f"Invalid value for {key}")
                if key == "cookies_file" and value and (Path(value).name != value or value in {".", ".."} or "\\" in value):
                    raise ValueError("cookies_file must be a filename inside the service cookies directory")
                result[key] = value
            elif key in {"write_subtitles", "embed_subtitles", "allow_playlist"}:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
                result[key] = value
            elif key == "extra_args":
                from app.security import validate_extra_args
                result[key] = validate_extra_args(value)
            elif key == "playlist_items":
                if not isinstance(value, str) or len(value) > 512 or (
                    value and not re.fullmatch(r"[1-9]\d{0,3}(?:-[1-9]\d{0,3})?(?:,[1-9]\d{0,3}(?:-[1-9]\d{0,3})?)*", value)
                ):
                    raise ValueError("playlist_items must be comma-separated indices or index ranges")
                result[key] = value
        return result
    if engine == "get-sauce":
        from app.adapters.get_sauce import effective_settings
        validated = effective_settings(ENGINE_DEFAULTS["get-sauce"], values)
        return {key: validated[key] for key in values}
    if engine == "svtplay-dl":
        allowed = {"resolution", "quality", "subtitles", "all_subtitles", "all_episodes", "output_format"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown svtplay-dl setting(s): {', '.join(sorted(unknown))}")
        from app.adapters.svtplay import effective_settings
        validated = effective_settings(ENGINE_DEFAULTS["svtplay-dl"], values)
        return {key: validated[key] for key in values}
    if engine == "you-get":
        allowed = {"format", "cookies_file", "no_proxy"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown you-get setting(s): {', '.join(sorted(unknown))}")
        from app.adapters.you_get import effective_settings
        validated = effective_settings(ENGINE_DEFAULTS["you-get"], values)
        return {key: validated[key] for key in values}
    if engine == "generic":
        allowed = {"filename", "follow_redirects", "max_redirects", "retry_count", "user_agent"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown generic setting(s): {', '.join(sorted(unknown))}")
        from app.adapters.generic import effective_settings
        defaults = ENGINE_DEFAULTS["generic"]
        validated = effective_settings(defaults, values)
        return {key: validated[key] for key in values}
    if engine == "gallery-dl":
        allowed = {"options", "cookies_file", "proxy"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown gallery-dl setting(s): {', '.join(sorted(unknown))}")
        result = {}
        if "options" in values:
            options = values["options"]
            if not isinstance(options, dict) or len(options) > 100:
                raise ValueError("gallery-dl options must be an object with at most 100 entries")
            blocked = {
                "output.directory", "output.base-directory", "output.mode",
                "output.shorten", "extractor.base-directory",
            }
            normalized = {}
            for key, value in options.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", key):
                    raise ValueError(f"Invalid gallery-dl option name: {key!r}")
                if key in blocked:
                    raise ValueError(f"gallery-dl option {key} is managed by the service")
                if any(part in key.lower() for part in ("password", "token", "secret", "api-key", "cookie")):
                    raise ValueError(f"Sensitive gallery-dl option {key} cannot be stored as plain text; use an uploaded cookie file")
                if isinstance(value, (dict, list)) or value is None or len(str(value)) > 2048 or "\x00" in str(value):
                    raise ValueError(f"Invalid gallery-dl option value: {key}")
                if key.rsplit(".", 1)[-1] in {"filename", "directory"} and any(
                    marker in str(value) for marker in ("..", "/", "\\")
                ):
                    raise ValueError("gallery-dl filename/directory templates cannot contain path separators or parent traversal")
                normalized[key] = str(value)
            result["options"] = normalized
        for key in ("cookies_file", "proxy"):
            if key in values:
                value = values[key]
                if not isinstance(value, str) or len(value) > 2048 or "\x00" in value:
                    raise ValueError(f"Invalid gallery-dl {key}")
                if key == "cookies_file" and value and (
                    Path(value).name != value or value in {".", ".."} or "\\" in value
                ):
                    raise ValueError("cookies_file must be a filename inside the service cookies directory")
                if key == "proxy" and value:
                    parsed_proxy = urlsplit(value)
                    if parsed_proxy.scheme not in {"http", "https", "socks4", "socks5", "socks5h"} or not parsed_proxy.hostname or parsed_proxy.username or parsed_proxy.password:
                        raise ValueError("gallery-dl proxy must be an HTTP(S)/SOCKS URL without embedded credentials")
                result[key] = value
        return result
    if engine == "aria2c":
        unknown = set(values) - {"options"}
        if unknown:
            raise ValueError(f"Unknown aria2c setting(s): {', '.join(sorted(unknown))}")
        from app.adapters.aria2 import ARIA2_OPTION_ALLOWLIST
        options = values.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("aria2c options must be an object")
        for key, value in options.items():
            if key not in ARIA2_OPTION_ALLOWLIST:
                raise ValueError(f"aria2c option is not allowed: {key}")
            if len(str(value)) > 1024:
                raise ValueError(f"aria2c option value is too long: {key}")
        return {"options": {str(k): str(v) for k, v in options.items()}} if "options" in values else {}
    raise ValueError("Unsupported engine")


def validate_shared_preset_settings(settings: dict) -> None:
    """Public-to-entitled users: never expose cookies, proxy URLs or CLI flags."""
    for name in ("cookies_file", "proxy", "extra_args"):
        if settings.get(name):
            raise HTTPException(
                status_code=422,
                detail="Shared presets cannot contain cookies, proxies or custom arguments",
            )
    options = settings.get("options", {})
    if isinstance(options, dict) and any(
        marker in str(name).lower()
        for name in options
        for marker in ("auth", "cookie", "token", "secret", "password", "header", "referer", "proxy")
    ):
        raise HTTPException(
            status_code=422,
            detail="Shared presets cannot contain private site access options",
        )


def preset_to_dict(row: Preset) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "engine": row.engine,
        "settings": row.settings or {},
        "description": row.description,
        "is_shared": bool(row.is_shared),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def engine_rule_to_dict(row: EngineRule) -> dict:
    return {
        "id": row.id,
        "host_suffix": row.host_suffix,
        "path_prefix": row.path_prefix,
        "engine": row.engine,
        "priority": row.priority,
        "enabled": row.enabled,
        "description": row.description,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _merge_settings(base: dict, override: dict) -> dict:
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_settings(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_job_engine(url: str, requested: str, session: Session, preset: Preset | None) -> str:
    if requested != "auto":
        return requested
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme == "magnet" or scheme in {"ftp", "ftps"}:
        return "aria2c"
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path or "/"
    matches = []
    if host:
        for rule in session.scalars(select(EngineRule).where(EngineRule.enabled.is_(True))).all():
            suffix_match = host == rule.host_suffix or host.endswith("." + rule.host_suffix)
            prefix = rule.path_prefix.rstrip("/") or "/"
            path_match = prefix == "/" or path == prefix or path.startswith(prefix + "/")
            if suffix_match and path_match:
                matches.append(rule)
    if matches:
        selected = max(
            matches,
            key=lambda rule: (rule.priority, len(rule.host_suffix), len(rule.path_prefix)),
        )
        return selected.engine
    if preset is not None:
        return preset.engine
    return "yt-dlp"


def build_job(payload: JobCreate, session: Session, config: Settings) -> Job:
    preset = None
    if payload.preset_id:
        preset = session.get(Preset, payload.preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="Selected preset was not found")

    engine = _resolve_job_engine(payload.url, payload.engine, session, preset)
    if preset is not None and preset.engine != engine:
        raise HTTPException(
            status_code=422,
            detail=f"Selected preset uses {preset.engine}, but URL routing selected {engine}",
        )

    try:
        url = validate_target_url(payload.url, engine, config.allow_private_targets)
        preset_settings = validate_engine_settings(engine, preset.settings, partial=True) if preset else {}
        job_overrides = validate_engine_settings(engine, payload.overrides, partial=True)
        overrides = _merge_settings(preset_settings, job_overrides)
        if engine == "yt-dlp":
            allow_playlist = overrides.get("allow_playlist")
            if allow_playlist is None:
                saved = session.get(EngineSetting, "yt-dlp")
                allow_playlist = bool((saved.settings if saved else {}).get("allow_playlist", False))
            if overrides.get("playlist_items") and not allow_playlist:
                raise ValueError("playlist_items requires allow_playlist=true")
    except (UnsafeUrlError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    encrypted_delivery = None
    if payload.owner_id:
        if payload.delivery_subfolder is not None:
            raise HTTPException(
                status_code=422,
                detail="Nextcloud user destinations are selected by the trusted gateway",
            )
        if payload.delivery is None:
            raise HTTPException(status_code=422, detail="Per-user jobs require a WebDAV delivery destination")
        try:
            delivery = DeliverySettingsIn.model_validate(payload.delivery)
            encrypted_delivery = encrypt_job_delivery(delivery, payload.owner_id, config)
        except (ValueError, DeliveryError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif payload.delivery is not None:
        raise HTTPException(status_code=422, detail="Per-job WebDAV delivery requires an owner_id")
    return Job(
        url=url,
        owner_id=payload.owner_id,
        delivery_token=encrypted_delivery,
        delivery_subfolder=payload.delivery_subfolder,
        engine=engine,
        status="queued",
        overrides=overrides,
        progress={"state": "queued"},
    )


def pause_capability(job: Job) -> dict[str, Any]:
    """Per-job reversible action contract; other engines must never fake pause."""
    status = str(job.status)
    if job.engine != "aria2c":
        reason = f"{job.engine} cannot pause this job without restarting the download"
    elif status == JobStatus.DOWNLOADING:
        reason = None
    elif status == JobStatus.PAUSED:
        reason = None
    else:
        reason = f"This aria2c job is {status}; pause is available during active transfer"
    return {
        "can_pause": job.engine == "aria2c" and status == JobStatus.DOWNLOADING,
        "can_resume": job.engine == "aria2c" and status == JobStatus.PAUSED,
        "reason": reason,
    }


def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "url": job.url,
        "owner_id": job.owner_id,
        "delivery_subfolder": job.delivery_subfolder,
        "engine": job.engine,
        "status": job.status,
        "pause_capability": pause_capability(job),
        "overrides": job.overrides or {},
        "progress": job.progress or {},
        "output_files": job.output_files or [],
        "log_text": job.log_text,
        "error": job.error,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


app = create_app()
