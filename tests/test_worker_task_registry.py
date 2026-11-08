"""Supervisor job identity must never be derived from asyncio task names."""
from __future__ import annotations

import asyncio

from app.db import Job, initialize_database, make_engine, make_session_factory
from app.worker import JobWorker


def test_shutdown_marks_real_running_job_even_when_task_name_is_unrelated(settings):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    with sessions() as session:
        job = Job(url="https://example.test/sample", engine="yt-dlp",
                  status="downloading", cancel_requested=False)
        session.add(job)
        session.commit()
        identifier = job.id
    worker = JobWorker(sessions, settings)
    observed = []

    async def exercise():
        async def simulated_download():
            while True:
                with sessions() as session:
                    current = session.get(Job, identifier)
                    if current.cancel_requested:
                        observed.append("cancelled-in-db")
                        return
                await asyncio.sleep(0.01)

        task = asyncio.create_task(simulated_download(), name="unrelated-label")
        worker._active.add(task)
        worker._active_jobs[identifier] = task
        worker._active_engines[identifier] = "yt-dlp"
        task.add_done_callback(lambda done: worker._task_done(identifier, done))
        await worker.stop()
        assert task.done()
        await asyncio.sleep(0)
        assert worker._active_jobs == {}
        assert worker._active_engines == {}

    asyncio.run(exercise())
    with sessions() as session:
        assert session.get(Job, identifier).cancel_requested is True
    assert observed == ["cancelled-in-db"]
    engine.dispose()


def test_stale_task_completion_does_not_drop_newer_attempt_for_same_job(settings):
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    worker = JobWorker(sessions, settings)

    async def exercise():
        old = asyncio.create_task(asyncio.sleep(0), name="anything")
        current = asyncio.create_task(asyncio.sleep(0), name="not-a-download-task")
        worker._active.add(old)
        worker._active.add(current)
        worker._active_jobs["same-job"] = current
        worker._active_engines["same-job"] = "aria2c"
        worker._task_done("same-job", old)
        assert worker._active_jobs["same-job"] is current
        assert worker._active_engines["same-job"] == "aria2c"
        worker._task_done("same-job", current)
        assert "same-job" not in worker._active_jobs
        assert "same-job" not in worker._active_engines
        await asyncio.gather(old, current)

    asyncio.run(exercise())
    engine.dispose()
