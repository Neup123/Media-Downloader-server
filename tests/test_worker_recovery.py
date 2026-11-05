from __future__ import annotations

import asyncio

from app.completion import save_completed_files
from app.db import Job, initialize_database, make_engine, make_session_factory
from app.worker import JobWorker


def _new_worker(settings, status="queued", cancel_requested=False):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        job = Job(
            url="https://example.com/video",
            engine="yt-dlp",
            status=status,
            cancel_requested=cancel_requested,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    return engine, sessions, job_id, JobWorker(sessions, settings)


def test_recovery_clears_stale_queued_shutdown_cancellation(settings):
    engine, sessions, job_id, worker = _new_worker(
        settings, status="queued", cancel_requested=True
    )
    worker._recover_jobs()
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued"
        assert job.cancel_requested is False
    engine.dispose()


def test_worker_redownloads_instead_of_delivering_unverified_leftover(settings, monkeypatch):
    engine, sessions, job_id, worker = _new_worker(settings)
    workdir = settings.data_dir / "jobs" / job_id
    workdir.mkdir(parents=True)
    (workdir / "video.mp4").write_bytes(b"truncated but ordinary extension")
    calls = []

    async def fake_download(**kwargs):
        calls.append(kwargs["url"])
        assert not (workdir / "video.mp4").exists()
        output = workdir / "video.mp4"
        output.write_bytes(b"real completed media")
        return [output]

    monkeypatch.setattr("app.worker.run_ytdlp", fake_download)
    asyncio.run(worker._execute(job_id))
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "completed", job.error
        assert job.output_files[0]["verified"] is True
        assert job.output_files[0]["size_bytes"] == len(b"real completed media")
    assert calls == ["https://example.com/video"]
    assert (settings.output_dir / job_id / "video.mp4").read_bytes() == b"real completed media"
    engine.dispose()


def test_worker_reuses_manifest_after_delivery_only_failure(settings, monkeypatch):
    engine, sessions, job_id, worker = _new_worker(settings)
    workdir = settings.data_dir / "jobs" / job_id
    workdir.mkdir(parents=True)
    output = workdir / "video.mp4"
    output.write_bytes(b"already downloaded")
    save_completed_files(workdir, [output])

    async def unexpected_download(**kwargs):
        raise AssertionError("Engine must not run when download completion was verified")

    monkeypatch.setattr("app.worker.run_ytdlp", unexpected_download)
    asyncio.run(worker._execute(job_id))
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "completed", job.error
        assert job.output_files[0]["verified"] is True
    assert (settings.output_dir / job_id / "video.mp4").read_bytes() == b"already downloaded"
    engine.dispose()
