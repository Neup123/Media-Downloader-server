from pathlib import Path

from app.db import Job, make_engine, make_session_factory, initialize_database
from app.worker import JobWorker
from app.config import Settings


def test_recovery_requeues_jobs_that_were_active(settings, tmp_path):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        job = Job(url="https://example.com/movie", engine="yt-dlp", status="downloading")
        session.add(job)
        session.commit()
        job_id = job.id
    worker = JobWorker(sessions, settings)
    worker._recover_jobs()
    with sessions() as session:
        recovered = session.get(Job, job_id)
        assert recovered.status == "queued"
        assert "returned to the queue" in recovered.log_text
    engine.dispose()


def test_local_delivery_copies_and_verifies_file(settings):
    import asyncio
    from app.delivery import deliver_files
    from app.db import make_engine, make_session_factory, initialize_database

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    source = settings.data_dir / "jobs" / "job-1" / "sample.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"verified output bytes")
    delivered = asyncio.run(deliver_files([source], "job-1", sessions, settings))
    assert len(delivered) == 1
    assert delivered[0]["verified"] is True
    assert Path(delivered[0]["path"]).read_bytes() == b"verified output bytes"
    engine.dispose()


def test_webdav_delivery_mkcol_put_and_size_verification(settings):
    import asyncio

    import httpx
    from cryptography.fernet import Fernet

    from app.db import DeliverySetting, make_engine, make_session_factory, initialize_database
    from app.delivery import deliver_files

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    password = "example-app-password"
    encrypted = Fernet(settings.encryption_key.encode("ascii")).encrypt(password.encode()).decode("ascii")
    with sessions() as session:
        session.add(DeliverySetting(
            id=1,
            mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/test-user",
            username="test-user",
            password_encrypted=encrypted,
            folder="Media/Downloads",
        ))
        session.commit()

    source = settings.data_dir / "jobs" / "webdav-job" / "sample.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"webdav payload")
    remote = {}
    methods = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            body = await request.aread()
            remote[str(request.url)] = body
            return httpx.Response(201)
        if request.method == "PROPFIND":
            size = len(remote[str(request.url)])
            xml = f"<d:multistatus xmlns:d='DAV:'><d:response><d:href>{request.url.path}</d:href><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
            return httpx.Response(207, content=xml, headers={"Content-Type": "application/xml"})
        return httpx.Response(405)

    delivered = asyncio.run(deliver_files(
        [source], "webdav-job", sessions, settings, transport=httpx.MockTransport(handler)
    ))
    assert delivered[0]["delivery"] == "webdav"
    assert delivered[0]["verified"] is True
    assert delivered[0]["size_bytes"] == len(b"webdav payload")
    assert methods == ["MKCOL", "MKCOL", "MKCOL", "PUT", "PROPFIND"]
    engine.dispose()


def test_scheduler_applies_per_engine_caps_without_starving_other_engines(settings):
    from types import SimpleNamespace

    settings.max_concurrent_per_engine.update({"yt-dlp": 1, "aria2c": 1})
    worker = JobWorker(lambda: None, settings)
    worker._active_engines["already-running"] = "yt-dlp"

    queued = [
        SimpleNamespace(id="yt-2", engine="yt-dlp"),
        SimpleNamespace(id="aria-1", engine="aria2c"),
        SimpleNamespace(id="yt-3", engine="yt-dlp"),
    ]
    selected = worker._select_queued_jobs(queued, slots=2)
    assert [job.id for job in selected] == ["aria-1"]

    worker._active_engines.clear()
    selected = worker._select_queued_jobs(queued, slots=3)
    assert [job.id for job in selected] == ["yt-2", "aria-1"]


def test_queue_fetch_does_not_starve_eligible_engines_behind_busy_engine(settings):
    from datetime import datetime, timedelta, timezone

    from app.db import get_queued_jobs

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as session:
        session.add_all([
            Job(
                url=f"https://example.com/video/{index}",
                engine="yt-dlp",
                status="queued",
                created_at=start + timedelta(seconds=index),
            )
            for index in range(110)
        ])
        session.add(Job(
            url="https://example.com/direct.bin",
            engine="aria2c",
            status="queued",
            created_at=start + timedelta(seconds=110),
        ))
        session.commit()
    worker = JobWorker(sessions, settings)
    worker._active_engines["running-yt"] = "yt-dlp"
    with sessions() as session:
        # An unfiltered query of the oldest 100 would contain only yt-dlp.
        unfiltered = get_queued_jobs(session, 100)
        assert all(job.engine == "yt-dlp" for job in unfiltered)
        eligible = get_queued_jobs(session, 100, excluded_engines={"yt-dlp"})
        selected = worker._select_queued_jobs(eligible, slots=1)
        assert [job.engine for job in selected] == ["aria2c"]
    engine.dispose()
