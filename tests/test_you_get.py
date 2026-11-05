from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.you_get import effective_settings, run_you_get


def test_you_get_settings_are_validated():
    assert effective_settings({}, {"format": "720p"})["format"] == "720p"
    for value in ("../cookies.txt", "/tmp/cookies.txt", ".."):
        with pytest.raises(ValueError):
            effective_settings({}, {"cookies_file": value})
    with pytest.raises(ValueError):
        effective_settings({}, {"no_proxy": "false"})


def test_you_get_uses_service_managed_output_and_detects_files(tmp_path: Path):
    executable = tmp_path / "fake-you-get"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv\n"
        "assert args[args.index('--output-dir') + 1] == str(pathlib.Path.cwd())\n"
        "assert args[args.index('--format') + 1] == '720p'\n"
        "output = pathlib.Path.cwd() / 'video.mp4'\n"
        "output.write_bytes(b'complete video')\n"
        "print('Downloaded video.mp4', flush=True)\n"
    )
    executable.chmod(0o755)
    seen = []

    async def on_progress(item): seen.append(item)
    async def on_log(_): pass
    async def cancelled(): return False

    files = asyncio.run(run_you_get(
        executable=str(executable), url="https://example.com/video",
        workdir=tmp_path / "job",
        global_settings={}, overrides={"format": "720p"},
        cookie_dir=tmp_path / "cookies",
        on_progress=on_progress, on_log=on_log, is_cancelled=cancelled,
    ))
    assert len(files) == 1
    assert files[0].read_bytes() == b"complete video"
    assert seen and all(event["total_bytes"] is None for event in seen)


def test_you_get_cannot_enable_unsafe_options_through_api(client, auth_headers):
    good = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/video", "engine": "you-get",
        "overrides": {"format": "HD", "no_proxy": True},
    })
    assert good.status_code == 202, good.text
    blocked = client.put("/api/v1/engines/you-get/settings", headers=auth_headers, json={
        "settings": {"player": "sh"},
    })
    assert blocked.status_code == 422
    blocked_cookie = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/video", "engine": "you-get",
        "overrides": {"cookies_file": "../secrets"},
    })
    assert blocked_cookie.status_code == 422
