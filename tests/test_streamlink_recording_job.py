import asyncio

import pytest

from app.db import Job
from app.worker import JobWorker
from app.completion import save_completed_files


def enabled_job(client, auth_headers):
    assert client.put("/api/v1/engines/streamlink/policy", headers=auth_headers,
                      json={"enabled": True}).status_code == 200
    response = client.post("/api/v1/jobs", headers=auth_headers, json={
        "engine": "streamlink", "url": "https://example.org/live", "overrides": {"quality": "720p,best"}
    })
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_explicit_opt_in_and_stop_api(client, auth_headers):
    path = "/api/v1/engines/streamlink/policy"
    assert client.get(path, headers=auth_headers).json()["enabled"] is False
    req = {"engine": "streamlink", "url": "https://example.org/live"}
    assert client.post("/api/v1/jobs", headers=auth_headers, json=req).status_code == 422
    job_id = enabled_job(client, auth_headers)
    assert client.post("/api/v1/jobs", headers=auth_headers, json={
        **req, "owner_id": "someone"
    }).status_code == 422
    assert client.post("/api/v1/jobs", headers=auth_headers, json={
        **req, "overrides": {"quality": "--force"}
    }).status_code == 422
    stop = f"/api/v1/jobs/{job_id}/stop"
    assert client.post(stop).status_code == 401
    assert client.post(stop, headers=auth_headers).status_code == 409
    with client.app.state.session_factory() as session:
        job = session.get(Job, job_id)
        job.status = "downloading"
        session.commit()
    assert client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers).json()["can_stop"]
    for _ in range(2):
        result = client.post(stop, headers=auth_headers)
        assert result.status_code == 200
        assert result.json()["stop_requested"] and not result.json()["can_stop"]
        assert result.json()["status"] == "downloading"
    assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers).status_code == 200
    assert client.post(stop, headers=auth_headers).status_code == 409


@pytest.mark.parametrize("verified", [False, True])
def test_interrupted_stop_never_starts_new_recording(settings, client, auth_headers, monkeypatch, verified):
    job_id = enabled_job(client, auth_headers)
    factory = client.app.state.session_factory
    with factory() as session:
        job = session.get(Job, job_id)
        job.status = "downloading"
        job.stop_requested = True
        session.commit()
    work = settings.data_dir / "jobs" / job_id
    work.mkdir(parents=True)
    file = work / "recording.ts"
    file.write_bytes(b"previous recording")
    if verified:
        save_completed_files(work, [file])
    async def no_record(**kwargs):
        raise AssertionError("Accepted Stop must not restart a recording")
    monkeypatch.setattr("app.worker.run_streamlink", no_record)
    worker = JobWorker(factory, settings)
    worker._recover_jobs()
    asyncio.run(worker._execute(job_id))
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == ("completed" if verified else "failed"), job.error
        assert not job.stop_requested
        assert bool(job.output_files) == verified


def test_worker_uses_persisted_stop_and_delivers_same_job(settings, client, auth_headers, monkeypatch):
    job_id = enabled_job(client, auth_headers)
    factory = client.app.state.session_factory
    monkeypatch.setattr("app.worker.preflight_source_dns", lambda url: None)
    async def fake_record(**kwargs):
        assert kwargs["quality"] == "720p,best"
        assert not await kwargs["is_stopped"]()
        await kwargs["on_progress"]({"state": "downloading"})
        response = client.post(f"/api/v1/jobs/{job_id}/stop", headers=auth_headers)
        assert response.status_code == 200
        assert await kwargs["is_stopped"]()
        output = kwargs["workdir"] / "recording.ts"
        output.write_bytes(b"fixture")
        return [output]
    monkeypatch.setattr("app.worker.run_streamlink", fake_record)
    asyncio.run(JobWorker(factory, settings)._execute(job_id))
    result = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers).json()
    assert result["status"] == "completed", result["error"]
    assert result["output_files"][0]["verified"]
    assert not result["stop_requested"] and not result["can_stop"]
