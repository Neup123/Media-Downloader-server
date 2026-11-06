"""Global defaults and UI-submitted overrides must influence real CLI options.

These tests exercise adapter command construction without contacting public sites.
Real browser/production-Compose/WebDAV acceptance is separate and mandatory.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.aria2 import _effective_options
from app.adapters.yt_dlp import _effective_settings, run_ytdlp


def test_yt_dlp_override_wins_over_saved_global_cli_selector():
    global_defaults = {"format": "best[height<=360]/best"}
    override = {"format": "best[height<=240]/best"}
    effective = _effective_settings(global_defaults, override)
    assert effective["format"] == override["format"]
    assert _effective_settings(global_defaults, {})["format"] == global_defaults["format"]


def test_aria2_rpc_option_override_wins_over_saved_global():
    global_defaults = {"options": {"max-connection-per-server": "3", "split": "4"}}
    override = {"options": {"max-connection-per-server": "2"}}
    effective = _effective_options(global_defaults, override)
    assert effective["max-connection-per-server"] == "2"
    assert effective["split"] == "4"
    assert _effective_options(global_defaults, {})["max-connection-per-server"] == "3"


def test_yt_dlp_subprocess_actually_receives_overridden_format(tmp_path: Path, monkeypatch):
    args_seen = []

    class FakeStdout:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = FakeStdout()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_spawn(*argv, **kwargs):
        args_seen.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    workdir = tmp_path / "jobs" / "ui-job"
    workdir.mkdir(parents=True)
    (workdir / "sample.mp4").write_bytes(b"offline test file")

    async def callback(_event):
        pass

    async def is_cancelled():
        return False

    files = asyncio.run(run_ytdlp(
        executable="yt-dlp",
        url="https://example.org/sample.mp4",
        workdir=workdir,
        global_settings={"format": "best[height<=360]/best"},
        overrides={"format": "best[height<=240]/best"},
        cookie_dir=tmp_path / "cookies",
        on_progress=callback, on_log=callback, is_cancelled=is_cancelled,
    ))
    assert files == [workdir / "sample.mp4"]
    assert len(args_seen) == 1
    argv, kwargs = args_seen[0]
    assert argv[argv.index("-f") + 1] == "best[height<=240]/best"
    assert "best[height<=360]/best" not in argv
    assert argv[-1] == "https://example.org/sample.mp4"
    assert kwargs["cwd"] == str(workdir)


def test_browser_suite_runs_against_disposable_production_services():
    from pathlib import Path

    browser = (Path(__file__).parent / "e2e_browser_engine_settings.py").read_text()
    assert "sync_playwright()" in browser
    assert 'page.goto(BASE_URL' in browser
    assert 'Save yt-dlp defaults' in browser
    assert 'Save aria2c defaults' in browser
    assert 'submitted_job(page, api, "yt-dlp"' in browser
    assert "verify_webdav_bytes" in browser
    assert "await_completion" in browser
