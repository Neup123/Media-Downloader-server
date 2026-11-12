"""Engine failure contracts: offline diagnostics, public message and queued notes."""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from app.failure_messages import (
    CliFailed, LastErrorLine, UserFacingFailure, _ARIA2_ERRORS,
    blocked_engine_message, format_failure, timeout_message,
)


async def _progress(_):
    return None


async def _log(_):
    return None


async def _not_cancelled():
    return False


def _script(tmp_path, code):
    script = tmp_path / "fake-downloader.py"
    script.write_text(code)
    return str(script)


def test_last_error_collector_replaces_older_error_and_ignores_info():
    errors = LastErrorLine()
    errors.capture("ERROR: first")
    errors.capture("WARNING: no relevant failure")
    errors.capture("[error] latest failure")
    assert errors.value == "[error] latest failure"
    assert LastErrorLine().value is None


@pytest.mark.parametrize("name", ["you-get", "svtplay-dl", "get-sauce", "musicdl"])
def test_shared_cli_runner_preserves_last_error_and_raw_log(tmp_path, name):
    from app.adapters.common_cli import run_cli
    logs = []

    async def logger(line):
        logs.append(line)

    executable = _script(tmp_path, "print('ERROR: earlier', flush=True)\n"
                                   "print('error: final detail', flush=True)\n"
                                   "raise SystemExit(7)\n")
    with pytest.raises(CliFailed) as caught:
        asyncio.run(run_cli(name=name, argv=[sys.executable, executable], workdir=tmp_path / "out",
                            on_progress=_progress, on_log=logger, is_cancelled=_not_cancelled))
    assert caught.value.last_error == "error: final detail"
    assert "ERROR: earlier" in logs
    assert "error: final detail" in logs


def test_cli_exit_code_fallback_and_no_file(tmp_path):
    from app.adapters.common_cli import run_cli

    executable = _script(tmp_path, "print('FYI: no diagnostics', flush=True)\nraise SystemExit(3)\n")
    with pytest.raises(CliFailed, match="you-get exited with status 3") as caught:
        asyncio.run(run_cli(name="you-get", argv=[sys.executable, executable], workdir=tmp_path / "failed",
                            on_progress=_progress, on_log=_log, is_cancelled=_not_cancelled))
    assert format_failure("you-get", caught.value) == "you-get exited with status 3"

    executable = _script(tmp_path, "raise SystemExit(0)\n")
    with pytest.raises(RuntimeError) as missing:
        asyncio.run(run_cli(name="musicdl", argv=[sys.executable, executable], workdir=tmp_path / "empty",
                            on_progress=_progress, on_log=_log, is_cancelled=_not_cancelled))
    assert "reported success but produced no file" in format_failure("musicdl", missing.value)


@pytest.mark.parametrize("adapter,runner", [
    ("app.adapters.yt_dlp", "run_ytdlp"),
    ("app.adapters.gallery_dl", "run_gallery_dl"),
])
def test_specialized_runners_keep_last_error(tmp_path, adapter, runner, monkeypatch):
    import importlib

    module = importlib.import_module(adapter)
    script = _script(tmp_path, "print('ERROR: first', flush=True)\n"
                                   "print('ERROR: last reason', flush=True)\n"
                                   "raise SystemExit(1)\n")
    # These adapters prepend the executable themselves. Launch the test script
    # through Python so Windows does not try to execute a .py file directly.
    real_spawn = asyncio.create_subprocess_exec

    async def spawn_script(*argv, **kwargs):
        return await real_spawn(sys.executable, *argv, **kwargs)

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn_script)
    logs = []

    async def logger(line):
        logs.append(line)

    with pytest.raises(CliFailed) as caught:
        asyncio.run(getattr(module, runner)(
            executable=script, url="https://example.test/media", workdir=tmp_path / "out",
            global_settings={}, overrides={}, cookie_dir=tmp_path / "cookies",
            on_progress=_progress, on_log=logger, is_cancelled=_not_cancelled,
        ))
    assert caught.value.last_error == "ERROR: last reason"
    assert "ERROR: last reason" in logs


