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
from app.db import DeliverySetting, EngineSetting, Job, make_engine, make_session_factory, initialize_database, append_log, utcnow
from app.delivery import DeliveryError, serialize_delivery, set_delivery_settings
from app.schemas import CookieUploadIn, DeliverySettingsIn, EngineSettingsIn, JobCreate
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

    @app.post("/api/v1/jobs", status_code=202, tags=["jobs"])
    def submit_job(payload: JobCreate, _: bool = Depends(authenticate), session: Session = Depends(db_session)):
        try:
            url = validate_target_url(payload.url, payload.engine, config.allow_private_targets)
            overrides = validate_engine_settings(payload.engine, payload.overrides, partial=True)
        except (UnsafeUrlError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = Job(url=url, engine=payload.engine, status="queued", overrides=overrides, progress={"state": "queued"})
        session.add(job)
        session.commit()
        session.refresh(job)
        return job_to_dict(job)

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
        allowed = {"format", "subtitles_lang", "write_subtitles", "embed_subtitles", "extra_args", "cookies_file"}
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
            elif key in {"write_subtitles", "embed_subtitles"}:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
                result[key] = value
            elif key == "extra_args":
                from app.security import validate_extra_args
                result[key] = validate_extra_args(value)
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
