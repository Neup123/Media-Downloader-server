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