@pytest.mark.parametrize("adapter,runner", [
    ("app.adapters.common_cli", "run_cli"),
    ("app.adapters.yt_dlp", "run_ytdlp"),
    ("app.adapters.gallery_dl", "run_gallery_dl"),
])
def test_missing_executable_reports_install_message(tmp_path, adapter, runner):
    import importlib

    module = importlib.import_module(adapter)
    kwargs = dict(workdir=tmp_path / "out", on_progress=_progress,
                  on_log=_log, is_cancelled=_not_cancelled)
    if runner == "run_cli":
        kwargs.update(name="you-get", argv=[str(tmp_path / "no-binary")])
    else:
        kwargs.update(executable=str(tmp_path / "no-binary"), url="https://example.test/",
                      global_settings={}, overrides={}, cookie_dir=tmp_path / "cookies")
    with pytest.raises(UserFacingFailure) as caught:
        asyncio.run(getattr(module, runner)(**kwargs))
    assert "is not installed" in format_failure("yt-dlp", caught.value)
    assert "reinstall the engine" in format_failure("yt-dlp", caught.value)


@pytest.mark.parametrize("engine,reason,expected", [
    ("yt-dlp", "ERROR: Sign in to confirm you're not a bot", "cookies to the vault"),
    ("yt-dlp", "ERROR: Unsupported URL: https://example.test/", "no matching extractor"),
    ("yt-dlp", "ERROR: Video unavailable", "removed"),
    ("gallery-dl", "[error] 404 Not Found", "not found (404)"),
    ("gallery-dl", "[error] 401 Unauthorized", "(401)"),
    ("gallery-dl", "[error] 403 Forbidden", "(403)"),
    ("gallery-dl", "[error] login required", "saved cookies may have expired"),
    ("you-get", "error: age restricted", "Refresh the site's cookies"),
    ("svtplay-dl", "ERROR: geoblocked", "not available in your region"),
])
def test_extractor_mappings(engine, reason, expected):
    assert expected in format_failure(engine, CliFailed(engine, 1, reason))


def test_ytdlp_selected_cookies_get_refresh_instruction():
    error = CliFailed("yt-dlp", 1, "ERROR: login required")
    message = format_failure("yt-dlp", error, cookies_selected=True)
    assert "saved cookies may have expired" in message
    assert "re-export cookies.txt" in message


def test_you_get_youtube_fallback_keeps_ytdlp_reason(tmp_path, monkeypatch):
    from app.adapters import you_get

    async def failed_primary(**_):
        raise CliFailed("you-get", 3, "error: extractor broken")

    async def failed_fallback(**_):
        raise CliFailed("yt-dlp", 1, "ERROR: Video unavailable")

    monkeypatch.setattr(you_get, "run_cli", failed_primary)
    monkeypatch.setattr(you_get, "run_ytdlp", failed_fallback)
    with pytest.raises(UserFacingFailure) as caught:
        asyncio.run(you_get.run_you_get(
            executable="you-get", url="https://youtu.be/abc", workdir=tmp_path,
            global_settings={}, overrides={}, cookie_dir=tmp_path,
            on_progress=_progress, on_log=_log, is_cancelled=_not_cancelled,
        ))
    message = format_failure("you-get", caught.value)
    assert "tried yt-dlp as a fallback" in message
    assert "Video unavailable" in message
    assert "Try yt-dlp directly" in message


def test_timeout_and_blocked_notes():
    assert "after 86400s (engine: yt-dlp)" in timeout_message(86400, "yt-dlp")
    assert "Retry — transient" in timeout_message(86400, "yt-dlp")
    assert "engine disabled by owner" in blocked_engine_message("musicdl", {
        "enabled": False, "inactive_reason": "Engine disabled by owner",
    })
    assert "Telegram credentials are required" in blocked_engine_message("telegram", {
        "enabled": True, "inactive_reason": "missing session",
    })


def test_queue_note_is_persistent_and_released_on_enable(settings):
    from app.db import Job, initialize_database, make_engine, make_session_factory
    from app.worker import JobWorker

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        job = Job(url="https://example.test/a", engine="musicdl", status="queued",
                  progress={"state": "queued"})
        session.add(job)
        session.commit()
        job_id = job.id
    with factory() as session:
        JobWorker._update_queued_engine_notes(session, {
            "musicdl": {"enabled": False, "inactive_reason": "Engine disabled by owner"},
        })
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued"
        assert job.progress["reason"].startswith("Waiting:")
        before = job.log_text
        JobWorker._update_queued_engine_notes(session, {
            "musicdl": {"enabled": False, "inactive_reason": "Engine disabled by owner"},
        })
        assert session.get(Job, job_id).log_text == before
        JobWorker._update_queued_engine_notes(session, {})
        assert "reason" not in session.get(Job, job_id).progress
    engine.dispose()


