"""One-off standalone delivery path acceptance and trusted Nextcloud isolation."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import text

from app.db import DeliverySetting, Job, initialize_database, make_engine, make_session_factory
from app.delivery import deliver_files
from app.schemas import JobCreate


@pytest.mark.parametrize("name", ["../private", "Research/../private", "Research/Sub", "Research\\Sub",
                                    ".", "..", "bad\x00name", "a\nname", "a\rname"])
def test_oneoff_folder_rejects_paths_and_control_characters(name):
    with pytest.raises(ValidationError):
        JobCreate(url="https://example.org/example.zip", delivery_subfolder=name)


def test_oneoff_folder_is_one_safe_name_and_empty_does_not_change_default():
    assert JobCreate(url="https://example.org/example.zip", delivery_subfolder=" Research ").delivery_subfolder == "Research"
    assert JobCreate(url="https://example.org/example.zip", delivery_subfolder="  ").delivery_subfolder is None
    assert JobCreate(url="https://example.org/example.zip", delivery_subfolder=None).delivery_subfolder is None


def _setup(settings, mode):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    if mode == "webdav":
        password = Fernet(settings.encryption_key.encode("ascii")).encrypt(b"private-test-secret").decode()
        with sessions() as session:
            session.add(DeliverySetting(
                id=1, mode="webdav", folder="Downloads",
                webdav_url="https://cloud.example.test/remote.php/dav/files/admin",
                username="admin", password_encrypted=password,
            ))
            session.commit()
    with sessions() as session:
        session.add(Job(
            id="job-research", url="https://example.com/album", engine="generic",
            owner_id=None, status="queued", delivery_subfolder="Research",
        ))
        session.commit()
    file = settings.data_dir / "jobs" / "job-research" / "album" / "photo.jpg"
    file.parent.mkdir(parents=True, exist_ok=True)
    data = b"real-delivery-bytes-" * 800
    file.write_bytes(data)
    return engine, sessions, file, data


def test_local_oneoff_folder_verified_and_regular_destination_unchanged(settings):
    engine, sessions, source, data = _setup(settings, "local")
    try:
        files = asyncio.run(deliver_files([source], "job-research", sessions, settings))
        target = settings.output_dir / "Research" / "job-research" / "album" / "photo.jpg"
        assert target.read_bytes() == data
        assert len(files) == 1 and files[0]["verified"] is True
        assert Path(files[0]["path"]) == target
        assert not (settings.output_dir / "job-research").exists()
    finally:
        engine.dispose()


def test_webdav_oneoff_mkcol_put_and_profind_verified_under_saved_root(settings):
    engine, sessions, source, data = _setup(settings, "webdav")
    written = {}
    created = []
    def handler(request: httpx.Request):
        url = str(request.url)
        assert "private-test-secret" not in url
        if request.method == "MKCOL":
            created.append(url)
            return httpx.Response(201)
        if request.method == "PUT":
            # Memory payload from a real file yielded by the delivery stream.
            # httpx.MockTransport invokes a sync handler: request.read() is supported.
            written[url] = request.read()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            if url not in written:
                return httpx.Response(404)
            return httpx.Response(207, content=(
                "<d:multistatus xmlns:d='DAV:'><d:response><d:href>" + request.url.path +
                "</d:href><d:propstat><d:prop><d:getcontentlength>" +
                str(len(written[url])) +
                "</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                "</d:propstat></d:response></d:multistatus>"
            ))
        return httpx.Response(405)
    try:
        result = asyncio.run(deliver_files(
            [source], "job-research", sessions, settings, transport=httpx.MockTransport(handler),
        ))
        assert len(result) == 1 and result[0]["verified"] is True
        assert written and list(written.values()) == [data]
        assert all("/Downloads/Research/job-research/" in url for url in written)
        assert any("/Downloads/Research" in url for url in created)
        assert all("/Downloads/Research/job-research/" in file["path"] for file in result)
        assert all("private-test-secret" not in str(file) for file in result)
    finally:
        engine.dispose()


def test_api_persists_folder_but_rejects_per_user_gateway_override(client, auth_headers):
    posted = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/movie.mp4", "engine": "generic",
        "delivery_subfolder": "Research",
    })
    assert posted.status_code == 202, posted.text
    assert posted.json()["delivery_subfolder"] == "Research"
    loaded = client.get("/api/v1/jobs/" + posted.json()["id"], headers=auth_headers)
    assert loaded.json()["delivery_subfolder"] == "Research"
    rejected = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/movie.mp4", "engine": "generic",
        "owner_id": "alice", "delivery_subfolder": "Research",
        "delivery": {
            "mode": "webdav", "webdav_url": "https://example.org/remote.php/dav/files/alice",
            "username": "alice", "app_password": "private", "folder": "Downloads",
        },
    })
    assert rejected.status_code == 422
    assert "trusted gateway" in rejected.text


def test_additive_schema_upgrade_preserves_old_jobs(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'old-jobs.sqlite'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY, url TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO jobs (id, url) VALUES ('legacy', 'https://example.com')"))
        initialize_database(engine)
        initialize_database(engine)
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT id, delivery_subfolder FROM jobs WHERE id='legacy'"
            )).one()
        assert tuple(row) == ("legacy", None)
    finally:
        engine.dispose()
