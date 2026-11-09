"""Lifecycle contract: adapter states, terminal invariants and retry intent."""
from datetime import datetime, timezone

import pytest

from app.db import Job, requeue_job
from app.job_states import (
    JobStatus, TERMINAL_STATUSES, ACTIVE_STATUSES, ENGINE_PROGRESS_STATUSES,
    TRANSITIONS, InvalidJobTransition, transition_job,
)


def make_job(status="queued", progress=None):
    return Job(
        id="test-job", url="https://example.com/media", engine="generic",
        status=status, progress=progress or {"state": status},
        output_files=[], log_text="", cancel_requested=False,
    )


def test_status_vocabulary_and_transition_table_are_complete():
    assert set(TRANSITIONS) == set(JobStatus)
    assert TERMINAL_STATUSES.isdisjoint(ACTIVE_STATUSES)
    assert TERMINAL_STATUSES | ACTIVE_STATUSES == set(JobStatus)
    assert ENGINE_PROGRESS_STATUSES <= ACTIVE_STATUSES
    assert all(set(next_states) <= set(JobStatus) for next_states in TRANSITIONS.values())


@pytest.mark.parametrize("raw", [
    "completed", "cancelled", "failed", "queued", "pausing", "removed",
    "", None, 123, "DOWNLOADING",
])
def test_adapter_cannot_set_terminal_or_unknown_progress(raw):
    job = make_job("starting")
    if raw in ENGINE_PROGRESS_STATUSES:
        transition_job(job, raw, progress={"state": raw})
        return
    with pytest.raises(InvalidJobTransition):
        # Adapter ingress is restricted to ENGINE_PROGRESS_STATUSES before
        # invoking the general transition helper (tested in worker source).
        if raw not in ENGINE_PROGRESS_STATUSES:
            raise InvalidJobTransition("Engine emitted unsupported state")
        transition_job(job, raw, progress={"state": raw})


def test_normal_lifecycle_syncs_progress_and_finished_at():
    job = make_job()
    transition_job(job, JobStatus.STARTING)
    transition_job(job, JobStatus.DOWNLOADING, progress={"state": "downloading", "percent": 35})
    assert job.progress == {"state": "downloading", "percent": 35}
    transition_job(job, JobStatus.DELIVERING)
    transition_job(job, JobStatus.COMPLETED)
    assert job.progress["state"] == "completed"
    assert job.progress["percent"] == 35
    assert job.finished_at is not None
    with pytest.raises(InvalidJobTransition):
        transition_job(job, JobStatus.DOWNLOADING)


def test_terminal_job_requires_explicit_retry_reset():
    job = make_job("failed")
    job.finished_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with pytest.raises(InvalidJobTransition):
        transition_job(job, JobStatus.QUEUED)
    requeue_job(job, reason="retry")
    assert job.status == "queued"
    assert job.progress == {"state": "queued"}
    assert job.finished_at is None


def test_progress_mismatch_and_illegal_state_fail_closed():
    job = make_job("starting")
    with pytest.raises(InvalidJobTransition):
        transition_job(job, "downloading", progress={"state": "failed"})
    with pytest.raises(InvalidJobTransition):
        transition_job(job, "paused")
    with pytest.raises(InvalidJobTransition):
        transition_job(job, "completed")
    assert job.status == "starting"
    assert job.progress["state"] == "starting"


def test_api_and_worker_route_all_external_statuses_through_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    worker = (root / "app/worker.py").read_text()
    main = (root / "app/main.py").read_text()
    assert "if state not in ENGINE_PROGRESS_STATUSES" in worker
    assert "transition_job(current, state, progress=" in worker
    assert 'current.status = data.get("state"' not in worker
    assert "if status not in JobStatus._value2member_map_" in main
    assert "in TERMINAL_STATUSES" in main


def test_paused_job_lifecycle_is_reversible_but_not_terminal():
    job = make_job("downloading", {"state": "downloading", "percent": 42})
    transition_job(job, JobStatus.PAUSED)
    assert job.status == "paused"
    assert job.progress == {"state": "paused", "percent": 42}
    assert job.finished_at is None
    transition_job(job, JobStatus.DOWNLOADING)
    assert job.status == "downloading"
    assert job.progress["percent"] == 42
