"""Deterministic fallback acceptance. Never contacts a real provider."""
import asyncio
import sys
from pathlib import Path

import pytest

from app.adapters.you_get import permits_youtube_fallback, run_you_get
from app.adapters.yt_dlp import run_ytdlp
from app.completion import load_completed_files
from app.db import EngineSetting, Job
from app.engine_failures import EngineFailure, EngineFailureKind, YouGetFallbackFailure
from app.user_failures import user_failure_message
from app.worker import JobWorker

URL = "https://youtu.be/abcdefghijk"


@pytest.mark.parametrize("url,overrides,kind,allowed", [
    (URL, {}, EngineFailureKind.UNSUPPORTED, True),
    ("https://www.youtube.com/watch?v=abcdefghijk", {}, EngineFailureKind.UNSUPPORTED, True),
    ("https://www.youtube.com/shorts/abcdefghijk", {}, EngineFailureKind.UNSUPPORTED, True),
    ("https://youtu.be.evil.test/abcdefghijk", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://youtu.be/abcdefghijk?list=fixture", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://youtu.be/abcdefghijk?p=fixture", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://youtu.be/abcdefghijk?list=", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://youtu.be:8443/abcdefghijk", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://www.youtube.com/playlist?list=fixture", {}, EngineFailureKind.UNSUPPORTED, False),
    ("https://example.test/abcdefghijk", {}, EngineFailureKind.UNSUPPORTED, False),
    (URL, {"format": "fixture"}, EngineFailureKind.UNSUPPORTED, False),
    (URL, {"no_proxy": True}, EngineFailureKind.UNSUPPORTED, False),
    (URL, {}, EngineFailureKind.AUTH_REQUIRED, False),
    (URL, {}, EngineFailureKind.NETWORK, False),
    (URL, {}, EngineFailureKind.ACCESS_DENIED, False),
    (URL, {}, EngineFailureKind.RATE_LIMITED, False),
    (URL, {}, EngineFailureKind.UNKNOWN, False),
    (URL, {}, EngineFailureKind.NO_OUTPUT, False),
])
def test_narrow_fallback_admission(url, overrides, kind, allowed):
    assert permits_youtube_fallback(url, {}, overrides, EngineFailure(kind, "fixture")) == allowed


def executable(tmp_path, name, script):
    path = tmp_path / name
    path.write_text("#!" + sys.executable + "\n" + script)
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize("case", ["success", "cookie", "failed", "disabled", "busy", "missing", "target-settings", "cancelled", "stale-producer", "manifest-failure"])
def test_worker_fallback_contract(client, settings, auth_headers, monkeypatch, tmp_path, case):
    cookie_check = """
import sys
cookie = Path(sys.argv[sys.argv.index('--cookies') + 1])
assert cookie.read_bytes() == b'synthetic-only'
assert cookie.parent != Path.cwd()
""" if case == "cookie" else ""
    primary = executable(tmp_path, "primary", """
from pathlib import Path
""" + cookie_check + """
Path('stale.mp4').write_bytes(b'unverified primary bytes')
from you_get.util import log
log.wtf('No signatureCipher or url for itag=18', exit_code=3)
""")
    secondary = executable(tmp_path, "secondary", """
from pathlib import Path
""" + cookie_check + """
assert not Path('stale.mp4').exists()
""" + ("""
print('ERROR: HTTP Error 429: Too Many Requests token=synthetic-private')
raise SystemExit(4)
""" if case == "failed" else """
p = Path('result.mp4')
p.write_bytes(b'verified secondary fixture')
print('__DL_FILE__' + str(p.resolve()))
"""))
    factory = client.app.state.session_factory
    worker = JobWorker(factory, settings)
    calls = []

    overrides = {}
    if case == "cookie":
        from app.cookies import store_cookie_file
        store_cookie_file(settings.data_dir / "cookies", "fixture.txt",
                          b"synthetic-only", settings.encryption_key)
        overrides["cookies_file"] = "fixture.txt"
    with factory() as session:
        job = Job(url=URL, engine="you-get", status="queued", overrides=overrides,
                  output_engine="yt-dlp" if case == "stale-producer" else None)
        session.add(job)
        if case == "target-settings":
            session.add(EngineSetting(engine="yt-dlp", settings={"format": "custom"}))
        session.commit()
        job_id = job.id
    worker._active_engines[job_id] = "you-get"
    if case == "stale-producer":
        # Simulate crash after producer persistence but before manifest publication.
        work = settings.data_dir / "jobs" / job_id
        work.mkdir(parents=True)
        (work / "old.mp4").write_bytes(b"unverified")
    if case == "manifest-failure":
        def fail_manifest(*_):
            raise OSError("synthetic disk failure")
        monkeypatch.setattr("app.worker.save_completed_files", fail_manifest)
    if case == "disabled":
        assert client.put("/api/v1/engines/yt-dlp/policy", headers=auth_headers,
                          json={"enabled": False}).status_code == 200
    if case == "busy":
        settings.max_concurrent_per_engine["yt-dlp"] = 1
        worker._active_engines["other-job"] = "yt-dlp"

    async def run_primary(**kw):
        calls.append("you-get")
        if case == "stale-producer":
            with factory() as session:
                assert session.get(Job, job_id).output_engine is None
            assert not list(kw["workdir"].iterdir())
            result = kw["workdir"] / "result.mp4"
            result.write_bytes(b"verified primary fixture")
            return [result]
        kw["executable"] = primary
        if case == "cancelled":
            with factory() as session:
                current = session.get(Job, job_id)
                current.cancel_requested = True
                session.commit()
            raise EngineFailure(EngineFailureKind.UNSUPPORTED, "synthetic")
        return await run_you_get(**kw)

    async def run_secondary(**kw):
        calls.append("yt-dlp")
        assert worker._active_engines[job_id] == "yt-dlp"
        assert not list(kw["workdir"].iterdir())
        kw["executable"] = secondary
        return await run_ytdlp(**kw)

    monkeypatch.setattr("app.worker.preflight_source_dns", lambda _: None)
    monkeypatch.setattr("app.worker.shutil.which", lambda _: None if case == "missing" else secondary)
    monkeypatch.setattr("app.worker.run_you_get", run_primary)
    monkeypatch.setattr("app.worker.run_ytdlp", run_secondary)
    asyncio.run(worker._execute(job_id))
    job = client.get("/api/v1/jobs/" + job_id, headers=auth_headers).json()
    assert job["engine"] == "you-get"
    work = settings.data_dir / "jobs" / job_id
    if case in {"success", "cookie"}:
        assert calls == ["you-get", "yt-dlp"]
        assert job["status"] == "completed", job["error"]
        assert job["output_engine"] == "yt-dlp"
        assert load_completed_files(work) == [work / "result.mp4"]
        assert not (work / "stale.mp4").exists()
        assert (settings.output_dir / job_id / "result.mp4").read_bytes() == b"verified secondary fixture"
        assert not (settings.output_dir / job_id / "stale.mp4").exists()
        assert client.post("/api/v1/jobs/" + job_id + "/redeliver", headers=auth_headers).status_code == 202
        worker._active_engines[job_id] = "you-get"
        asyncio.run(worker._execute(job_id))
        recovered = client.get("/api/v1/jobs/" + job_id, headers=auth_headers).json()
        assert recovered["status"] == "completed"
        assert recovered["output_engine"] == "yt-dlp"
        assert calls == ["you-get", "yt-dlp"]
        if case == "cookie":
            assert not list(work.parent.glob(".downloader-cookie-" + job_id + "-*"))
            assert "synthetic-only" not in job["log_text"]
    elif case == "stale-producer":
        assert calls == ["you-get"]
        assert job["status"] == "completed"
        assert job["output_engine"] is None
        assert load_completed_files(work) == [work / "result.mp4"]
        assert (settings.output_dir / job_id / "result.mp4").read_bytes() == b"verified primary fixture"
    else:
        assert job["status"] == ("cancelled" if case == "cancelled" else "failed"), job["error"]
        assert job["output_engine"] is None
        assert not job["output_files"]
        assert not load_completed_files(work)
        assert not (settings.output_dir / job_id).exists()
        if case == "failed":
            assert calls == ["you-get", "yt-dlp"]
            assert "yt-dlp fallback also failed" in job["error"]
            assert "limiting requests" in job["error"]
            assert "synthetic-private" not in job["error"]
        elif case == "manifest-failure":
            assert calls == ["you-get", "yt-dlp"]
        else:
            assert calls == ["you-get"]
            if case != "cancelled":
                assert "fallback also failed" not in job["error"]
    assert job_id not in worker._engine_deadlines


def test_provenance_keeps_requested_and_actual_engine(client, auth_headers):
    with client.app.state.session_factory() as session:
        job = Job(url=URL, engine="you-get", output_engine="yt-dlp",
                  owner_id="fixture-owner", status="completed",
                  output_files=[{"verified": True, "file_id": "12345", "filename": "fixture.mp4"}])
        session.add(job)
        session.commit()
    data = client.get("/api/v1/provenance", headers=auth_headers,
                      params={"owner_id": "fixture-owner", "file_id": "12345"}).json()
    assert data["engine"] == "you-get" and data["output_engine"] == "yt-dlp"
    assert client.get("/api/v1/provenance", headers=auth_headers,
                      params={"owner_id": "other-owner", "file_id": "12345"}).status_code == 404


def test_secondary_failure_mapping_is_fixed():
    for kind in EngineFailureKind:
        message = user_failure_message(YouGetFallbackFailure(kind))
        assert "yt-dlp fallback also failed" in message
        assert user_failure_message(EngineFailure(kind, "synthetic")) in message


def test_additive_producer_migration_preserves_existing_jobs(settings):
    from sqlalchemy import inspect, text
    from app.db import initialize_database, make_engine, make_session_factory

    engine = make_engine(settings.database_url)
    try:
        initialize_database(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            original = Job(url="https://example.test/fixture", engine="you-get", status="queued")
            session.add(original)
            session.commit()
            original_id = original.id
        # Simulate pre-feature schema; the existing row must survive upgrade.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE jobs DROP COLUMN output_engine"))
        initialize_database(engine)
        initialize_database(engine)  # idempotent additive migration
        assert "output_engine" in {column["name"] for column in inspect(engine).get_columns("jobs")}
        with factory() as session:
            migrated = session.get(Job, original_id)
            assert migrated.url == "https://example.test/fixture"
            assert migrated.engine == "you-get"
            assert migrated.output_engine is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("line,kind", [
    ("you-get:   No signatureCipher or url for itag=18", EngineFailureKind.UNSUPPORTED),
    ("\x1b[31;1myou-get: No signatureCipher or url for itag=140\x1b[0m", EngineFailureKind.UNSUPPORTED),
    ("you-get: [error] oops, something went wrong.", EngineFailureKind.UNKNOWN),
    ("you-get: [Failed] Unable to find base.js on the video page", None),
    ("INFO: you-get: No signatureCipher or url for itag=18", None),
    ("you-get: No signatureCipher or url for itag=18 https://example.test/secret", None),
])
def test_native_pinned_extractor_diagnostic_only(line, kind):
    from app.engine_failures import classify_cli_error
    assert classify_cli_error(line) == kind
