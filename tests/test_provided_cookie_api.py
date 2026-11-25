import asyncio
import base64

from app.cookies import read_cookie_file
from app.db import Job
from app.worker import JobWorker


ADMIN = b"# Netscape HTTP Cookie File\n.example.test\tTRUE\t/\tTRUE\t0\tid\tadmin-fixture\n"
PERSONAL = ADMIN.replace(b"admin-fixture", b"personal-fixture")


def payload(content):
    return {"content_base64": base64.b64encode(content).decode("ascii")}


def test_provisioning_api_does_not_expose_values(client, settings, auth_headers):
    url = "/api/v1/provided-cookies/www.example.test"
    assert client.put(url, json=payload(ADMIN)).status_code == 401
    invalid = client.put(url, headers=auth_headers, json=payload(ADMIN.replace(b".example.test", b".other.test")))
    assert invalid.status_code == 422 and "admin-fixture" not in invalid.text
    malformed = client.put(url, headers=auth_headers, json={"content_base64": ["secret-fixture"]})
    assert malformed.status_code == 422 and "secret-fixture" not in malformed.text
    result = client.put(url, headers=auth_headers, json=payload(ADMIN))
    assert result.status_code == 200, result.text
    assert result.json() == {"hostname": "www.example.test", "stored": True}
    assert client.get("/api/v1/provided-cookies", headers=auth_headers).json() == [result.json()]
    assert client.get("/api/v1/cookies", headers=auth_headers).json() == []
    path = settings.data_dir / "provided-cookies" / "www.example.test.txt"
    assert ADMIN not in path.read_bytes()
    assert read_cookie_file(path, settings.encryption_key) == ADMIN
    assert client.delete(url, headers=auth_headers).json() == {"hostname": "www.example.test", "deleted": True}
    assert not path.exists()


def test_personal_cookie_first_fallback_revoked_before_new_execution(client, settings, auth_headers, monkeypatch):
    url = "/api/v1/provided-cookies/www.example.test"
    assert client.put(url, headers=auth_headers, json=payload(ADMIN)).status_code == 200
    personal_name = "nc-fixture-www.example.test.txt"
    assert client.put("/api/v1/cookies/" + personal_name, headers=auth_headers, json=payload(PERSONAL)).status_code == 200
    factory = client.app.state.session_factory
    calls = []
    async def fake_download(**kw):
        name = kw["overrides"].get("cookies_file")
        calls.append(read_cookie_file(kw["cookie_dir"] / name, settings.encryption_key) if name else None)
        output = kw["workdir"] / "fixture.bin"
        output.write_bytes(b"fixture")
        return [output]
    async def fake_delivery(*args, **kwargs):
        return [{"filename": "fixture.bin", "verified": True, "delivery": "webdav"}]
    monkeypatch.setattr("app.worker.run_ytdlp", fake_download)
    monkeypatch.setattr("app.worker.deliver_files", fake_delivery)
    monkeypatch.setattr("app.worker.preflight_source_dns", lambda _: None)
    worker = JobWorker(factory, settings)
    def run(owner, overrides, host="www.example.test"):
        with factory() as session:
            job = Job(owner_id=owner, url="https://" + host + "/media", engine="yt-dlp",
                      status="queued", overrides=overrides)
            session.add(job)
            session.commit()
            job_id = job.id
        asyncio.run(worker._execute(job_id))
        with factory() as session:
            job = session.get(Job, job_id)
            assert job.status == "completed", job.error
            assert job.overrides == overrides
            assert "admin-fixture" not in job.log_text
    run("alice", {"cookies_file": personal_name})
    run("bob", {})
    run("bob", {}, "other.example.test")
    run(None, {})
    client.delete(url, headers=auth_headers)
    run("bob", {})
    assert calls == [PERSONAL, ADMIN, None, None, None]
