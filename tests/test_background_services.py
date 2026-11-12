"""Lifecycle races, real subprocess teardown, and B-side API contract."""
import asyncio
import json
import socket
import sys
from dataclasses import replace

import pytest

from app.background_services import BackgroundServices
from app.db import EngineSetting, EnginePolicy, Job
from app.engine_gate import engine_policy


def setup_service(client, settings, mode="on-demand", **options):
    sessions = client.app.state.session_factory
    with sessions() as session:
        session.add(EngineSetting(engine="cobalt", settings={"mode": mode}))
        jobs = [Job(engine="cobalt", url="https://example.org/a", status="starting") for _ in range(2)]
        session.add_all(jobs)
        session.commit()
        ids = [job.id for job in jobs]
    # A real loopback child answers readiness, writes a log, and is reaped.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = "import http.server; print('daemon boot', flush=True); http.server.HTTPServer(('127.0.0.1', %d), http.server.SimpleHTTPRequestHandler).serve_forever()" % port
    config = replace(settings, cobalt_api_url=f"http://127.0.0.1:{port}",
                     cobalt_command=json.dumps([sys.executable, "-u", "-c", script]))
    return BackgroundServices(sessions, config, **options), ids


def set_mode(manager, mode):
    with manager.sessions() as session:
        session.get(EngineSetting, "cobalt").settings = {"mode": mode}
        session.commit()


def test_default_off_boolean_does_not_grant_permission(client, settings, auth_headers):
    for name in ("pyload", "cobalt"):
        with client.app.state.session_factory() as session:
            session.add(EnginePolicy(engine=name, enabled=True, run_carefully=True))
            session.commit()
            policy = engine_policy(session, name, settings)
            assert policy["mode"] == "off" and not policy["permitted"]
            assert policy["requirements_met"]  # No missing-requirement hint while off.
        path = f"/api/v1/engines/{name}/settings"
        assert client.get(path, headers=auth_headers).json()["settings"] == {"mode": "off"}
        for mode in ("always", "on-demand", "off"):
            response = client.put(path, headers=auth_headers, json={"settings": {"mode": mode}})
            assert response.status_code == 200
            assert response.json()["settings"]["mode"] == mode
        for value in (True, "enabled", None, [], {}):
            assert client.put(path, headers=auth_headers, json={"settings": {"mode": value}}).status_code == 422
        assert client.put(f"/api/v1/engines/{name}/policy", headers=auth_headers,
                          json={"enabled": True, "run_carefully": True}).status_code == 422


def test_real_shared_process_grace_reuse_and_logs(client, settings):
    async def run():
        manager, ids = setup_service(client, settings, grace=.08)
        try:
            await manager.apply("cobalt")
            await asyncio.gather(*(manager.acquire("cobalt", job) for job in ids))
            service = manager.services["cobalt"]
            child = service.process
            assert manager.status("cobalt")["state"] == "running"
            with manager.sessions() as session:
                assert session.get(Job, ids[0]).progress == {"phase": "starting_service", "service": "cobalt"}
            await manager.release("cobalt", ids[0])
            await asyncio.sleep(.12)
            assert child.returncode is None  # Other job still owns it.
            await manager.release("cobalt", ids[1])
            await manager.apply("cobalt")  # Same-mode autosave preserves grace.
            assert service.process is child
            await manager.acquire("cobalt", ids[0])
            assert service.process is child
            await manager.release("cobalt", ids[0])
            await asyncio.sleep(.2)
            assert child.returncode is not None
            assert manager.status("cobalt")["state"] == "stopped"
            with manager.sessions() as session:
                assert "daemon boot" in session.get(Job, ids[0]).log_text
                assert "phase" not in session.get(Job, ids[0]).progress
        finally:
            await manager.close()
    asyncio.run(run())


def test_start_failure_specific_and_retryable(client, settings):
    async def run():
        manager, ids = setup_service(client, settings, readiness_timeout=.15)
        manager.settings = replace(manager.settings, cobalt_command=json.dumps([
            sys.executable, "-c", "print('diagnostic failure', flush=True); raise SystemExit(2)",
        ]))
        for _ in range(2):
            with pytest.raises(RuntimeError, match="cobalt service did not start"):
                await manager.acquire("cobalt", ids[0])
            assert manager.status("cobalt")["state"] == "stopped"
        with manager.sessions() as session:
            assert "diagnostic failure" in session.get(Job, ids[0]).log_text
        await manager.close()
    asyncio.run(run())


