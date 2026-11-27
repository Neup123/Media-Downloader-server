"""Extracted #173 policy contract against the current registry and worker."""
import asyncio

from app.db import Job, get_queued_jobs
from app.engine_gate import disabled_engines, engine_permitted
from app.worker import JobWorker


def test_engine_policy_persists_and_blocks_new_jobs(client, auth_headers):
    path = "/api/v1/engines/generic/policy"
    request = {"url": "https://example.org/file.bin", "engine": "generic"}
    assert client.get(path).status_code == 401
    assert client.put(path, json={"enabled": False}).status_code == 401
    assert client.get(path, headers=auth_headers).json() == {"engine": "generic", "enabled": True}
    old = client.post("/api/v1/jobs", headers=auth_headers, json=request)
    assert old.status_code == 202
    for invalid in ({"enabled": "false"}, {"enabled": False, "owner_id": "other"}, {}):
        assert client.put(path, headers=auth_headers, json=invalid).status_code == 422
    result = client.put(path, headers=auth_headers, json={"enabled": False})
    assert result.json() == {"engine": "generic", "enabled": False}
    assert client.get(path, headers=auth_headers).json() == result.json()
    assert client.post("/api/v1/jobs", headers=auth_headers, json=request).status_code == 422
    assert client.get("/api/v1/engines/not-installed/policy", headers=auth_headers).status_code == 404
    with client.app.state.session_factory() as session:
        assert disabled_engines(session) == {"generic", "streamlink"}
        assert not engine_permitted(session, "generic")
        assert engine_permitted(session, "yt-dlp")
        assert not get_queued_jobs(session, 100, disabled_engines(session))
        assert session.get(Job, old.json()["id"]).status == "queued"
    assert client.put(path, headers=auth_headers, json={"enabled": True}).json()["enabled"] is True
    assert client.post("/api/v1/jobs", headers=auth_headers, json=request).status_code == 202


def test_worker_rechecks_policy_after_selection(settings, client, auth_headers):
    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/file.bin", "engine": "generic"
    }).json()
    client.put("/api/v1/engines/generic/policy", headers=auth_headers, json={"enabled": False})
    worker = JobWorker(client.app.state.session_factory, settings)
    asyncio.run(worker._execute(job["id"]))
    with client.app.state.session_factory() as session:
        assert session.get(Job, job["id"]).status == "queued"
    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_engine_registry_requires_probe_and_policy(client, auth_headers, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("app.main.shutil.which", lambda name: "/bin/" + name)
    monkeypatch.setattr("app.main.subprocess.run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="v1.2.3\n", stderr=""))
    def generic():
        return next(row for row in client.get("/api/v1/engines", headers=auth_headers).json()
                    if row["name"] == "generic")
    assert generic()["available"] is True
    client.put("/api/v1/engines/generic/policy", headers=auth_headers, json={"enabled": False})
    assert generic()["available"] is False and generic()["installed"] is True
    client.put("/api/v1/engines/generic/policy", headers=auth_headers, json={"enabled": True})
    monkeypatch.setattr("app.main.shutil.which", lambda name: None)
    assert generic()["available"] is False and generic()["enabled"] is True


def test_queued_policy_reason_is_derived_and_clears_without_a_worker(client, auth_headers):
    path = "/api/v1/engines/generic/policy"
    first = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/first.bin", "engine": "generic"
    }).json()
    other = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/other", "engine": "yt-dlp"
    }).json()
    active = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/active.bin", "engine": "generic"
    }).json()
    from app.job_states import JobStatus, transition_job
    with client.app.state.session_factory() as session:
        transition_job(session.get(Job, active["id"]), JobStatus.STARTING)
        session.commit()
    assert first["queue_block_reason"] is None
    assert client.put(path, headers=auth_headers, json={"enabled": False}).status_code == 200
    def get(job):
        response = client.get("/api/v1/jobs/" + job["id"], headers=auth_headers)
        assert response.status_code == 200
        return response.json()
    blocked = get(first)
    assert blocked["status"] == "queued" and blocked["error"] is None
    assert blocked["queue_block_reason"] == "engine_disabled"
    assert blocked["progress"] == first["progress"]
    assert blocked["log_text"] == first["log_text"]
    listed = {row["id"]: row for row in client.get("/api/v1/jobs", headers=auth_headers).json()}
    assert listed[first["id"]]["queue_block_reason"] == "engine_disabled"
    assert get(other)["queue_block_reason"] is None
    assert get(active)["queue_block_reason"] is None
    assert client.put(path, headers=auth_headers, json={"enabled": True}).status_code == 200
    assert get(first)["queue_block_reason"] is None
    assert client.put(path, headers=auth_headers, json={"enabled": False}).status_code == 200
    cancelled = client.post("/api/v1/jobs/" + first["id"] + "/cancel", headers=auth_headers).json()
    assert cancelled["status"] == "cancelled" and cancelled["queue_block_reason"] is None
    retried = client.post("/api/v1/jobs/" + first["id"] + "/retry", headers=auth_headers).json()
    assert retried["status"] == "queued" and retried["queue_block_reason"] == "engine_disabled"


