"""Regression: broken YouTube extraction falls back without changing direct files."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters import you_get


async def progress(_): pass
async def log(_): pass
async def not_cancelled(): return False


def test_you_get_direct_file_stays_on_you_get(monkeypatch, tmp_path):
    calls = []
    target = tmp_path / "direct.dat"
    target.write_bytes(b"direct")
    async def fake_cli(**kwargs):
        calls.append(kwargs["name"])
        return [target]
    async def fake_ytdlp(**kwargs):
        raise AssertionError("Direct file must not go to yt-dlp")
    monkeypatch.setattr(you_get, "run_cli", fake_cli)
    monkeypatch.setattr(you_get, "run_ytdlp", fake_ytdlp)
    found = asyncio.run(you_get.run_you_get(
        executable="you-get", url="https://example.org/direct.dat", workdir=tmp_path,
        global_settings={}, overrides={}, cookie_dir=tmp_path,
        on_progress=progress, on_log=log, is_cancelled=not_cancelled,
    ))
    assert found == [target]
    assert calls == ["you-get"]


def test_you_get_youtube_failure_uses_yt_dlp_fallback(monkeypatch, tmp_path):
    calls = []
    target = tmp_path / "video.mp4"
    target.write_bytes(b"video")
    async def broken_cli(**kwargs):
        calls.append("you-get")
        raise RuntimeError("you-get exited with status 1")
    async def good_ytdlp(**kwargs):
        assert kwargs["executable"] == "yt-dlp"
        assert kwargs["url"].startswith("https://www.youtube.com/")
        calls.append("yt-dlp")
        return [target]
    monkeypatch.setattr(you_get, "run_cli", broken_cli)
    monkeypatch.setattr(you_get, "run_ytdlp", good_ytdlp)
    found = asyncio.run(you_get.run_you_get(
        executable="you-get", url="https://www.youtube.com/watch?v=abc",
        workdir=tmp_path, global_settings={}, overrides={}, cookie_dir=tmp_path,
        on_progress=progress, on_log=log, is_cancelled=not_cancelled,
    ))
    assert found == [target]
    assert calls == ["you-get", "yt-dlp"]


def test_you_get_other_site_failure_is_not_masked(monkeypatch, tmp_path):
    async def broken_cli(**kwargs):
        raise RuntimeError("you-get exited with status 1")
    monkeypatch.setattr(you_get, "run_cli", broken_cli)
    with pytest.raises(RuntimeError, match="status 1"):
        asyncio.run(you_get.run_you_get(
            executable="you-get", url="https://example.org/direct.dat",
            workdir=tmp_path, global_settings={}, overrides={}, cookie_dir=tmp_path,
            on_progress=progress, on_log=log, is_cancelled=not_cancelled,
        ))
