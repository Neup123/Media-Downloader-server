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
            xml = f"<d:multistatus xmlns:d='DAV:'><d:response><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop></d:propstat></d:response></d:multistatus>"
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
