"""Advanced yt-dlp options must never smuggle an unvalidated second URL."""

from __future__ import annotations

import asyncio

import pytest

from app.adapters.yt_dlp import run_ytdlp
from app.security import validate_extra_args


@pytest.mark.parametrize("args", [
    ["http://169.254.169.254/latest/meta-data"],
    ["https://127.0.0.1/admin"],
    ["--", "https://127.0.0.1/admin"],
    ["--socket-timeout", "15"],
    ["-x"],
    ["--proxy=http://127.0.0.1:8080"],
    ["--plugin-dirs=/tmp/plugin"],
    ["--external-downloader-args=--help"],
    ["--exec=echo invalid"],
    ["--config-locations=/tmp/config"],
    ["--download-archive=/tmp/file"],
    ["--add-header=Authorization: token"],
    ["--output=/tmp/out"],
    ["--no-warnings=true"],
    ["--retries=999999"],
    ["--concurrent-fragments=1000"],
    ["--sleep-interval=400"],
    ["--format-sort=https://127.0.0.1"],
    [123],
])
def test_rejects_unreviewed_or_unsafe_extra_args(args):
    with pytest.raises(ValueError):
        validate_extra_args(args)


@pytest.mark.parametrize("args", [
    [],
    ["--no-warnings"],
    ["--no-mtime", "--restrict-filenames"],
    ["--socket-timeout=15", "--retries=3"],
    ["--concurrent-fragments=4", "--limit-rate=200K"],
    ["--merge-output-format=mkv", "--sub-format=srt/ass/best"],
    ["--playlist-start=3", "--playlist-end=6"],
])
def test_accepts_explicitly_reviewed_option_shapes(args):
    assert validate_extra_args(args) == args


def test_rejects_arbitrary_second_url_before_engine_execution(tmp_path, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("Untrusted URL must not reach the downloader process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected)

    async def on_progress(_): pass
    async def on_log(_): pass
    async def is_cancelled(): return False

    with pytest.raises(ValueError, match="Positional"):
        asyncio.run(run_ytdlp(
            executable="yt-dlp",
            url="https://example.com/verified",
            workdir=tmp_path / "work",
            global_settings={},
            overrides={"extra_args": ["http://169.254.169.254/latest/meta-data"]},
            cookie_dir=tmp_path / "cookies",
            on_progress=on_progress, on_log=on_log,
            is_cancelled=is_cancelled,
        ))


def test_api_rejects_advanced_url_bypass_in_global_and_per_job_settings(client, auth_headers):
    for payload in [
        {"extra_args": ["http://127.0.0.1/private"]},
        {"extra_args": ["--proxy=http://127.0.0.1:8080"]},
        {"extra_args": ["--external-downloader=sh"]},
    ]:
        response = client.put("/api/v1/engines/yt-dlp/settings", headers=auth_headers, json={
            "settings": payload,
        })
        assert response.status_code == 422, response.text
        job = client.post("/api/v1/jobs", headers=auth_headers, json={
            "url": "https://example.com/public",
            "engine": "yt-dlp",
            "overrides": payload,
        })
        assert job.status_code == 422, job.text

    valid = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/public",
        "engine": "yt-dlp",
        "overrides": {"extra_args": ["--no-warnings", "--retries=3"]},
    })
    assert valid.status_code == 202