def test_preview_admission_before_dns_or_extractor(client, auth_headers, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from app.discovery import PreviewError
    dns = Mock()
    ytdlp = AsyncMock(side_effect=PreviewError("synthetic extractor reached"))
    gallery = AsyncMock(side_effect=PreviewError("synthetic extractor reached"))
    monkeypatch.setattr("app.main.preflight_source_dns", dns)
    monkeypatch.setattr("app.main.inspect_ytdlp", ytdlp)
    monkeypatch.setattr("app.main.inspect_gallery_dl", gallery)
    for engine, inspect, other in (("yt-dlp", ytdlp, gallery), ("gallery-dl", gallery, ytdlp)):
        dns.reset_mock()
        inspect.reset_mock()
        other.reset_mock()
        path = f"/api/v1/engines/{engine}/policy"
        request = {"url": "https://example.org/media", "engine": engine}
        assert client.put(path, headers=auth_headers, json={"enabled": False}).status_code == 200
        blocked = client.post("/api/v1/preview", headers=auth_headers, json=request)
        assert blocked.status_code == 409
        assert blocked.json() == {"detail": "Selected engine is disabled"}
        dns.assert_not_called()
        inspect.assert_not_awaited()
        other.assert_not_awaited()
        assert client.put(path, headers=auth_headers, json={"enabled": True}).status_code == 200
        allowed = client.post("/api/v1/preview", headers=auth_headers, json=request)
        assert allowed.status_code == 422
        assert allowed.json()["detail"] == "synthetic extractor reached"
        dns.assert_called_once()
        inspect.assert_awaited_once()
        other.assert_not_awaited()


def test_disabled_aria2_manifest_never_creates_rpc(client, auth_headers, monkeypatch):
    from unittest.mock import Mock, AsyncMock
    rpc = Mock()
    rpc.return_value.inspect_torrent = AsyncMock(side_effect=ValueError("synthetic RPC reached"))
    rpc.return_value.close = AsyncMock()
    monkeypatch.setattr("app.main.Aria2Rpc", rpc)
    path = "/api/v1/engines/aria2c/policy"
    request = {"url": "magnet:?xt=urn:btih:0123456789012345678901234567890123456789"}
    assert client.put(path, headers=auth_headers, json={"enabled": False}).status_code == 200
    blocked = client.post("/api/v1/torrent/manifest", headers=auth_headers, json=request)
    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "Selected engine is disabled"}
    rpc.assert_not_called()
    assert client.put(path, headers=auth_headers, json={"enabled": True}).status_code == 200
    allowed = client.post("/api/v1/torrent/manifest", headers=auth_headers, json=request)
    assert allowed.status_code == 422
    assert allowed.json()["detail"] == "synthetic RPC reached"
    rpc.assert_called_once()
    rpc.return_value.inspect_torrent.assert_awaited_once()
    rpc.return_value.close.assert_awaited_once()
