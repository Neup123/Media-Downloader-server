"""Signed Nextcloud request retries must never create a second Core job."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from app.db import Job


def payload(owner="alice", key=None):
    return {
        "url": "https://example.org/item.mp4", "engine": "yt-dlp",
        "owner_id": owner, "submission_id": key or str(uuid4()),
        "delivery": {
            "mode": "webdav",
            "webdav_url": f"https://cloud.example.org/remote.php/dav/files/{owner}",
            "username": owner, "app_password": "test-private-app-password",
            "folder": "Downloads",
        },
    }


def test_same_owner_request_replays_original_job_without_exposing_secret_or_request_id(client, auth_headers):
    body=payload()
    first=client.post("/api/v1/jobs",headers=auth_headers,json=body)
    assert first.status_code == 202, first.text
    second=client.post("/api/v1/jobs",headers=auth_headers,json=body)
    assert second.status_code == 202, second.text
    assert first.json()["id"] == second.json()["id"]
    found=client.get("/api/v1/jobs",headers=auth_headers,params={
        "owner_id":"alice","submission_id":body["submission_id"]
    })
    assert found.status_code==200
    assert [row["id"] for row in found.json()]==[first.json()["id"]]
    assert client.get("/api/v1/jobs",headers=auth_headers,params={
        "submission_id":body["submission_id"]
    }).status_code==422
    assert "submission_id" not in second.json()
    assert "submission_fingerprint" not in second.json()
    assert "test-private-app-password" not in second.text
    with client.app.state.session_factory() as session:
        rows=session.scalars(select(Job).where(Job.owner_id=="alice")).all()
        assert len(rows)==1
        assert rows[0].submission_id==body["submission_id"]
        assert rows[0].submission_fingerprint is not None
        assert "test-private-app-password" not in rows[0].submission_fingerprint


def test_owner_scope_and_request_mismatch(client, auth_headers):
    shared=str(uuid4())
    alice=payload("alice",shared)
    bob=payload("bob",shared)
    a=client.post("/api/v1/jobs",headers=auth_headers,json=alice)
    b=client.post("/api/v1/jobs",headers=auth_headers,json=bob)
    assert a.status_code==202 and b.status_code==202
    assert a.json()["id"] != b.json()["id"]
    edited=deepcopy(alice)
    edited["url"]="https://example.org/different.mp4"
    bad=client.post("/api/v1/jobs",headers=auth_headers,json=edited)
    assert bad.status_code==409
    assert client.get("/api/v1/jobs?owner_id=alice",headers=auth_headers).json()[0]["id"]==a.json()["id"]


def test_request_id_cannot_be_used_without_owner_or_in_batch(client, auth_headers):
    key=str(uuid4())
    no_owner={"url":"https://example.org/file.zip","engine":"generic","submission_id":key}
    rejected=client.post("/api/v1/jobs",headers=auth_headers,json=no_owner)
    assert rejected.status_code==422
    batch=client.post("/api/v1/jobs/batch",headers=auth_headers,json={"items":[no_owner]})
    assert batch.status_code==422
    invalid=payload(key="not-a-uuid")
    assert client.post("/api/v1/jobs",headers=auth_headers,json=invalid).status_code==422


def test_legacy_submission_without_request_key_still_creates_independent_jobs(client, auth_headers):
    single={"url":"https://example.org/file.zip","engine":"generic"}
    a=client.post("/api/v1/jobs",headers=auth_headers,json=single)
    b=client.post("/api/v1/jobs",headers=auth_headers,json=single)
    assert a.status_code==202 and b.status_code==202
    assert a.json()["id"]!=b.json()["id"]
