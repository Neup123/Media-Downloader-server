import asyncio
from dataclasses import replace

import pytest

from app.captcha import CaptchaWaiting
from app.db import EngineSetting, Job
from app.worker import JobWorker


def make_worker(client, settings):
    sessions = client.app.state.session_factory
    config = replace(settings, pyload_api_url="http://127.0.0.1:8000", pyload_api_key="pl_test",
                     pyload_shared_root=settings.data_dir / "pyload-downloads", allow_private_targets=True)
    worker = JobWorker(sessions, config)
    with sessions() as session:
        session.add(EngineSetting(engine="pyload", settings={"mode": "on-demand"}))
        job = Job(engine="pyload", url="https://example.org/package.dlc", status="queued")
        session.add(job)
        session.commit()
        job_id = job.id
    return worker, job_id


def test_service_lease_extends_through_verified_delivery(client, settings, monkeypatch):
    worker, job_id = make_worker(client, settings)
    events = []

    async def acquire(name, identity):
        assert identity == job_id
        worker.services.phase(job_id, name, "starting_service")
        events.append("acquire")

    async def download(**kw):
        with worker.session_factory() as session:
            assert session.get(Job, job_id).progress["phase"] == "starting_service"
        await kw["on_progress"]({"state": "downloading", "downloaded_bytes": 5})
        with worker.session_factory() as session:
            assert session.get(Job, job_id).progress["phase"] == "downloading"
        target = kw["workdir"] / "out.bin"
        target.write_bytes(b"valid")
        events.append("download")
        return [target]

    async def deliver(files, *args, **kw):
        assert events == ["acquire", "download"]
        assert files[0].read_bytes() == b"valid"
        with worker.session_factory() as session:
            assert "phase" not in session.get(Job, job_id).progress
        events.append("handover")
        return [{"filename": "out.bin", "size_bytes": 5}]

    async def release(name, identity):
        with worker.session_factory() as session:
            assert session.get(Job, identity).status == "completed"
        events.append("release")

    monkeypatch.setattr(worker.services, "acquire", acquire)
    monkeypatch.setattr(worker.services, "release", release)
    monkeypatch.setattr("app.worker.run_pyload", download)
    monkeypatch.setattr("app.worker.deliver_files", deliver)
    asyncio.run(worker._execute(job_id))
    assert events == ["acquire", "download", "handover", "release"]


def test_captcha_retains_lease_and_resumes_original_package(client, settings, monkeypatch):
    worker, job_id = make_worker(client, settings)
    releases = []
    packages = []

    async def acquire(*args):
        pass

    async def release(*args):
        releases.append(args)

    async def download(**kw):
        packages.append(kw["existing_package_id"])
        raise CaptchaWaiting("owner action", task_id=7, package_id=12)

    monkeypatch.setattr(worker.services, "acquire", acquire)
    monkeypatch.setattr(worker.services, "release", release)
    monkeypatch.setattr("app.worker.run_pyload", download)
    asyncio.run(worker._execute(job_id))
    with worker.session_factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "captcha-waiting"
        job.status = "queued"
        session.commit()
    asyncio.run(worker._execute(job_id))
    assert packages == [None, 12]
    assert releases == []


@pytest.mark.parametrize("mode", ["off", "on-demand"])
def test_cobalt_fallback_only_when_mode_permits(client, settings, monkeypatch, mode):
    worker, job_id = make_worker(client, settings)
    with worker.session_factory() as session:
        session.get(Job, job_id).engine = "yt-dlp"
        session.add(EngineSetting(engine="cobalt", settings={"mode": mode}))
        session.commit()
    calls = []

    async def failed(**kw):
        raise RuntimeError("extractor failed")

    async def acquire(*args):
        calls.append("acquire")
        raise RuntimeError("The cobalt service did not start: test failure")

    monkeypatch.setattr("app.worker.run_ytdlp", failed)
    monkeypatch.setattr(worker.services, "acquire", acquire)
    asyncio.run(worker._execute(job_id))
    assert calls == ([] if mode == "off" else ["acquire"])
    with worker.session_factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert ("extractor failed" if mode == "off" else "cobalt service did not start") in job.error
