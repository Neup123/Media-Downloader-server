from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.adapters.yt_dlp import run_ytdlp
from app.discovery import inspect_ytdlp, summarize_metadata


def test_playlist_preview_summarizes_entries_without_exposing_urls():
    result = summarize_metadata({
        "title": "My playlist",
        "_type": "playlist",
        "entries": [
            {"id": "one", "title": "First item", "webpage_url": "https://example.com/?token=secret"},
            {"id": "two", "title": "Second item"},
        ],
    }, 5)
    assert result["kind"] == "playlist"
    assert [entry["index"] for entry in result["entries"]] == [1, 2]
    assert "secret" not in json.dumps(result)


def test_video_preview_formats_are_selectable():
    result = summarize_metadata({
        "title": "Single item",
        "formats": [
            {"format_id": "720p", "ext": "mp4", "resolution": "1280x720", "url": "https://signed.example/?token=secret"}
        ],
    }, 5)
    assert result["kind"] == "video"
    assert result["formats"][0]["format_id"] == "720p"
    assert "secret" not in json.dumps(result)


def test_preview_invokes_real_cli_shape_with_bounded_items(tmp_path: Path):
    fake = tmp_path / "fake-yt-dlp"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert '--flat-playlist' in sys.argv\n"
        "assert sys.argv[sys.argv.index('--playlist-end')+1] == '3'\n"
        "print(json.dumps({'title': 'Playlist', 'entries': [{'title': 'One'}, {'title': 'Two'}]}))\n"
    )
    fake.chmod(0o755)
    result = asyncio.run(inspect_ytdlp("https://example.com/playlist", executable=str(fake), max_entries=3))
    assert result["kind"] == "playlist"
    assert len(result["entries"]) == 2


def test_playlist_selector_rejects_unbounded_or_invalid_indices(client, auth_headers):
    valid = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/playlist", "engine": "yt-dlp",
        "overrides": {"allow_playlist": True, "playlist_items": "1,3-5"},
    })
    assert valid.status_code == 202, valid.text
    for selector in ("0", "1;2", "1, 2", "999999", "../1"):
        bad = client.post("/api/v1/jobs", headers=auth_headers, json={
            "url": "https://example.com/playlist", "engine": "yt-dlp",
            "overrides": {"allow_playlist": True, "playlist_items": selector},
        })
        assert bad.status_code == 422, (selector, bad.text)
    invalid = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/playlist", "engine": "yt-dlp",
        "overrides": {"allow_playlist": False, "playlist_items": "1-5"},
    })
    assert invalid.status_code == 422


def test_preview_api_requires_auth_and_sanitizes_output(client, auth_headers, monkeypatch):
    async def fake_preview(url, *, max_entries):
        return {"title": "Test", "kind": "video", "entries": [], "formats": []}

    monkeypatch.setattr("app.main.inspect_ytdlp", fake_preview)
    assert client.post("/api/v1/preview", json={"url": "https://example.com/video"}).status_code == 401
    ok = client.post("/api/v1/preview", headers=auth_headers, json={"url": "https://example.com/video"})
    assert ok.status_code == 200
    assert ok.json()["kind"] == "video"
    assert client.post("/api/v1/preview", headers=auth_headers, json={"url": "http://127.0.0.1/private"}).status_code == 422


def test_adapter_passes_explicit_playlist_indices(tmp_path: Path):
    executable = tmp_path / "fake-yt-dlp"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "assert '--yes-playlist' in sys.argv and '--no-playlist' not in sys.argv\n"
        "assert sys.argv[sys.argv.index('--playlist-items') + 1] == '1,3-4'\n"
        "output = pathlib.Path.cwd() / 'movie.mp4'\n"
        "output.write_bytes(b'media')\n"
        "print('__DL_FILE__' + str(output))\n"
    )
    executable.chmod(0o755)

    async def on_progress(_): pass
    async def on_log(_): pass
    async def is_cancelled(): return False

    files = asyncio.run(run_ytdlp(
        executable=str(executable), url="https://example.com/playlist",
        workdir=tmp_path / "work",
        global_settings={}, overrides={"allow_playlist": True, "playlist_items": "1,3-4"},
        cookie_dir=tmp_path / "cookies",
        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
    ))
    assert len(files) == 1 and files[0].read_bytes() == b"media"
