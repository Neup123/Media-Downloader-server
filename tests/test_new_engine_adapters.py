"""Isolated contract tests for the new downloader engine adapters."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.musicdl import music_client, run_musicdl
from app.adapters.pyload import run_pyload, submit_pyload_captcha
from app.adapters.telegram import parse_telegram_message_link, run_telegram
from app.captcha import CaptchaWaiting


async def progress(_): pass
async def log(_): pass
async def not_cancelled(): return False


def test_telegram_post_url_scope():
    assert parse_telegram_message_link("https://t.me/valid_channel/123") == ("valid_channel", 123)
    assert parse_telegram_message_link("https://t.me/c/112233/456") == (-100112233, 456)
    for url in ("https://t.me/login", "https://evil.example/valid/123",
                "https://t.me.evil.example/valid/123", "file:///tmp/secret"):
        with pytest.raises(ValueError):
            parse_telegram_message_link(url)


def test_telegram_download_restricts_credentials_and_output(tmp_path, monkeypatch):
    observed = []
    class Session:
        def __init__(self, value):
            assert value == "authorized-session"
    class Client:
        def __init__(self, session, api_id, api_hash):
            assert api_id == 1 and api_hash == "f" * 32
        async def connect(self): observed.append("connect")
        async def is_user_authorized(self): return True
        async def get_messages(self, entity, ids):
            assert (entity, ids) == ("channel", 9)
            return SimpleNamespace(media=True)
        async def download_media(self, message, file, progress_callback):
            target = Path(file) / "track.mp3"
            target.write_bytes(b"music")
            await progress_callback(5, 5)
            return str(target)
        async def disconnect(self): observed.append("disconnect")
    telethon = types.ModuleType("telethon")
    telethon.TelegramClient = Client
    sessions = types.ModuleType("telethon.sessions")
    sessions.StringSession = Session
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)
    events, logs = [], []
    async def on_progress(event): events.append(event)
    async def on_log(message): logs.append(message)
    files = asyncio.run(run_telegram(
        url="https://t.me/channel/9", workdir=tmp_path / "download",
        credentials={"api_id": 1, "api_hash": "f" * 32, "session_string": "authorized-session"},
        global_settings={}, overrides={},
        on_progress=on_progress, on_log=on_log, is_cancelled=not_cancelled,
    ))
    assert files[0].read_bytes() == b"music"
    assert events[-1]["percent"] == 100.0
    assert observed == ["connect", "disconnect"]
    assert "authorized-session" not in " ".join(logs)


def test_musicdl_source_is_derived_from_supported_host():
    assert music_client("https://open.spotify.com/playlist/123") == "SpotifyMusicClient"
    with pytest.raises(ValueError):
        music_client("https://example.invalid/playlist/123")
    with pytest.raises(ValueError):
        music_client("https://open.spotify.com/playlist/123", "JamendoMusicClient")


def test_musicdl_runs_cli_as_argv_without_shell(tmp_path, monkeypatch):
    # Use the same Python interpreter on Linux and Windows. A shebang-only
    # executable is not runnable with CreateProcess on Windows.
    from app.adapters import common_cli, musicdl
    cli = tmp_path / "fake_musicdl.py"
    cli.write_text(
        "import pathlib,sys\\n"
        "assert sys.argv[1:] == ['-p', 'https://open.spotify.com/playlist/123', '-m', 'SpotifyMusicClient']\\n"
        "pathlib.Path('song.mp3').write_bytes(b'song')\\n"
    )

    async def run_platform_neutral_cli(*, name, argv, **kwargs):
        assert name == "musicdl"
        assert argv == [
            "musicdl", "-p", "https://open.spotify.com/playlist/123",
            "-m", "SpotifyMusicClient",
        ]
        return await common_cli.run_cli(
            name=name, argv=[sys.executable, str(cli), *argv[1:]], **kwargs,
        )

    monkeypatch.setattr(musicdl, "run_cli", run_platform_neutral_cli)
    files = asyncio.run(run_musicdl(
        executable="musicdl", url="https://open.spotify.com/playlist/123",
        workdir=tmp_path / "songs", global_settings={}, overrides={},
        on_progress=progress, on_log=log, is_cancelled=not_cancelled,
    ))
    assert len(files) == 1
    assert files[0].read_bytes() == b"song"


def test_pyload_rest_package_handoff_copies_completed_shared_files(tmp_path):
    shared = tmp_path / "shared"
    folder = shared / "album"
    folder.mkdir(parents=True)
    (folder / "song.mp3").write_bytes(b"song")
    seen = []
    def handler(request: httpx.Request):
        seen.append((request.url.path, request.headers.get("X-API-Key")))
        if request.url.path == "/api/add_package":
            assert json.loads(request.content) == {
                "name": "Core job", "links": ["https://example.org/file.dlc"], "dest": 1,
            }
            return httpx.Response(200, json=7)
        if request.url.path == "/api/is_captcha_waiting":
            return httpx.Response(200, json=False)
        if request.url.path == "/api/get_package_data":
            return httpx.Response(200, json={
                "pid": 7, "folder": "album", "links": [{"name": "song.mp3", "status": 0}],
                "sizedone": 4, "sizetotal": 4,
            })
        raise AssertionError(str(request.url))
    files = asyncio.run(run_pyload(
        url="https://example.org/file.dlc", workdir=tmp_path / "work",
        api_url="http://127.0.0.1:8000", api_key="pl_secret", shared_root=shared,
        run_ddddocr_opt_in=False, global_settings={}, overrides={},
        on_progress=progress, on_log=log, is_cancelled=not_cancelled,
        transport=httpx.MockTransport(handler),
    ))
    assert files[0].read_bytes() == b"song"
    assert all(key == "pl_secret" for _, key in seen)


def test_pyload_captcha_is_nonterminal_and_preserves_resume_ids(tmp_path):
    def handler(request: httpx.Request):
        if request.url.path == "/api/add_package":
            return httpx.Response(200, json=7)
        if request.url.path == "/api/is_captcha_waiting":
            return httpx.Response(200, json=True)
        if request.url.path == "/api/get_captcha_task":
            return httpx.Response(200, json={"tid": 42, "data": None})
        raise AssertionError(str(request.url))
    async def run():
        with pytest.raises(CaptchaWaiting) as error:
            await run_pyload(
                url="https://example.org/file.ccf", workdir=tmp_path / "work",
                api_url="http://127.0.0.1:8000", api_key="pl_secret",
                shared_root=tmp_path, run_ddddocr_opt_in=False,
                global_settings={}, overrides={},
                on_progress=progress, on_log=log, is_cancelled=not_cancelled,
                transport=httpx.MockTransport(handler),
            )
        assert error.value.task_id == 42
        assert error.value.package_id == 7
    asyncio.run(run())


def test_manual_captcha_answer_uses_post_form_not_query():
    observed = []
    def handler(request: httpx.Request):
        observed.append(request)
        return httpx.Response(200, json=None)
    asyncio.run(submit_pyload_captcha(
        api_url="http://127.0.0.1:8000", api_key="pl_secret", task_id=4,
        answer="private answer", transport=httpx.MockTransport(handler),
    ))
    assert len(observed) == 1
    assert observed[0].url.query == b""
    assert observed[0].method == "POST"
    assert b"private+answer" in observed[0].content


def test_engine_probe_uses_metadata_without_importing_model(monkeypatch, capsys):
    from app import engine_probe_cli
    monkeypatch.setattr(engine_probe_cli, "version", lambda package: "1.2.3")
    monkeypatch.setattr(sys, "argv", ["ddddocr-engine", "--version"])
    assert engine_probe_cli.main() == 0
    assert "ddddocr 1.2.3" in capsys.readouterr().out


def test_new_engine_available_only_if_requirements_met(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.main.shutil.which", lambda name: "/bin/" + name)
    monkeypatch.setattr("app.main.subprocess.run", lambda argv, **kw: SimpleNamespace(
        returncode=0, stdout="v1.2.3\n", stderr="",
    ))
    result = client.get("/api/v1/engines", headers=auth_headers)
    assert result.status_code == 200
    found = {item["name"]: item for item in result.json()}
    assert len(found) == 11
    assert found["telegram"]["available"] is False
    assert found["pyload"]["available"] is False
    assert found["ddddocr"]["available"] is False
    assert found["musicdl"]["available"] is True
    assert found["ddddocr"]["requires_run_carefully"] is True

    result = client.put("/api/v1/engines/ddddocr/policy",
        headers=auth_headers, json={"enabled": True, "run_carefully": True})
    assert result.status_code == 200
    found = {item["name"]: item for item in client.get("/api/v1/engines", headers=auth_headers).json()}
    assert found["ddddocr"]["available"] is True
    assert client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/captcha.png", "engine": "ddddocr",
    }).status_code == 422


def test_missing_engine_binary_or_failed_version_never_available(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.main.shutil.which", lambda name: None if name == "musicdl-engine" else "/bin/" + name)
    monkeypatch.setattr("app.main.subprocess.run", lambda argv, **kw: SimpleNamespace(
        returncode=2, stdout="", stderr="bad version",
    ))
    rows = client.get("/api/v1/engines", headers=auth_headers).json()
    assert all(item["available"] is False for item in rows)
    assert next(row for row in rows if row["name"] == "musicdl")["installed"] is False