def test_off_never_starts_and_always_mode_transitions(client, settings):
    async def run():
        manager, ids = setup_service(client, settings, mode="off", grace=.02)
        with pytest.raises(RuntimeError, match="off"):
            await manager.acquire("cobalt", ids[0])
        assert manager.services["cobalt"].process is None
        set_mode(manager, "always")
        await manager.apply("cobalt")
        child = manager.services["cobalt"].process
        await asyncio.sleep(.05)
        assert child.returncode is None
        set_mode(manager, "on-demand")
        await manager.apply("cobalt")
        assert child.returncode is not None
        assert manager.services["cobalt"].process is None
        set_mode(manager, "off")
        await manager.apply("cobalt")
        await manager.close()
    asyncio.run(run())


def test_waiting_lease_survives_until_terminal(client, settings):
    async def run():
        manager, ids = setup_service(client, settings, grace=.03)
        await manager.acquire("cobalt", ids[0])
        with manager.sessions() as session:
            session.get(Job, ids[0]).status = "captcha-waiting"
            session.commit()
        await manager.reconcile()
        await asyncio.sleep(.08)
        assert manager.status("cobalt")["state"] == "running"
        with manager.sessions() as session:
            session.get(Job, ids[0]).status = "cancelled"
            session.commit()
        await manager.reconcile()
        await asyncio.sleep(.1)
        assert manager.status("cobalt")["state"] == "stopped"
        await manager.close()
    asyncio.run(run())


def test_stale_port_is_specific_and_can_recover(client, settings):
    async def run():
        manager, ids = setup_service(client, settings)
        _, host, port = manager.endpoint("cobalt")
        server = await asyncio.start_server(lambda r, w: w.close(), host, port)
        try:
            with pytest.raises(RuntimeError, match="occupied by an unowned or stale process"):
                await manager.acquire("cobalt", ids[0])
            assert manager.services["cobalt"].process is None
        finally:
            server.close()
            await server.wait_closed()
        await manager.acquire("cobalt", ids[0])
        await manager.close()
    asyncio.run(run())


def test_readiness_phase_precedes_wait_and_cancellation_reaps_child(client, settings, monkeypatch):
    async def run():
        manager, ids = setup_service(client, settings)
        entered = asyncio.Event()

        async def not_ready(name):
            with manager.sessions() as session:
                assert session.get(Job, ids[0]).progress["phase"] == "starting_service"
            assert manager.status(name)["state"] == "starting"
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(manager, "ready", not_ready)
        task = asyncio.create_task(manager.acquire("cobalt", ids[0]))
        await entered.wait()
        child = manager.services["cobalt"].process
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.returncode is not None
        assert manager.status("cobalt")["state"] == "stopped"
        await manager.close()
    asyncio.run(run())


def test_stop_timeout_escalates_without_failing_completed_job(client, settings, monkeypatch):
    class StubbornProcess:
        returncode = None
        pid = 123
        killed = False

        def terminate(self):
            pass

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            if not self.killed:
                await asyncio.Future()
            return self.returncode

    async def run():
        manager, ids = setup_service(client, settings, stop_timeout=.02)
        child = StubbornProcess()
        manager.services["cobalt"].process = child
        # Exercise both platform paths without sending a real OS signal.
        monkeypatch.setattr("app.background_services.os.killpg", lambda pid, sig: child.kill() if sig == 9 else None, raising=False)
        with manager.sessions() as session:
            session.get(Job, ids[0]).status = "completed"
            session.commit()
        observed = []
        original = manager.phase

        def phase(*args):
            observed.append(args[-1])
            original(*args)

        monkeypatch.setattr(manager, "phase", phase)
        await manager._stop("cobalt", ids[0])
        assert child.killed
        assert observed == ["stopping_service", None]
        assert "forced termination" in manager.status("cobalt")["service_error"]
        with manager.sessions() as session:
            assert session.get(Job, ids[0]).status == "completed"
        await manager.close()
    asyncio.run(run())


def test_settings_autosave_applies_and_reports_live_state(settings, monkeypatch, auth_headers):
    from fastapi.testclient import TestClient
    from app.main import create_app

    starts = []

    async def start(manager, name):
        starts.append(name)
        manager.services[name].state = "running"

    monkeypatch.setattr(BackgroundServices, "_start", start)
    app = create_app(settings)
    path = "/api/v1/engines/cobalt/settings"
    with TestClient(app) as client:
        assert starts == []
        response = client.put(path, headers=auth_headers, json={"settings": {"mode": "on-demand"}})
        assert response.json()["state"] == "stopped"
        assert starts == []
        response = client.put(path, headers=auth_headers, json={"settings": {"mode": "always"}})
        assert response.json()["state"] == "running"
        assert starts == ["cobalt"]
        assert client.get(path, headers=auth_headers).json()["state"] == "running"
        response = client.put(path, headers=auth_headers, json={"settings": {"mode": "off"}})
        assert response.json()["state"] == "stopped"
