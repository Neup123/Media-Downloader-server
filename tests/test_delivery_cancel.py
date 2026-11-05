from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import DeliverySetting, Job, initialize_database, make_engine, make_session_factory
from app.delivery import deliver_files
from app.worker import JobWorker


def _files(settings, job_id):
    root = settings.data_dir / "jobs" / job_id
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for index in range(2):
        path = root / f"file-{index}.bin"
        path.write_bytes(bytes([index + 1]) * (1024 * 64))
        files.append(path)
    return files


def test_local_delivery_cancellation_preserves_only_verified_files(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    files = _files(settings, "local-cancel")
    cancelled = False
    completed = []

    async def is_cancelled():
        return cancelled

    async def on_delivered(item):
        nonlocal cancelled
        completed.append(item)
        cancelled = True

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await deliver_files(
                files, "local-cancel", sessions, settings,
                is_cancelled=is_cancelled, on_delivered=on_delivered,
            )

    asyncio.run(run())
    assert len(completed) == 1 and completed[0]["verified"] is True
    assert (settings.output_dir / "local-cancel" / "file-0.bin").exists()
    assert not (settings.output_dir / "local-cancel" / "file-1.bin").exists()
    assert not list((settings.output_dir / "local-cancel").rglob("*.part"))
    engine.dispose()


def test_webdav_cancellation_stops_before_second_upload(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    encrypted = Fernet(settings.encryption_key.encode()).encrypt(b"example").decode()
    with sessions() as session:
        session.add(DeliverySetting(
            id=1, mode="webdav",
            webdav_url="https://cloud.example/remote.php/dav/files/user",
            username="user", password_encrypted=encrypted,
            folder="Downloader",
        ))
        session.commit()
    files = _files(settings, "webdav-cancel")
    cancelled = False
    verified = []
    uploaded = {}

    async def handler(request):
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            uploaded[str(request.url)] = await request.aread()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            size = len(uploaded[str(request.url)])
            return httpx.Response(
                207,
                content=f"<d:multistatus xmlns:d='DAV:'><d:response><d:href>{request.url.path}</d:href><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>",
            )
        return httpx.Response(405)

    async def is_cancelled():
        return cancelled

    async def on_delivered(item):
        nonlocal cancelled
        verified.append(item)
        cancelled = True

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await deliver_files(
                files, "webdav-cancel", sessions, settings,
                transport=httpx.MockTransport(handler),
                is_cancelled=is_cancelled, on_delivered=on_delivered,
            )

    asyncio.run(run())
    assert len(uploaded) == 1
    assert len(verified) == 1 and verified[0]["verified"] is True
    engine.dispose()


def test_worker_marks_job_cancelled_when_delivery_is_interrupted(settings, monkeypatch):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        item = Job(url="https://example.com/video", engine="yt-dlp", status="queued")
        session.add(item)
        session.commit()
        job_id = item.id

    async def completed_download(**kwargs):
        root = kwargs["workdir"]
        output = root / "video.mp4"
        output.write_bytes(b"complete")
        return [output]

    async def cancellation_during_delivery(files, job_id, session_factory, settings, **kwargs):
        await kwargs["on_delivered"]({
            "filename": "video.mp4", "size_bytes": len(b"complete"),
            "path": "https://cloud.example/video.mp4", "delivery": "webdav",
            "verified": True,
        })
        with session_factory() as session:
            job = session.get(Job, job_id)
            job.cancel_requested = True
            session.commit()
        raise asyncio.CancelledError

    monkeypatch.setattr("app.worker.preflight_source_dns", lambda url: None)
    monkeypatch.setattr("app.worker.run_ytdlp", completed_download)
    monkeypatch.setattr("app.worker.deliver_files", cancellation_during_delivery)
    asyncio.run(JobWorker(sessions, settings)._execute(job_id))
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "cancelled"
        assert len(job.output_files) == 1
        assert "some files may already exist" in job.log_text
    engine.dispose()
