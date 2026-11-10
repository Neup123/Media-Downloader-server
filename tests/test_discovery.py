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
        "assert sys.argv[sys.argv.index('--playlist-start')+1] == '1'\n"
        "assert sys.argv[sys.argv.index('--playlist-end')+1] == '4'\n"
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
    async def fake_preview(url, *, max_entries, offset=0):
        return {"title": "Test", "kind": "video", "entries": [], "formats": []}

    monkeypatch.setattr("app.main.inspect_ytdlp", fake_preview)
    monkeypatch.setattr("app.main.preflight_source_dns", lambda url: None)
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



def test_preview_metadata_and_missing_values_are_honest():
    result = summarize_metadata({
        "title": "Album", "playlist_count": 3,
        "entries": [
            {
                "id": "a", "title": "Newest", "thumbnail": "https://cdn.example/a.jpg",
                "duration": 81, "timestamp": 1730000000, "release_timestamp": 1730001000,
                "upload_date": "20241027", "live_status": "is_live", "is_live": True,
                "webpage_url": "https://secret.example/?token=never-return",
            },
            {"id": "b", "title": "Older", "duration": None, "thumbnails": []},
        ],
    }, 2)
    assert result["ordering"] == "source"
    assert result["has_more"] is True and result["next_offset"] == 2
    assert result["entry_count"] == 3
    assert [e["title"] for e in result["entries"]] == ["Newest", "Older"]
    newest = result["entries"][0]
    assert newest["thumbnail"] == "https://cdn.example/a.jpg"
    assert newest["duration"] == 81
    assert newest["timestamp"] == 1730000000
    assert newest["release_timestamp"] == 1730001000
    assert newest["live_status"] == "is_live" and newest["is_live"] is True
    older = result["entries"][1]
    assert older["thumbnail"] is None and older["duration"] is None
    assert older["is_live"] is None and older["timestamp"] is None
    assert "never-return" not in json.dumps(result)


def test_preview_offset_lookahead_pages_past_100(tmp_path: Path):
    fake = tmp_path / "fake-yt-dlp"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "start = int(sys.argv[sys.argv.index('--playlist-start') + 1])\n"
        "end = int(sys.argv[sys.argv.index('--playlist-end') + 1])\n"
        "print(json.dumps({'title': 'Many', 'playlist_count': 161, "
        "'entries': [{'id': str(i), 'title': str(i)} for i in range(start, min(end, 161) + 1)]}))\n"
    )
    fake.chmod(0o755)
    first = asyncio.run(inspect_ytdlp("https://example.com/playlist", executable=str(fake), max_entries=100))
    second = asyncio.run(inspect_ytdlp("https://example.com/playlist", executable=str(fake), max_entries=100, offset=100))
    assert len(first["entries"]) == 100 and first["next_offset"] == 100
    assert len(second["entries"]) == 61 and second["next_offset"] is None
    assert first["entries"][0]["id"] == "1" and second["entries"][0]["id"] == "101"
    assert second["entries"][-1]["index"] == 161
    assert second["has_more"] is False


def test_video_preview_returns_live_fields_without_fabrication():
    result = summarize_metadata({
        "id": "live", "title": "Live now", "is_live": True,
        "live_status": "is_live", "timestamp": 1700000000,
    }, 10)
    assert result["kind"] == "video" and result["is_live"] is True
    assert result["live_status"] == "is_live"
    assert result["duration"] is None and result["thumbnail"] is None
    assert result["entry_count"] == 0 and result["next_offset"] is None


def test_preview_api_validates_offset(client, auth_headers, monkeypatch):
    seen = []

    async def fake_preview(url, *, max_entries, offset):
        seen.append((max_entries, offset))
        return {"kind": "playlist", "entries": [], "formats": [], "has_more": False}

    monkeypatch.setattr("app.main.inspect_ytdlp", fake_preview)
    monkeypatch.setattr("app.main.preflight_source_dns", lambda url: None)
    result = client.post("/api/v1/preview", headers=auth_headers, json={
        "url": "https://example.com/list", "max_entries": 25, "offset": 125,
    })
    assert result.status_code == 200
    assert seen == [(25, 125)]
    bad = client.post("/api/v1/preview", headers=auth_headers, json={
        "url": "https://example.com/list", "offset": -1,
    })
    assert bad.status_code == 422
