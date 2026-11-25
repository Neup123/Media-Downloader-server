"""Persistent graceful-stop command for the explicitly enabled recording engine.

Stop is a request, not a new job status: DOWNLOADING/no request -> requested;
repeat is idempotent; Cancel supersedes Stop; terminal transitions and explicit
retry clear the flag. A restart with a request may deliver only an already
verified completed manifest, never silently start a different live recording.
"""
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db import Job, utcnow
from app.job_states import JobStatus


def can_request_stop(job: Job) -> bool:
    return (job.engine == "streamlink" and job.owner_id is None
            and job.status == JobStatus.DOWNLOADING
            and not job.cancel_requested and not job.stop_requested)


def request_recording_stop(session: Session, job_id: str) -> Job | None:
    # Conditional SQL write preserves concurrent progress and cancellation.
    result = session.execute(update(Job).where(
        Job.id == job_id, Job.engine == "streamlink", Job.owner_id.is_(None),
        Job.status == JobStatus.DOWNLOADING,
        Job.cancel_requested.is_(False), Job.stop_requested.is_(False),
    ).values(stop_requested=True, updated_at=utcnow()))
    session.commit()
    job = session.get(Job, job_id, populate_existing=True)
    if job and (result.rowcount == 1 or (
        job.engine == "streamlink" and job.owner_id is None
        and job.status == JobStatus.DOWNLOADING and job.stop_requested
        and not job.cancel_requested
    )):
        return job
    return None