def test_worker_timeout_persists_nonempty_public_error(settings, monkeypatch):
    from app.db import Job, initialize_database, make_engine, make_session_factory
    from app import worker as module

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        job = Job(url="https://example.test/a", engine="yt-dlp", status="queued",
                  progress={"state": "queued"})
        session.add(job)
        session.commit()
        job_id = job.id

    async def timeout(**_):
        raise TimeoutError

    monkeypatch.setattr(module, "run_ytdlp", timeout)
    monkeypatch.setattr(module, "preflight_source_dns", lambda _: None)
    test_settings = replace(settings, job_timeout_seconds=1)
    asyncio.run(module.JobWorker(factory, test_settings)._execute(job_id))
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert "after 1s (engine: yt-dlp)" in job.error
    engine.dispose()


@pytest.mark.parametrize("code", sorted(_ARIA2_ERRORS))
def test_every_documented_aria2_failure_code_is_mapped(code):
    message = format_failure("aria2c", RuntimeError(f"aria2 download failed ({code}): daemon text"))
    assert f"({code})" in message
    assert "daemon text" not in message


def test_aria2_corrected_codes_rpc_and_manifest():
    assert "not enough disk space" in format_failure("aria2c", RuntimeError("aria2 download failed (9): disk"))
    assert "option or option value is invalid" in format_failure("aria2c", RuntimeError("aria2 download failed (28): bad"))
    assert "refused the credentials" in format_failure("aria2c", RuntimeError("aria2 download failed (24): auth"))
    assert "network error" in format_failure("aria2c", RuntimeError("aria2 download failed (6): network"))
    assert "(999)" in format_failure("aria2c", RuntimeError("aria2 download failed (999): unknown"))
    rpc = format_failure("aria2c", RuntimeError("aria2 RPC aria2.tellStatus failed (-32602): invalid"))
    assert "(aria2.tellStatus): invalid" in rpc and "-32602" not in rpc
    for raw in ("Torrent contains too many files for manifest preview",
                "Torrent metadata contains an invalid path",
                "Torrent metadata contains an invalid relative path",
                "Torrent metadata contains invalid file size or index"):
        assert "file list cannot be previewed" in format_failure("aria2c", ValueError(raw))
    assert "known problem, being fixed" in format_failure(
        "aria2c", ValueError("Torrent metadata contains a path outside its preview directory"))


@pytest.mark.parametrize("code,detail,expected", [
    (22, "curl: (22) The requested URL returned error: 404", "404 Not Found"),
    (6, "curl: (6) Could not resolve host", "resolve the hostname"),
    (7, "curl: (7) Failed to connect", "connect to the server"),
    (28, "curl: (28) timeout", "too long to respond"),
    (47, "curl: (47) redirect", "Too many redirects"),
    (60, "curl: (60) SSL certificate", "certificate could not be verified"),
])
def test_curl_failures(code, detail, expected):
    assert expected in format_failure("generic", RuntimeError(f"curl failed with status {code}: {detail}"))


def test_no_file_messages_for_all_cli_engines():
    for engine in ("yt-dlp", "gallery-dl", "you-get", "svtplay-dl", "get-sauce", "musicdl", "generic"):
        assert "reported success but produced no file" in format_failure(
            engine, RuntimeError(f"{engine} completed without any output file"))


def _pyload_run(tmp_path, handler):
    from app.adapters.pyload import run_pyload

    return asyncio.run(run_pyload(
        url="https://example.test/file", workdir=tmp_path / "job",
        api_url="http://localhost:8000", api_key="pl_test", shared_root=tmp_path,
        run_ddddocr_opt_in=False, global_settings={}, overrides={},
        on_progress=_progress, on_log=_log, is_cancelled=_not_cancelled,
        transport=httpx.MockTransport(handler),
    ))


def test_pyload_enqueue_includes_daemon_reason(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"error": {"message": "hoster blocked"}})

    with pytest.raises(UserFacingFailure) as caught:
        _pyload_run(tmp_path, handler)
    assert "enqueue failed" in str(caught.value)
    assert "hoster blocked" in str(caught.value)


