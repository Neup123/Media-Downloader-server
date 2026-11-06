from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import DeliverySetting, initialize_database, make_engine, make_session_factory
from app.delivery import DeliveryError, deliver_files


def _two_album_files(settings, job_id: str):
    root = settings.data_dir / "jobs" / job_id
    first = root / "album-a" / "image.jpg"
    second = root / "album-b" / "image.jpg"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"image A")
    second.write_bytes(b"image B with distinct bytes")
    return first, second


def test_local_delivery_preserves_two_images_with_same_basename(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    first, second = _two_album_files(settings, "gallery-job")
    output = asyncio.run(deliver_files([first, second], "gallery-job", sessions, settings))
    assert len(output) == 2
    assert all(item["verified"] for item in output)
    assert (settings.output_dir / "gallery-job" / "album-a" / "image.jpg").read_bytes() == b"image A"
    assert (settings.output_dir / "gallery-job" / "album-b" / "image.jpg").read_bytes() == b"image B with distinct bytes"
    assert output[0]["path"] != output[1]["path"]
    engine.dispose()


def test_webdav_delivery_preserves_album_paths_without_overwriting(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    encrypted = Fernet(settings.encryption_key.encode("ascii")).encrypt(b"test-secret").decode("ascii")
    with sessions() as session:
        session.add(DeliverySetting(
            id=1,
            mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/u",
            username="u",
            password_encrypted=encrypted,
            folder="Downloader",
        ))
        session.commit()

    files = _two_album_files(settings, "gallery-job")
    saved = {}

    async def handler(request: httpx.Request):
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            saved[str(request.url)] = await request.aread()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            size = len(saved[str(request.url)])
            return httpx.Response(
                207,
                content=f"<d:multistatus xmlns:d='DAV:'><d:response><d:href>{request.url.path}</d:href><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>",
            )
        return httpx.Response(405)

    output = asyncio.run(deliver_files(
        list(files), "gallery-job", sessions, settings, transport=httpx.MockTransport(handler)
    ))
    assert len(saved) == 2
    assert any("/gallery-job/album-a/image.jpg" in url and data == b"image A" for url, data in saved.items())
    assert any("/gallery-job/album-b/image.jpg" in url and data == b"image B with distinct bytes" for url, data in saved.items())
    assert all(item["verified"] for item in output)
    assert output[0]["path"] != output[1]["path"]
    engine.dispose()


def test_delivery_rejects_a_file_outside_the_job_workspace(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    outside = settings.data_dir / "other-file.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("not an engine output")
    with pytest.raises(DeliveryError, match="outside its job directory"):
        asyncio.run(deliver_files([outside], "gallery-job", sessions, settings))
    engine.dispose()


@pytest.mark.parametrize("folder", ["../private", "Downloads/../../private", "./Downloads", "Downloads/./nested"])
def test_webdav_delivery_rejects_dot_segment_folder(settings, folder):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    encrypted = Fernet(settings.encryption_key.encode("ascii")).encrypt(b"test-secret").decode("ascii")
    with sessions() as session:
        session.add(DeliverySetting(
            id=1,
            mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/u",
            username="u",
            password_encrypted=encrypted,
            folder=folder,
        ))
        session.commit()
    source = _two_album_files(settings, "gallery-job")[0]
    requests = []

    def handler(request: httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(201)

    with pytest.raises(DeliveryError, match="dot segments"):
        asyncio.run(deliver_files(
            [source], "gallery-job", sessions, settings,
            transport=httpx.MockTransport(handler),
        ))
    assert requests == []
    engine.dispose()


def test_per_user_webdav_ignores_global_delivery_and_uses_own_files(settings):
    from app.db import Job
    from app.schemas import DeliverySettingsIn
    from app.delivery import encrypt_job_delivery
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    files = {}
    for owner in ("alice", "bob"):
        payload = DeliverySettingsIn(mode="webdav",
            webdav_url=f"https://cloud.example.test/remote.php/dav/files/{owner}",
            username=owner, app_password=f"secret-{owner}", folder="Downloads")
        files[owner] = _two_album_files(settings, f"job-{owner}")[0]
        with sessions() as session:
            session.add(Job(id=f"job-{owner}", url="https://example.com/x", engine="generic",
                status="queued", owner_id=owner,
                delivery_token=encrypt_job_delivery(payload, owner, settings)))
            session.commit()

    seen = {}
    def handler(request: httpx.Request):
        user = "alice" if "/files/alice/" in str(request.url) else "bob"
        assert request.headers.get("authorization") is not None
        seen.setdefault(user, []).append((request.method, str(request.url)))
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201)
        if request.method == "PROPFIND":
            path = request.url.path
            if path.endswith("/Downloads/"):
                xml = f"<d:multistatus xmlns:d='DAV:'><d:response><d:href>{path}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
            else:
                size = files[user].stat().st_size
                xml = f"<d:multistatus xmlns:d='DAV:'><d:response><d:href>{path}</d:href><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
            return httpx.Response(207, content=xml)
        return httpx.Response(500)

    for owner in ("alice", "bob"):
        result = asyncio.run(deliver_files([files[owner]], f"job-{owner}", sessions, settings,
            transport=httpx.MockTransport(handler)))
        assert len(result) == 1
        assert f"/files/{owner}/Downloads/job-{owner}/" in result[0]["path"]
    assert seen["alice"] and seen["bob"]
    assert all("/files/bob/" not in url for _, url in seen["alice"])
    assert all("/files/alice/" not in url for _, url in seen["bob"])
    engine.dispose()


@pytest.mark.parametrize(("status", "reason"), [
    (507, "quota"), (403, "not writable"), (404, "missing"),
])
def test_per_user_webdav_reports_actionable_delivery_error(settings, status, reason):
    from app.db import Job
    from app.schemas import DeliverySettingsIn
    from app.delivery import encrypt_job_delivery
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    payload = DeliverySettingsIn(mode="webdav",
        webdav_url="https://cloud.example.test/remote.php/dav/files/alice",
        username="alice", app_password="secret", folder="Downloads")
    with sessions() as session:
        session.add(Job(id="job-alice", url="https://example.com/x", engine="generic",
            status="queued", owner_id="alice",
            delivery_token=encrypt_job_delivery(payload, "alice", settings)))
        session.commit()
    source = _two_album_files(settings, "job-alice")[0]
    def handler(request: httpx.Request):
        return httpx.Response(status)
    with pytest.raises(DeliveryError, match=reason):
        asyncio.run(deliver_files([source], "job-alice", sessions, settings,
            transport=httpx.MockTransport(handler)))
    engine.dispose()


def test_initialize_database_upgrades_existing_jobs_additively(tmp_path):
    from sqlalchemy import text
    from app.db import Job
    url = f"sqlite:///{tmp_path / 'legacy.sqlite'}"
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY, url TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO jobs (id, url) VALUES ('legacy', 'https://example.com')"))
    initialize_database(engine)
    initialize_database(engine)
    with engine.begin() as connection:
        row = connection.execute(text("SELECT id, owner_id, delivery_token FROM jobs WHERE id='legacy'")).one()
    assert tuple(row) == ("legacy", None, None)
    engine.dispose()
