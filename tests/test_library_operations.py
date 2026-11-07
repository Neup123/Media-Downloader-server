"""Library operations must preserve Nextcloud ownership, active jobs and file bytes."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.db import Job, utcnow


def add(client, *, status, url, owner=None, filename=None, days_ago=0):
    identifier = str(uuid4())
    with client.app.state.session_factory() as db:
        db.add(Job(
            id=identifier, owner_id=owner, status=status, url=url, engine="generic",
            overrides={}, progress={"state": status},
            output_files=([{"filename": filename, "verified": True}] if filename else []),
            created_at=utcnow() - timedelta(days=days_ago), log_text="",
        ))
        db.commit()
    return identifier


def test_library_search_finds_old_failed_filename_and_filters_owner(client, auth_headers):
    old = add(client, status="failed", url="https://example.org/source/old",
              filename="family-video.mp4", days_ago=16)
    add(client, status="completed", url="https://example.org/source/recent")
    add(client, status="failed", url="https://example.org/source/old",
        filename="family-video.mp4", owner="alice")
    response = client.get("/api/v1/jobs", params={
        "state": "failed", "q": "FAMILY-VIDEO", "standalone_only": "true",
        "limit": 20, "offset": 0,
    }, headers=auth_headers)
    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [old]
    assert client.get("/api/v1/jobs", params={"q": "x"*129}, headers=auth_headers).status_code == 422
    assert client.get("/api/v1/jobs", params={"state": "bogus"}, headers=auth_headers).status_code == 422


def test_library_search_escapes_like_wildcards(client, auth_headers):
    literal = add(client,status="failed",url="https://example.org/has-%25-percent")
    add(client,status="failed",url="https://example.org/ordinary")
    found = client.get("/api/v1/jobs", params={"q":"%", "standalone_only":"true"},
                       headers=auth_headers)
    assert {job["id"] for job in found.json()} == {literal}


def test_bulk_retry_changes_only_failed_standalone_jobs(client, auth_headers):
    first = add(client,status="failed",url="https://example.org/one")
    second = add(client,status="cancelled",url="https://example.org/two")
    running = add(client,status="downloading",url="https://example.org/three")
    nextcloud = add(client,status="failed",url="https://example.org/four",owner="alice")
    assert client.post("/api/v1/jobs/retry-failed").status_code == 401
    response = client.post("/api/v1/jobs/retry-failed",headers=auth_headers)
    assert response.status_code == 200 and response.json() == {"retried":2}
    with client.app.state.session_factory() as session:
        assert session.get(Job,first).status == "queued"
        assert session.get(Job,second).status == "queued"
        assert session.get(Job,running).status == "downloading"
        assert session.get(Job,nextcloud).status == "failed"


def test_clear_history_explicitly_removes_only_terminal_standalone_metadata(client, auth_headers, settings):
    done = add(client,status="completed",url="https://example.org/completed")
    failed = add(client,status="failed",url="https://example.org/failed")
    active = add(client,status="queued",url="https://example.org/active")
    nc_owned = add(client,status="completed",url="https://example.org/alice",owner="alice")
    delivered = settings.output_dir / "existing.mp4"
    delivered.parent.mkdir(parents=True,exist_ok=True)
    delivered.write_bytes(b"unmodified")
    assert client.delete("/api/v1/jobs/history",headers=auth_headers).status_code == 422
    assert client.delete("/api/v1/jobs/history?confirm=true").status_code == 401
    response = client.delete("/api/v1/jobs/history?confirm=true",headers=auth_headers)
    assert response.status_code == 200 and response.json() == {"deleted":2,"files_retained":True}
    with client.app.state.session_factory() as session:
        assert session.get(Job,done) is None and session.get(Job,failed) is None
        assert session.get(Job,active) is not None and session.get(Job,nc_owned) is not None
    assert delivered.read_bytes() == b"unmodified"
