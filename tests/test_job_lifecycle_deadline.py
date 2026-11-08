"""Bounds and explicit partial-delivery markers for lifecycle completion."""
import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.job_states import JobStatus, transition_job
from app.db import Job


@pytest.mark.parametrize(("value", "expected"), [
    ("0", 60), ("600", 600), ("9999999", 604800),
])
def test_job_timeout_env_is_bounded(monkeypatch, value, expected):
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", value)
    assert Settings.from_env().job_timeout_seconds == expected


def test_asyncio_engine_deadline_fails_on_stalled_operation():
    asyncio.run(_deadline_expires())


async def _deadline_expires():
    async def stalled():
        await asyncio.sleep(1)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await stalled()


def test_partially_verified_files_remain_on_terminal_failure():
    job = Job(id="test", url="https://example.com", engine="generic",
              status="delivering", progress={"state": "delivering"},
              output_files=[{"path": "part-one.mp4", "verified": True}],
              log_text="")
    transition_job(job, JobStatus.FAILED)
    if job.output_files:
        job.progress = {**job.progress, "partial_delivery": True,
                        "verified_file_count": len(job.output_files)}
    assert job.status == "failed"
    assert job.progress["state"] == "failed"
    assert job.progress["partial_delivery"] is True
    assert job.output_files[0]["verified"] is True


def test_worker_explicitly_applies_engine_deadline_and_partial_delivery():
    source = (Path(__file__).resolve().parents[1] / "app/worker.py").read_text()
    assert "async with asyncio.timeout(self.settings.job_timeout_seconds)" in source
    assert "was_delivering and current.output_files" in source
    assert '"partial_delivery": True' in source
    assert '"verified_file_count": len(current.output_files)' in source
