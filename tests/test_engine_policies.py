"""Persisted engine policy, encrypted credentials and nonterminal captcha status."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.captcha import CaptchaWaiting, solve_captcha
from app.db import EngineCredential, EnginePolicy, Job
from app.engine_gate import engine_policy, save_telegram_credentials, telegram_credentials
from app.job_states import JobStatus, transition_job


def test_legacy_engines_enabled_by_default(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.main.shutil.which", lambda name: "/usr/local/bin/" + name)
    monkeypatch.setattr("app.main.subprocess.run", lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout="1.2.3\n", stderr="",
    ))
    listed = client.get("/api/v1/engines", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    for row in listed.json():
        if row["name"] in {"yt-dlp", "aria2c", "gallery-dl", "generic", "you-get", "svtplay-dl", "get-sauce"}:
            assert row["available"]
            assert row["enabled"] is True


def test_disabled_engine_rejected_for_new_jobs_and_unavailable_in_registry(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.main.shutil.which", lambda name: "/bin/" + name)
    monkeypatch.setattr("app.main.subprocess.run", lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout="v1.2.3\n", stderr="",
    ))
    path = "/api/v1/engines/generic/policy"
    assert client.get(path).status_code == 401
    update = client.put(path, headers=auth_headers, json={"enabled": False, "run_carefully": False})
    assert update.status_code == 200
    assert update.json()["permitted"] is False
    assert client.get(path, headers=auth_headers).json()["enabled"] is False
    registry = client.get("/api/v1/engines", headers=auth_headers).json()
    assert next(row for row in registry if row["name"] == "generic")["available"] is False
    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/file.bin", "engine": "generic",
    })
    assert job.status_code == 422
    assert "disabled" in job.json()["detail"].lower()
    with client.app.state.session_factory() as session:
        assert session.get(EnginePolicy, "generic").enabled is False
    assert client.put(path, headers=auth_headers, json={
        "enabled": "false", "run_carefully": False,
    }).status_code == 422


def test_telegram_credentials_are_fernet_only_and_absent_until_supplied(settings, client, auth_headers):
    with client.app.state.session_factory() as session:
        assert engine_policy(session, "telegram", settings)["permitted"] is False
        secrets = {"api_id": 123456, "api_hash": "f" * 32, "session_string": "1" * 120}
        save_telegram_credentials(session, settings, secrets)
        row = session.get(EngineCredential, "telegram")
        assert row is not None
        assert "123456" not in row.ciphertext
        assert "f" * 32 not in row.ciphertext
        assert telegram_credentials(session, settings) == secrets
        assert engine_policy(session, "telegram", settings)["permitted"] is True
    result = client.get("/api/v1/engines/telegram/credentials", headers=auth_headers)
    assert result.status_code == 200
    assert result.json() == {"configured": True, "secrets_returned": False}


def test_background_opt_in_required(settings, client):
    with client.app.state.session_factory() as session:
        assert engine_policy(session, "ddddocr", settings)["requirements_met"] is False
        assert "150" in engine_policy(session, "ddddocr", settings)["ram_note"]
        assert engine_policy(session, "pyload", settings)["permitted"] is False
        session.add(EnginePolicy(engine="ddddocr", enabled=True, run_carefully=True))
        session.commit()
        assert engine_policy(session, "ddddocr", settings)["permitted"] is True


def test_captcha_chain_uses_ocr_then_user_paid_then_system_then_manual(monkeypatch):
    calls = []

    def ocr(_):
        calls.append("ddddocr")
        return None

    monkeypatch.setattr("app.captcha._ocr", ocr)
    async def user_paid(_):
        calls.append("user")
        return "owner answer"
    async def system_paid(_):
        calls.append("system")
        return "system answer"

    assert asyncio.run(solve_captcha(b"image", ddddocr_enabled=True,
        user_paid_solver=user_paid, configured_paid_solver=system_paid)) == "owner answer"
    assert calls == ["ddddocr", "user"]
    calls.clear()
    with pytest.raises(CaptchaWaiting):
        asyncio.run(solve_captcha(b"image", ddddocr_enabled=False))
    assert calls == []
    calls.clear()
    async def no_answer(_):
        calls.append("user")
        return None
    assert asyncio.run(solve_captcha(b"image", ddddocr_enabled=True,
        user_paid_solver=no_answer, configured_paid_solver=system_paid)) == "system answer"
    assert calls == ["ddddocr", "user", "system"]


def test_captcha_waiting_is_nonterminal_and_can_resume():
    job = Job(url="https://example.org/file", engine="yt-dlp",
              status=JobStatus.STARTING.value, progress={"state": "starting"})
    transition_job(job, JobStatus.CAPTCHA_WAITING)
    assert job.status == "captcha-waiting"
    assert job.finished_at is None
    transition_job(job, JobStatus.QUEUED)
    assert job.status == "queued"
