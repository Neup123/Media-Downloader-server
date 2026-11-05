"""Local delivery must not publish a same-size corrupted or changing copy."""
from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import contextmanager

import pytest

from app.db import initialize_database, make_engine, make_session_factory
from app.delivery import DeliveryError, deliver_files


def _prepared_delivery(settings, job_id: str):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    source = settings.data_dir / "jobs" / job_id / "payload.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"correct original payload")
    return engine, make_session_factory(engine), source


def _assert_unpublished(settings, job_id: str):
    folder = settings.output_dir / job_id
    assert not (folder / "payload.bin").exists()
    assert not list(folder.glob(".downloader-*.part"))


def test_local_copy_rejects_same_size_corruption_before_atomic_publish(settings, monkeypatch):
    engine, sessions, source = _prepared_delivery(settings, "corrupt-copy")
    real_named_temporary_file = tempfile.NamedTemporaryFile
    reported = []

    @contextmanager
    def corrupt_after_copy(*args, **kwargs):
        with real_named_temporary_file(*args, **kwargs) as handle:
            path = handle.name
            yield handle
        # Model a storage fault after a complete write: length stays the same.
        with open(path, "r+b") as stream:
            stream.write(b"X")

    monkeypatch.setattr("app.delivery.tempfile.NamedTemporaryFile", corrupt_after_copy)

    async def on_delivered(item):
        reported.append(item)

    try:
        with pytest.raises(DeliveryError, match="checksum verification failed"):
            asyncio.run(deliver_files(
                [source], "corrupt-copy", sessions, settings,
                on_delivered=on_delivered,
            ))
        _assert_unpublished(settings, "corrupt-copy")
        assert reported == []
        assert source.read_bytes() == b"correct original payload"
    finally:
        engine.dispose()


def test_local_copy_rejects_source_changed_during_transfer(settings, monkeypatch):
    engine, sessions, source = _prepared_delivery(settings, "changed-source")
    real_named_temporary_file = tempfile.NamedTemporaryFile
    before = source.stat()

    @contextmanager
    def change_source_after_copy(*args, **kwargs):
        with real_named_temporary_file(*args, **kwargs) as handle:
            yield handle
        # Keep the file length, but make the source stat change unambiguous.
        source.write_bytes(b"modified original payload")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr("app.delivery.tempfile.NamedTemporaryFile", change_source_after_copy)

    try:
        with pytest.raises(DeliveryError, match="Source changed during local delivery"):
            asyncio.run(deliver_files([source], "changed-source", sessions, settings))
        _assert_unpublished(settings, "changed-source")
    finally:
        engine.dispose()
