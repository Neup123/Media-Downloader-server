"""A completed job may re-deliver original verified outputs without re-downloading."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.completion import save_completed_files
from app.db import Job, utcnow
from app.worker import JobWorker


def _completed_job(client, settings):
    sessions = client.app.state.session_factory
    with sessions() as session:
        job = Job(
            url="https://example.com/media",
            engine="yt-dlp",
            status="completed",
            progress={"state": "completed", "percent": 100},
            output_files=[{"filename": "old.bin", "size_bytes": 1,
                           "path": "https://old.example/files/old.bin",
                           "delivery": "webdav", "verified": True}],
            finished_at=utcnow(),
        )
        session.add(job)
        session.commit()
        job_id = job.id
    return sessions, job_id


def test_re_delivery_reuses_verified_manifest_and_preserves_media_bytes(client, auth_headers, settings, monkeypatch):
    sessions, job_id = _completed_job(client, settings)
    workdir = settings.data_dir / "jobs" / job_id
    workdir.mkdir(parents=True)
    source = workdir / "video.mp4"
    source.write_bytes(b"original media fixture")
    save_completed_files(workdir, [source])

    unauthenticated = client.post(f"/api/v1/jobs/{job_id}/redeliver")
    assert unauthenticated.status_code == 401

    requested = client.post(f"/api/v1/jobs/{job_id}/redeliver", headers=auth_headers)
    assert requested.status_code == 202
    assert requested.json()["status"] == "queued"
    assert requested.json()["output_files"] == []
    assert requested.json()["finished_at"] is None
    assert requested.json()["progress"]["operation"] == "redeliver"

    repeated = client.post(f"/api/v1/jobs/{job_id}/redeliver", headers=auth_headers)
    assert repeated.status_code == 409

    async def unexpected_download(**kwargs):
        raise AssertionError("Re-delivery must not run any downloader engine")

    monkeypatch.setattr("app.worker.run_ytdlp", unexpected_download)
    asyncio.run(JobWorker(sessions, settings)._execute(job_id))

    result = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "completed", data["error"]
    assert len(data["output_files"]) == 1
    assert data["output_files"][0]["verified"] is True
    assert data["output_files"][0]["delivery"] == "local"
    assert (settings.output_dir / job_id / "video.mp4").read_bytes() == b"original media fixture"
    assert source.read_bytes() == b"original media fixture"
    assert "Re-delivery queued" in data["log_text"]


def test_redelivery_rejects_missing_manifest_without_erasing_old_delivery(client, auth_headers, settings):
    sessions, job_id = _completed_job(client, settings)
    response = client.post(f"/api/v1/jobs/{job_id}/redeliver", headers=auth_headers)
    assert response.status_code == 409
    assert "no longer verified" in response.text
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "completed"
        assert job.output_files[0]["delivery"] == "webdav"


def test_redelivery_rejects_modified_original_file(client, auth_headers, settings):
    sessions, job_id = _completed_job(client, settings)
    workdir = settings.data_dir / "jobs" / job_id
    workdir.mkdir(parents=True)
    source = workdir / "video.mp4"
    source.write_bytes(b"original")
    save_completed_files(workdir, [source])
    source.write_bytes(b"a different, longer payload")

    response = client.post(f"/api/v1/jobs/{job_id}/redeliver", headers=auth_headers)
    assert response.status_code == 409
    with sessions() as session:
        assert session.get(Job, job_id).status == "completed"


def test_redelivery_rejects_jobs_not_in_completed_state(client, auth_headers, settings):
    sessions = client.app.state.session_factory
    with sessions() as session:
        job = Job(url="https://example.com/media", engine="yt-dlp", status="queued")
        session.add(job)
        session.commit()
        job_id = job.id
    response = client.post(f"/api/v1/jobs/{job_id}/redeliver", headers=auth_headers)
    assert response.status_code == 409
    assert "Only completed jobs" in response.text
    missing = client.post("/api/v1/jobs/no-such-job/redeliver", headers=auth_headers)
    assert missing.status_code == 404
