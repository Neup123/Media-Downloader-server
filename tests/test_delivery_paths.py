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
                content=f"<d:multistatus xmlns:d='DAV:'><d:response><d:propstat><d:prop><d:getcontentlength>{size}</d:getcontentlength></d:prop></d:propstat></d:response></d:multistatus>",
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
