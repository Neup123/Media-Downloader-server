from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.svtplay import effective_settings, run_svtplay
from app.main import validate_engine_settings
from app.worker import ENGINE_DEFAULTS


def test_svtplay_cli_uses_managed_config_and_output_directory(tmp_path: Path):
    executable = tmp_path / "fake-svtplay-dl"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[args.index('--config')+1] == '/dev/null'\n"
        "output = pathlib.Path(args[args.index('--output')+1])\n"
        "assert args[args.index('--quality')+1] == 'best'\n"
        "assert args[args.index('--resolution')+1] == '720,1080'\n"
        "assert '--subtitle' in args and '--all-subtitles' in args\n"
        "assert '--all-episodes' in args\n"
        "assert args[args.index('--output-format')+1] == 'mkv'\n"
        "assert args[-1] == 'https://example.com/svt/video'\n"
        "(output / 'sample.mkv').write_bytes(b'video content')\n"
        "print('Wrote sample.mkv', flush=True)\n"
    )
    executable.chmod(0o755)
    logs, events = [], []

    async def on_progress(event): events.append(event)
    async def on_log(message): logs.append(message)
    async def not_cancelled(): return False

    files = asyncio.run(run_svtplay(
        executable=str(executable), url="https://example.com/svt/video",
        workdir=tmp_path / "job", global_settings={"quality": "best"},
        overrides={"resolution": "720,1080", "subtitles": True, "all_subtitles": True,
                   "all_episodes": True, "output_format": "mkv"},
        on_progress=on_progress, on_log=on_log, is_cancelled=not_cancelled,
    ))
    assert len(files) == 1 and files[0].read_bytes() == b"video content"
    assert any(event["state"] == "downloading" for event in events)
    assert "Starting svtplay-dl" in logs[0]


@pytest.mark.parametrize("invalid", [
    {"quality": "--config /tmp/evil"},
    {"resolution": "720,/tmp"},
    {"resolution": "20"},
    {"resolution": "1080,0"},
    {"subtitles": "true"},
    {"all_episodes": 1},
    {"output_format": "avi"},
    {"output_format": ["mkv"]},
])
def test_invalid_svtplay_settings_rejected(invalid):
    with pytest.raises(ValueError):
        effective_settings({}, invalid)


def test_svtplay_api_settings_reject_unknown_unmanaged_keys():
    with pytest.raises(ValueError, match="Unknown svtplay-dl"):
        validate_engine_settings("svtplay-dl", {"output": "/tmp/escape"})
    assert validate_engine_settings("svtplay-dl", {
        "resolution": "480,720", "subtitles": True,
    }) == {"resolution": "480,720", "subtitles": True}
    assert "svtplay-dl" in ENGINE_DEFAULTS


def test_svtplay_settings_preset_and_job_api(client, auth_headers):
    options = client.get("/api/v1/engines/svtplay-dl/options", headers=auth_headers)
    assert options.status_code == 200
    assert {"resolution", "quality", "subtitles", "all_subtitles", "all_episodes", "output_format"} == {
        item["key"] for item in options.json()["fields"]
    }
    saved = client.put("/api/v1/engines/svtplay-dl/settings", headers=auth_headers, json={
        "settings": {"resolution": "720,1080", "subtitles": True, "output_format": "mkv"},
    })
    assert saved.status_code == 200, saved.text
    preset = client.post("/api/v1/presets", headers=auth_headers, json={
        "name": "Nordic quality", "engine": "svtplay-dl", "settings": {"quality": "best"},
    })
    assert preset.status_code == 201, preset.text
    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://www.svtplay.se/video/example", "engine": "svtplay-dl",
        "preset_id": preset.json()["id"], "overrides": {"all_subtitles": True},
    })
    assert job.status_code == 202, job.text
    assert job.json()["engine"] == "svtplay-dl"
    assert job.json()["overrides"]["all_subtitles"] is True
    invalid = client.put("/api/v1/engines/svtplay-dl/settings", headers=auth_headers, json={
        "settings": {"output_path": "/tmp/unsafe"},
    })
    assert invalid.status_code == 422
