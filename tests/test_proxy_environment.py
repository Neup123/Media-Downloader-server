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
