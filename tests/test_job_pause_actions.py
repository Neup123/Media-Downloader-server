"""Per-job reversible pause capability and authenticated Core action contract."""
from unittest.mock import AsyncMock, MagicMock

from app.db import Job
from app.main import pause_capability


def test_per_job_capability_is_stage_and_engine_specific():
    def job(engine, status):
        return Job(id="j", url="https://example.org/media", engine=engine,
                   status=status, progress={"state": status})
    assert pause_capability(job("aria2c", "downloading")) == {
        "can_pause": True, "can_resume": False, "reason": None,
    }
    assert pause_capability(job("aria2c", "paused")) == {
        "can_pause": False, "can_resume": True, "reason": None,
    }
    for engine in ("generic", "yt-dlp", "gallery-dl", "you-get", "svtplay-dl", "get-sauce"):
        capability = pause_capability(job(engine, "downloading"))
        assert not capability["can_pause"] and not capability["can_resume"]
        assert engine in capability["reason"]
    unavailable = pause_capability(job("aria2c", "queued"))
    assert not unavailable["can_pause"] and "queued" in unavailable["reason"]


def test_pause_resume_use_same_job_and_worker_without_resubmitting(client, auth_headers):
    submitted = client.post("/api/v1/jobs", json={
        "url": "https://example.org/big.iso", "engine": "aria2c",
    }, headers=auth_headers).json()
    job_id = submitted["id"]
    assert submitted["pause_capability"]["can_pause"] is False
    assert client.post(f"/api/v1/jobs/{job_id}/pause", headers=auth_headers).status_code == 409

    with client.app.state.session_factory() as session:
        from app.job_states import transition_job, JobStatus
        job = session.get(Job, job_id)
        transition_job(job, JobStatus.STARTING)
        transition_job(job, JobStatus.DOWNLOADING)
        session.commit()

    # In a TestClient without a background worker, explicitly supply a live
    # controller; the route still uses the signed API and the persisted job.
    controller = MagicMock()
    controller.pause_job = AsyncMock()
    controller.resume_job = AsyncMock()
    client.app.state.worker = controller

    paused = client.post(f"/api/v1/jobs/{job_id}/pause", headers=auth_headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["id"] == job_id
    assert paused.json()["status"] == "paused"
    assert paused.json()["progress"]["state"] == "paused"
    assert paused.json()["pause_capability"]["can_resume"] is True
    controller.pause_job.assert_awaited_once_with(job_id)
    assert client.post(f"/api/v1/jobs/{job_id}/pause", headers=auth_headers).status_code == 409

    resumed = client.post(f"/api/v1/jobs/{job_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == job_id
    assert resumed.json()["status"] == "downloading"
    assert resumed.json()["pause_capability"]["can_pause"] is True
    controller.resume_job.assert_awaited_once_with(job_id)
    assert len(client.get("/api/v1/jobs", headers=auth_headers).json()) == 1


def test_nonpausable_jobs_refuse_actions_with_per_job_reason(client, auth_headers):
    job = client.post("/api/v1/jobs", json={
        "url": "https://example.org/media", "engine": "generic",
    }, headers=auth_headers).json()
    with client.app.state.session_factory() as session:
        from app.job_states import transition_job, JobStatus
        row = session.get(Job, job["id"])
        transition_job(row, JobStatus.STARTING)
        transition_job(row, JobStatus.DOWNLOADING)
        session.commit()
    current = client.get(f"/api/v1/jobs/{job['id']}", headers=auth_headers).json()
    assert current["pause_capability"]["can_pause"] is False
    assert "generic" in current["pause_capability"]["reason"]
    result = client.post(f"/api/v1/jobs/{job['id']}/pause", headers=auth_headers)
    assert result.status_code == 409
    assert "generic" in result.json()["detail"]
    assert client.post(f"/api/v1/jobs/{job['id']}/resume", headers=auth_headers).status_code == 409
    assert client.post(f"/api/v1/jobs/{job['id']}/pause").status_code == 401