@pytest.mark.parametrize("links,expected", [
    ([{"status": 8, "name": "a.zip", "error": "Hoster limit"},
      {"status": 0, "name": "b.zip"}], "1 of 2 files"),
    ([{"status": "unknown", "name": "a.zip"}], "unexpected status"),
])
def test_pyload_failed_links_and_invalid_status(tmp_path, links, expected):
    def handler(request):
        route = request.url.path
        if route.endswith("add_package"):
            return httpx.Response(200, json=5)
        if route.endswith("is_captcha_waiting"):
            return httpx.Response(200, json=False)
        return httpx.Response(200, json={"links": links})

    with pytest.raises(UserFacingFailure) as caught:
        _pyload_run(tmp_path, handler)
    assert expected in str(caught.value)
    if expected.startswith("1 of"):
        assert "Hoster limit" in str(caught.value)


def test_pyload_service_unreachable_and_no_completed_links(tmp_path):
    from app.adapters.pyload import _finished_output_files

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(UserFacingFailure) as caught:
        _pyload_run(tmp_path, handler)
    assert "unreachable" in str(caught.value)
    with pytest.raises(RuntimeError) as missing:
        _finished_output_files({"links": []}, tmp_path, tmp_path)
    assert "nothing downloadable" in format_failure("pyload", missing.value)


@pytest.mark.parametrize("kind,expected", [
    ("FloodWaitError", "retry in 42 seconds"),
    ("ChannelPrivateError", "channel is private"),
    ("AuthKeyUnregisteredError", "session expired"),
    ("ConnectionError", "Could not reach Telegram"),
])
def test_telegram_errors_are_mapped_by_exception_type(tmp_path, monkeypatch, kind, expected):
    from app.adapters.telegram import run_telegram

    class FloodWaitError(Exception):
        seconds = 42

    class ChannelPrivateError(Exception):
        pass

    class AuthKeyUnregisteredError(Exception):
        pass

    errors = ModuleType("telethon.errors")
    errors.FloodWaitError = FloodWaitError
    errors.ChannelPrivateError = ChannelPrivateError
    errors.AuthKeyUnregisteredError = AuthKeyUnregisteredError
    sessions = ModuleType("telethon.sessions")
    sessions.StringSession = lambda value: value
    telethon = ModuleType("telethon")

    class Client:
        def __init__(self, *_):
            pass

        async def connect(self):
            cls = {"FloodWaitError": FloodWaitError,
                   "ChannelPrivateError": ChannelPrivateError,
                   "AuthKeyUnregisteredError": AuthKeyUnregisteredError,
                   "ConnectionError": ConnectionError}[kind]
            raise cls("simulated")

        async def disconnect(self):
            return None

    telethon.TelegramClient = Client
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.errors", errors)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)
    with pytest.raises(UserFacingFailure) as caught:
        asyncio.run(run_telegram(
            url="https://t.me/example/123", workdir=tmp_path,
            credentials={"api_id": 1, "api_hash": "hash", "session_string": "s"},
            global_settings={}, overrides={}, on_progress=_progress,
            on_log=_log, is_cancelled=_not_cancelled,
        ))
    assert expected in format_failure("telegram", caught.value)


def test_ocr_exception_is_logged_without_changing_manual_prompt(monkeypatch):
    from app import captcha

    def broken(_):
        raise RuntimeError("OCR model unavailable")

    monkeypatch.setattr(captcha, "_ocr", broken)
    messages = []

    async def logger(message):
        messages.append(message)

    with pytest.raises(captcha.CaptchaWaiting, match="manual action"):
        asyncio.run(captcha.solve_captcha(b"image", ddddocr_enabled=True, on_log=logger))
    assert "OCR model unavailable" in messages[0]


