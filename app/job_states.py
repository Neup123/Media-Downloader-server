"""Single authoritative job-status vocabulary and valid lifecycle transitions.

The database column intentionally remains a string to read existing SQLite
rows without a destructive migration. All new status writes should pass
through transition_job rather than trusting an external adapter payload.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import Job


class JobStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    EXTRACTING = "extracting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    CAPTCHA_WAITING = "captcha-waiting"
    POST_PROCESSING = "post-processing"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
})
ACTIVE_STATUSES = frozenset(JobStatus) - TERMINAL_STATUSES
ENGINE_PROGRESS_STATUSES = frozenset({
    JobStatus.STARTING, JobStatus.EXTRACTING,
    JobStatus.DOWNLOADING, JobStatus.PAUSED, JobStatus.CAPTCHA_WAITING, JobStatus.POST_PROCESSING,
})
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.STARTING, JobStatus.CANCELLED}),
    JobStatus.STARTING: frozenset({
        JobStatus.EXTRACTING, JobStatus.DOWNLOADING, JobStatus.POST_PROCESSING,
        JobStatus.CAPTCHA_WAITING, JobStatus.DELIVERING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.EXTRACTING: frozenset({
        JobStatus.DOWNLOADING, JobStatus.POST_PROCESSING, JobStatus.DELIVERING,
        JobStatus.CAPTCHA_WAITING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.DOWNLOADING: frozenset({
        JobStatus.EXTRACTING, JobStatus.PAUSED, JobStatus.CAPTCHA_WAITING, JobStatus.POST_PROCESSING, JobStatus.DELIVERING,
        JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.PAUSED: frozenset({
        JobStatus.DOWNLOADING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.CAPTCHA_WAITING: frozenset({
        JobStatus.DOWNLOADING, JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED,
    }),
    JobStatus.POST_PROCESSING: frozenset({
        JobStatus.DOWNLOADING, JobStatus.DELIVERING,
        JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.DELIVERING: frozenset({
        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.QUEUED,
    }),
    JobStatus.COMPLETED: frozenset({JobStatus.QUEUED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset({JobStatus.QUEUED}),
}


class InvalidJobTransition(ValueError):
    pass


def transition_job(
    job: Job, status: JobStatus | str, *, progress: dict[str, Any] | None = None,
    reset: bool = False,
) -> None:
    """Apply and validate one state transition, keeping progress.state aligned.

    Existing/legacy jobs use string status values. Invalid adapter states and
    impossible terminal-to-active transitions must fail closed, never enter the
    database. Retries and verified re-delivery explicitly use reset=True.
    """
    try:
        current = JobStatus(job.status)
        target = JobStatus(status)
    except ValueError as exc:
        raise InvalidJobTransition("Unknown job status") from exc
    if current != target and target not in TRANSITIONS[current]:
        raise InvalidJobTransition(f"Invalid job transition {current} -> {target}")
    if current in TERMINAL_STATUSES and target == JobStatus.QUEUED and not reset:
        raise InvalidJobTransition("Terminal job reset requires an explicit retry")
    if progress is not None:
        if not isinstance(progress, dict) or (
            progress.get("state", str(target)) != str(target)
        ):
            raise InvalidJobTransition("Adapter progress.state must match job status")
        next_progress = {**progress, "state": str(target)}
    else:
        next_progress = {**(job.progress or {}), "state": str(target)}
    job.status = str(target)
    job.progress = next_progress
    if target in TERMINAL_STATUSES:
        from app.db import utcnow
        job.finished_at = utcnow()
    elif target == JobStatus.QUEUED:
        job.finished_at = None
