"""Liveness is not readiness: scheduler/database failures remain visible."""
from __future__ import annotations

import asyncio
import logging

from app.db import initialize_database, make_engine, make_session_factory
from app.worker import JobWorker


def test_disabled_scheduler_keeps_liveness_green_but_readiness_degraded(client):
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "degraded", "component": "scheduler",
        "reason": "scheduler_disabled",
    }


def test_database_failure_is_reported_without_leaking_exception_text(client, monkeypatch):
    from sqlalchemy.orm import Session

    def fail_query(*args, **kwargs):
        raise RuntimeError("private connection credential must not appear")

    monkeypatch.setattr(Session, "execute", fail_query)
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json() == {"status": "degraded", "component": "database"}
    assert "credential" not in ready.text
    assert client.get("/healthz").status_code == 200


def test_scheduler_repeated_failure_is_visible_and_rate_limited(settings, monkeypatch, caplog):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    worker = JobWorker(sessions, settings)

    def broken_dispatch(*args, **kwargs):
        raise RuntimeError("sensitive service token never belongs in scheduler logs")

    async def immediate_sleep(seconds):
        if worker._scheduler_consecutive_errors >= 3:
            worker._stopping = True

    monkeypatch.setattr("app.worker.get_queued_jobs", broken_dispatch)
    monkeypatch.setattr("app.worker.asyncio.sleep", immediate_sleep)
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        asyncio.run(worker._run_loop())

    assert worker._scheduler_consecutive_errors == 3
    assert worker.readiness() == (False, "scheduler_errors")
    warnings = [record for record in caplog.records
                if "download_scheduler_failure" in record.message]
    assert len(warnings) == 1
    assert "consecutive=1" in warnings[0].message
    assert "RuntimeError" in caplog.text
    assert "sensitive service token" not in caplog.text
    engine.dispose()
