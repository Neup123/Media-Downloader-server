from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.generic import effective_settings, output_filename, run_generic


def test_generic_options_and_filename_are_safe():
    assert output_filename("https://example.com/files/movie.mp4?token=hidden", "") == "movie.mp4"
    assert output_filename("https://example.com/", "") == "download.bin"
    with pytest.raises(ValueError):
        effective_settings({}, {"filename": "../escape"})
    with pytest.raises(ValueError):
        effective_settings({}, {"filename": "/tmp/file"})
    with pytest.raises(ValueError):
        effective_settings({}, {"max_redirects": 100})
    with pytest.raises(ValueError, match="Unknown generic setting"):
        effective_settings({"unrecognized_option": True}, {})
    with pytest.raises(ValueError, match="Unknown generic setting"):
        effective_settings({}, {"follow_redirect": True})
    with pytest.raises(ValueError, match="must be objects"):
        effective_settings([], ["unexpected"])


def test_generic_cli_uses_service_managed_output(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.adapters.generic.resolve_http_target", lambda *_: "example.com:443:93.184.215.14")
    executable = tmp_path / "fake-curl"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "assert sys.argv[1] == '--disable'\n"
        "assert '--proto' in sys.argv\n"
        "assert '--location' not in sys.argv\n"
        "assert sys.argv[sys.argv.index('--resolve')+1] == 'example.com:443:93.184.215.14'\n"
        "assert sys.argv[sys.argv.index('--noproxy')+1] == '*'\n"
        "assert sys.argv[sys.argv.index('--proxy')+1] == ''\n"
        "path = pathlib.Path(sys.argv[sys.argv.index('--output')+1])\n"
        "path.write_bytes(b'direct download content')\n"
    )
    executable.chmod(0o755)
    progress = []

    async def on_progress(event): progress.append(event)
    async def on_log(_): pass
    async def cancelled(): return False

    files = asyncio.run(run_generic(
        executable=str(executable), url="https://example.com/sample.bin",
        workdir=tmp_path / "job",
        global_settings={}, overrides={},
        on_progress=on_progress, on_log=on_log, is_cancelled=cancelled,
    ))
    assert len(files) == 1
    assert files[0].name == "sample.bin"
    assert files[0].read_bytes() == b"direct download content"
    assert all(event["total_bytes"] is None for event in progress)


def test_generic_api_accepts_safe_overrides(client, auth_headers):
    settings = client.put("/api/v1/engines/generic/settings", headers=auth_headers, json={
        "settings": {"retry_count": 4, "filename": "archive.zip", "follow_redirects": False},
    })
    assert settings.status_code == 200, settings.text
    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/archive.zip",
        "engine": "generic",
        "overrides": {"filename": "second.zip"},
    })
    assert job.status_code == 202, job.text
    assert job.json()["engine"] == "generic"
    assert job.json()["overrides"]["filename"] == "second.zip"
    invalid = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/", "engine": "generic",
        "overrides": {"filename": "../../private"},
    })
    assert invalid.status_code == 422
