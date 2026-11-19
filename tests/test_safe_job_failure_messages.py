"""Engine error output must never be rendered in the user-facing job record."""
from __future__ import annotations

import asyncio

import pytest

from app.delivery import DeliveryError
from app.db import Job
from app.user_failures import user_failure_message
from app.worker import JobWorker


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("yt-dlp exited with status 1; token=do-not-show"),
     "The download failed. Check the technical log or try again."),
    (ValueError("https://example.org/private?signature=do-not-show"),
     "The download failed. Check the technical log or try again."),
    (DeliveryError("Nextcloud storage quota exceeded; additional private text"),
     "Nextcloud Files quota exceeded. Free space and retry."),
    (DeliveryError("Nextcloud destination is not writable; credentials=private"),
     "Destination folder is not writable. Choose a writable folder."),
    (DeliveryError("Nextcloud destination folder is missing; token=private"),
     "Destination folder is missing. Choose an existing folder."),
    (DeliveryError("WebDAV PUT failed for signed.mp4 (503); token=private"),
     "Could not verify file delivery. Check the destination and retry."),
    (TimeoutError("origin=https://private.example/media"),
     "The download took too long. Try again."),
])
def test_user_message_contains_no_untrusted_error_text(error, expected):
    result = user_failure_message(error)
    assert result == expected
    assert "do-not-show" not in result
    assert "token" not in result and "private.example" not in result


def test_engine_failure_persists_safe_message_and_advanced_log_only(settings, monkeypatch):
    engine = settings.database_url
    from app.db import make_engine, initialize_database, make_session_factory
    database = make_engine(engine)
    initialize_database(database)
    sessions = make_session_factory(database)
    with sessions() as session:
        job = Job(url="https://example.org/source", engine="yt-dlp", status="queued")
        session.add(job)
        session.commit()
        identifier = job.id
    async def broken_engine(**_kwargs):
        raise RuntimeError("yt-dlp exited with status 1; token=hidden-from-users")
    monkeypatch.setattr("app.worker.run_ytdlp", broken_engine)
    monkeypatch.setattr("app.worker.preflight_source_dns", lambda _: None)
    worker = JobWorker(sessions, settings)
    asyncio.run(worker._execute(identifier))
    with sessions() as session:
        failed=session.get(Job, identifier)
        assert failed.status=="failed"
        assert failed.error == "The download failed. Check the technical log or try again."
        assert "yt-dlp exited with status 1" not in failed.error
        assert "token=hidden-from-users" not in failed.log_text
        assert "yt-dlp exited with status 1" in failed.log_text
    database.dispose()
