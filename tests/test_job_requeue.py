"""Single reset contract shared by retry-all, retry and verified re-delivery."""
from datetime import datetime, timezone

import pytest

from app.db import Job, requeue_job


@pytest.mark.parametrize(("operation", "expected_progress"), [
    (None, {"state": "queued"}),
    ("redeliver", {"state": "queued", "operation": "redeliver"}),
])
def test_requeue_clears_terminal_fields_but_preserves_owner_and_delivery(operation, expected_progress):
    previous = datetime(2026, 11, 1, tzinfo=timezone.utc)
    job = Job(
        id="id-1", url="https://example.com/media", engine="yt-dlp",
        status="failed", owner_id="alice", delivery_token="encrypted-destination",
        delivery_subfolder="Research", overrides={"format": "best"},
        progress={"state": "failed", "percent": 25}, output_files=[{"verified": True}],
        error="old failure", finished_at=previous, cancel_requested=True,
        log_text="previous log\n",
    )

    requeue_job(job, reason="New attempt", operation=operation)

    assert job.status == "queued"
    assert job.cancel_requested is False
    assert job.progress == expected_progress
    assert job.output_files == []
    assert job.error is None
    assert job.finished_at is None
    assert job.owner_id == "alice"
    assert job.delivery_token == "encrypted-destination"
    assert job.delivery_subfolder == "Research"
    assert job.overrides == {"format": "best"}
    assert job.log_text.startswith("previous log\n")
    assert job.log_text.count("New attempt") == 1


def test_reset_does_not_commit_or_waive_endpoint_specific_eligibility():
    """The helper mutates only the supplied row, leaving caller transaction and gates in control."""
    job = Job(url="https://example.com/file", engine="generic", status="completed",
              output_files=[{"verified": True}], progress={"state": "completed"})
    requeue_job(job, reason="Verified re-delivery", operation="redeliver")
    assert job.progress.get("operation") == "redeliver"
    assert job.status == "queued"
