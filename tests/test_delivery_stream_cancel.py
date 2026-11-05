"""Regression tests for cancellation *inside* a file transfer, not only between files."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import DeliverySetting, initialize_database, make_engine, make_session_factory
from app.delivery import deliver_files


def _sessions(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    return engine, make_session_factory(engine)


def _large_file(settings, job_id="mid-transfer"):
    output = settings.data_dir / "jobs" / job_id / "large.bin"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"x" * (3 * 1024 * 1024 + 17))
    return output


def test_local_copy_cancelled_mid_file_never_publishes_partial_file(settings):
    engine, sessions = _sessions(settings)
    source = _large_file(settings)
    checks = 0

    async def is_cancelled():
        nonlocal checks
        checks += 1
        # Cancel after transfer of at least one 1 MiB chunk.
        return checks >= 4

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await deliver_files([source], "mid-transfer", sessions, settings,
                                is_cancelled=is_cancelled)

    try:
        asyncio.run(run())
        destination = settings.output_dir / "mid-transfer"
        assert checks >= 4
        assert not (destination / "large.bin").exists()
        assert not list(destination.glob(".downloader-*.part"))
    finally:
        engine.dispose()


def test_webdav_upload_cancelled_during_request_stream(settings):
    engine, sessions = _sessions(settings)
    source = _large_file(settings)
    password = Fernet(settings.encryption_key.encode()).encrypt(b"test-app-password").decode()
    with sessions() as session:
        session.add(DeliverySetting(
            id=1, mode="webdav",
            webdav_url="https://cloud.example/remote.php/dav/files/user",
            username="user", password_encrypted=password, folder="Downloader",
        ))
        session.commit()

    cancelled = False
    chunks_seen = []
    published = []

    async def is_cancelled():
        return cancelled

    async def on_delivered(item):
        published.append(item)

    async def handler(request: httpx.Request):
        nonlocal cancelled
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            async for chunk in request.stream:
                chunks_seen.append(len(chunk))
                cancelled = True
                await asyncio.sleep(0)
            return httpx.Response(201)
        raise AssertionError("Cancelled upload should never reach metadata verification")

    # MockTransport consumes the request body eagerly before its handler runs.
    # Use a streaming transport so the cancellation occurs in the uploader's
    # async iterator, before the second chunk is read.
    class StreamingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await handler(request)

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await deliver_files(
                [source], "mid-transfer", sessions, settings,
                transport=StreamingTransport(),
                is_cancelled=is_cancelled, on_delivered=on_delivered,
            )

    try:
        asyncio.run(run())
        assert chunks_seen == [1024 * 1024]
        assert published == []
    finally:
        engine.dispose()