def test_worker_preserves_raw_line_in_log_and_maps_job_error(settings, monkeypatch):
    from app.db import Job, initialize_database, make_engine, make_session_factory
    from app import worker as module

    engine = make_engine(settings.database_url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        job = Job(url="https://example.test/a", engine="yt-dlp", status="queued",
                  progress={"state": "queued"})
        session.add(job)
        session.commit()
        job_id = job.id

    async def fail(**_):
        raise CliFailed("yt-dlp", 1, "ERROR: Unsupported URL")

    monkeypatch.setattr(module, "run_ytdlp", fail)
    monkeypatch.setattr(module, "preflight_source_dns", lambda _: None)
    asyncio.run(module.JobWorker(factory, settings)._execute(job_id))
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert "no matching extractor" in job.error
        assert "ERROR: Unsupported URL" in job.log_text
    engine.dispose()


def test_curl_adapter_logs_raw_stderr_on_failure(tmp_path, monkeypatch):
    from app.adapters import generic

    class Stream:
        async def read(self):
            return b"curl: (22) The requested URL returned error: 404\n"

    class Process:
        returncode = 22
        stdout = Stream()

    async def start(*_, **__):
        return Process()

    monkeypatch.setattr(generic, "resolve_http_target", lambda *args: None)
    monkeypatch.setattr(generic.asyncio, "create_subprocess_exec", start)
    logs = []

    async def logger(line):
        logs.append(line)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(generic.run_generic(
            executable="curl", url="https://example.test/file.bin", workdir=tmp_path,
            global_settings={}, overrides={}, on_progress=_progress,
            on_log=logger, is_cancelled=_not_cancelled,
        ))
    assert "404" in logs[-1]
    assert "404 Not Found" in format_failure("generic", caught.value)


def test_pyload_captcha_path_passes_job_logger_to_ocr(tmp_path, monkeypatch):
    from app.adapters import pyload
    from app.captcha import CaptchaWaiting

    async def broken_ocr(image, *, run_carefully, on_log):
        assert image == b"image"
        assert run_carefully
        await on_log("Local ddddocr OCR failed (RuntimeError): missing model")
        raise CaptchaWaiting("manual action")

    monkeypatch.setattr(pyload, "run_ddddocr", broken_ocr)
    logs = []

    async def logger(line):
        logs.append(line)

    def handler(request):
        if request.url.path.endswith("add_package"):
            return httpx.Response(200, json=10)
        if request.url.path.endswith("is_captcha_waiting"):
            return httpx.Response(200, json=True)
        return httpx.Response(200, json={"tid": 42, "data": {"image": "aW1hZ2U="}})

    with pytest.raises(CaptchaWaiting, match="owner action"):
        asyncio.run(pyload.run_pyload(
            url="https://example.test/file", workdir=tmp_path / "out",
            api_url="http://localhost:8000", api_key="pl_test", shared_root=tmp_path,
            run_ddddocr_opt_in=True, global_settings={}, overrides={},
            on_progress=_progress, on_log=logger, is_cancelled=_not_cancelled,
            transport=httpx.MockTransport(handler),
        ))
    assert "Local ddddocr OCR failed" in " ".join(logs)


@pytest.mark.parametrize("reason", [
    "ERROR: This video is private",
    "ERROR: video removed from server",
    "ERROR: not available in your country",
])
def test_yt_dlp_private_removed_or_region_blocked(reason):
    result = format_failure("yt-dlp", CliFailed("yt-dlp", 1, reason))
    assert "removed, made private, or region-blocked" in result
    assert "Check the link in a browser" in result


def test_svtplay_unavailable_maps_not_just_explicit_geoblock():
    result = format_failure("svtplay-dl", CliFailed("svtplay-dl", 1, "ERROR: unavailable"))
    assert "not available in your region or was removed" in result


def test_pyload_non_enqueue_api_error_contains_daemon_reason(tmp_path):
    def handler(request):
        if request.url.path.endswith("add_package"):
            return httpx.Response(200, json=2)
        return httpx.Response(200, json={"error": "rate limit reached"})

    with pytest.raises(RuntimeError, match="pyLoad reported an error: rate limit reached") as caught:
        _pyload_run(tmp_path, handler)
    assert "rate limit reached" in format_failure("pyload", caught.value)


def test_pyload_non_object_package_status(tmp_path):
    def handler(request):
        if request.url.path.endswith("add_package"):
            return httpx.Response(200, json=2)
        if request.url.path.endswith("is_captcha_waiting"):
            return httpx.Response(200, json=False)
        return httpx.Response(200, json=["not a package"])

    with pytest.raises(UserFacingFailure, match="unexpected status"):
        _pyload_run(tmp_path, handler)


def test_curl_binary_missing_is_reported(tmp_path, monkeypatch):
    from app.adapters import generic

    async def missing(*_, **__):
        raise FileNotFoundError("curl")

    monkeypatch.setattr(generic, "resolve_http_target", lambda *args: None)
    monkeypatch.setattr(generic.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(UserFacingFailure) as caught:
        asyncio.run(generic.run_generic(
            executable="curl", url="https://example.test/file.bin", workdir=tmp_path,
            global_settings={}, overrides={}, on_progress=_progress,
            on_log=_log, is_cancelled=_not_cancelled,
        ))
    assert "curl is not installed" in str(caught.value)
