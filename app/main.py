from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.discovery import PreviewError, inspect_ytdlp
from app.db import DeliverySetting, EngineSetting, EngineRule, Preset, Job, make_engine, make_session_factory, initialize_database, append_log, utcnow
from app.delivery import DeliveryError, serialize_delivery, set_delivery_settings
from app.schemas import CookieUploadIn, DeliverySettingsIn, EngineSettingsIn, EngineRuleInput, PresetInput, JobBatchCreate, JobCreate, MediaPreviewIn
from app.security import UnsafeUrlError, validate_target_url
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
        version="0.1.0",
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
            return await inspect_ytdlp(url, max_entries=payload.max_entries)
        except (UnsafeUrlError, ValueError, PreviewError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/engines", tags=["engines"])
    def list_engines(_: bool = Depends(authenticate)):
        result = []
        for item in ENGINE_DESCRIPTIONS:
            binary = {"yt-dlp": "yt-dlp", "aria2c": "aria2c", "gallery-dl": "gallery-dl"}[item["name"]]
            result.append({**item, "installed": shutil.which(binary) is not None, "defaults": ENGINE_DEFAULTS[item["name"]]})
        return result

    @app.get("/api/v1/engines/{engine_name}/settings", tags=["engines"])
    def get_engine_settings(engine_name: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        _require_engine(engine_name)
        row = session.get(EngineSetting, engine_name)
        return {"engine": engine_name, "settings": {**ENGINE_DEFAULTS[engine_name], **(row.settings if row else {})}}

    @app.put("/api/v1/engines/{engine_name}/settings", tags=["engines"])
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

    @app.get("/api/v1/cookies", tags=["engines"])
    def list_cookie_files(_: bool = Depends(authenticate)):
        cookie_dir = config.data_dir / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        return [
            {"filename": p.name, "size_bytes": p.stat().st_size}
            for p in sorted(cookie_dir.iterdir())
            if p.is_file() and p.suffix.lower() in {".txt", ".cookies"}
        ]

    @app.put("/api/v1/cookies/{filename}", tags=["engines"])
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
        cookie_dir = config.data_dir / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        target = cookie_dir / filename
        target.write_bytes(content)
        os.chmod(target, 0o600)
        return {"filename": filename, "size_bytes": len(content), "stored": True}

    @app.delete("/api/v1/cookies/{filename}", tags=["engines"])
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
        row = Preset(
            name=payload.name,
            engine=payload.engine,
            settings=settings_value,
            description=payload.description,
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
        row.name = payload.name
        row.engine = payload.engine
        row.settings = settings_value
        row.description = payload.description
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

    @app.post("/api/v1/jobs", status_code=202, tags=["jobs"])
    def submit_job(payload: JobCreate, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = build_job(payload, session, config)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/batch", status_code=202, tags=["jobs"])
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

    @app.get("/api/v1/jobs", tags=["jobs"])
    def list_jobs(_: bool = Depends(authenticate), session: Session = Depends(db_session), limit: int = 50, status: str | None = None):
        limit = max(1, min(200, limit))
        query = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if status:
            if status not in {"queued", "starting", "extracting", "downloading", "post-processing", "delivering", "completed", "failed", "cancelled"}:
                raise HTTPException(status_code=422, detail="Unknown job status")
            query = select(Job).where(Job.status == status).order_by(Job.created_at.desc()).limit(limit)
        return [job_to_dict(job) for job in session.scalars(query).all()]

    @app.get("/api/v1/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/cancel", tags=["jobs"])
    def cancel_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail=f"Cannot cancel a {job.status} job")
        job.cancel_requested = True
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = utcnow()
            append_log(job, "Queued job cancelled before execution")
        else:
            append_log(job, "Cancellation requested")
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

    @app.post("/api/v1/jobs/{job_id}/retry", status_code=202, tags=["jobs"])
    def retry_job(job_id: str, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
        job.status = "queued"
        job.cancel_requested = False
        job.progress = {"state": "queued"}
        job.output_files = []
        job.error = None
        job.finished_at = None
        append_log(job, "Job queued for retry")
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
                if current["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/delivery", tags=["delivery"])
    def get_delivery(_: bool = Depends(authenticate), session: Session = Depends(db_session)):
        return serialize_delivery(session.get(DeliverySetting, 1))

    @app.put("/api/v1/delivery", tags=["delivery"])
    def update_delivery(payload: DeliverySettingsIn, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        try:
            return set_delivery_settings(session, payload, config)
        except DeliveryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml():
        if not OPENAPI_FILE.is_file():
            raise HTTPException(status_code=404, detail="OpenAPI contract file is missing")
        return FileResponse(OPENAPI_FILE, media_type="application/yaml")

    @app.get("/api-reference", response_class=HTMLResponse, include_in_schema=False)
    def api_reference():
        return """<!doctype html><html><head><title>Downloader Server API</title><meta charset='utf-8'></head><body><script id='api-reference' data-url='/openapi.yaml'></script><script src='https://cdn.jsdelivr.net/npm/@scalar/api-reference'></script></body></html>"""

    @app.get("/", include_in_schema=False)
    def index():
        index_file = WEB_DIST / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Downloader Server</h1><p>Web UI build is not present. See <a href='/api-reference'>API reference</a>.</p>")

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
                    value and not re.fullmatch(r"[1-9]\\d{0,3}(?:-[1-9]\\d{0,3})?(?:,[1-9]\\d{0,3}(?:-[1-9]\\d{0,3})?)*", value)
                ):
                    raise ValueError("playlist_items must be comma-separated indices or index ranges")
                result[key] = value
        return result
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


def preset_to_dict(row: Preset) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "engine": row.engine,
        "settings": row.settings or {},
        "description": row.description,
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

    return Job(
        url=url,
        engine=engine,
        status="queued",
        overrides=overrides,
        progress={"state": "queued"},
    )


def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "url": job.url,
        "engine": job.engine,
        "status": job.status,
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
