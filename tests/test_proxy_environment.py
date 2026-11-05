"""Network proxy variables must never change downloader routing implicitly."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from app.adapters.common_cli import run_cli
from app.adapters.process_env import downloader_environment
from app.adapters.yt_dlp import run_ytdlp
from app.discovery import inspect_ytdlp

PROXY_NAMES = {
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy",
}


def _suspicious_environment(monkeypatch):
    for index, name in enumerate(sorted(PROXY_NAMES)):
        monkeypatch.setenv(name.upper(), f"http://proxy.example.test:{8000 + index}")
        monkeypatch.setenv(name.lower(), f"http://proxy.example.test:{9000 + index}")
    monkeypatch.setenv("DOWNLOADER_KEEP_SETTING", "safe")


def test_process_environment_removes_all_case_variants_without_mutating_source():
    source = {
        "HTTPS_PROXY": "https://example.test",
        "http_proxy": "http://example.test",
        "AlL_PrOxY": "socks5://example.test",
        "FTP_PROXY": "http://example.test",
        "NO_PROXY": "*",
        "APP_API_TOKEN": "test-token",
        "PATH": "/usr/bin",
    }
    result = downloader_environment(source)
    assert result == {"APP_API_TOKEN": "test-token", "PATH": "/usr/bin"}
    assert source["HTTPS_PROXY"] == "https://example.test"


def test_generic_cli_cannot_inherit_proxy_configuration(tmp_path, monkeypatch):
    _suspicious_environment(monkeypatch)
    script = (
        "import json,os,pathlib;"
        "proxy=[k for k in os.environ if k.lower().endswith('_proxy')];"
        "pathlib.Path('child.json').write_text(json.dumps({"
        "'proxy':proxy,'retained':os.getenv('DOWNLOADER_KEEP_SETTING')}))"
    )
    async def on_progress(_): pass
    async def on_log(_): pass
    async def is_cancelled(): return False
    files = asyncio.run(run_cli(
        name="test", argv=[sys.executable, "-c", script],
        workdir=tmp_path, on_progress=on_progress,
        on_log=on_log, is_cancelled=is_cancelled,
    ))
    assert len(files) == 1
    result = json.loads((tmp_path / "child.json").read_text())
    assert result == {"proxy": [], "retained": "safe"}


def test_ytdlp_subprocess_cannot_inherit_proxy_configuration(tmp_path, monkeypatch):
    _suspicious_environment(monkeypatch)
    executable = tmp_path / "fake-yt-dlp"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import os,pathlib\n"
        "output=pathlib.Path.cwd()/'result.txt'\n"
        "output.write_text(','.join(k for k in os.environ if k.lower().endswith('_proxy')))\n"
        "print('__DL_FILE__' + str(output), flush=True)\n"
    )
    executable.chmod(0o755)
    async def on_progress(_): pass
    async def on_log(_): pass
    async def is_cancelled(): return False
    files = asyncio.run(run_ytdlp(
        executable=str(executable), url="https://example.com/media",
        workdir=tmp_path / "work",
        global_settings={}, overrides={}, cookie_dir=tmp_path / "cookies",
        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
    ))
    assert len(files) == 1
    assert files[0].read_text() == ""


def test_media_preview_subprocess_cannot_inherit_proxy_configuration(tmp_path, monkeypatch):
    _suspicious_environment(monkeypatch)
    executable = tmp_path / "fake-preview"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json,os\n"
        "proxies=[k for k in os.environ if k.lower().endswith('_proxy')]\n"
        "print(json.dumps({'title': 'clean' if not proxies else 'leaked',"
        "'id':'fixture','formats':[]}))\n"
    )
    executable.chmod(0o755)
    data = asyncio.run(inspect_ytdlp(
        "https://example.com/media", executable=str(executable)
    ))
    assert data["title"] == "clean"


def test_webdav_credentials_never_use_inherited_http_proxy(settings, monkeypatch):
    import httpx
    from cryptography.fernet import Fernet
    from app import delivery
    from app.db import DeliverySetting, initialize_database, make_engine, make_session_factory

    _suspicious_environment(monkeypatch)
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    filename = settings.data_dir / "jobs" / "proxy-test" / "clip.bin"
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_bytes(b"media")
    secret = Fernet(settings.encryption_key.encode("ascii")).encrypt(b"private-nextcloud-password").decode("ascii")
    with sessions() as session:
        session.add(DeliverySetting(
            id=1, mode="webdav",
            webdav_url="https://cloud.example.test/remote.php/dav/files/user",
            username="user", password_encrypted=secret, folder="Downloader",
        ))
        session.commit()

    async def handler(request: httpx.Request):
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            assert await request.aread() == b"media"
            return httpx.Response(201)
        if request.method == "PROPFIND":
            xml = (
                "<d:multistatus xmlns:d='DAV:'><d:response>"
                "<d:href>" + request.url.path + "</d:href>"
                "<d:propstat><d:prop><d:getcontentlength>5</d:getcontentlength>"
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                "</d:propstat></d:response></d:multistatus>"
            )
            return httpx.Response(207, content=xml)
        return httpx.Response(405)

    original = delivery.httpx.AsyncClient
    observed = []

    def guarded_client(*args, **kwargs):
        observed.append(kwargs.get("trust_env"))
        assert kwargs.get("trust_env") is False
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery.httpx, "AsyncClient", guarded_client)
    try:
        output = asyncio.run(delivery.deliver_files(
            [filename], "proxy-test", sessions, settings,
            transport=httpx.MockTransport(handler),
        ))
        assert len(output) == 1
        assert output[0]["verified"]
        assert observed == [False]
    finally:
        engine.dispose()
