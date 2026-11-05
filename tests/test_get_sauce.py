from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.adapters.get_sauce import DEFAULTS, effective_settings, run_get_sauce
from app.main import validate_engine_settings
from app.worker import ENGINE_DEFAULTS


def test_get_sauce_uses_managed_output_and_validated_cli_settings(tmp_path: Path):
    binary = tmp_path / "fake-get-sauce"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "assert args[args.index('-s') + 1] == '2'\n"
        "assert args[args.index('-w') + 1] == '6'\n"
        "assert args[args.index('-T') + 1] == '20'\n"
        "assert args[args.index('-m') + 1] == 'cbz'\n"
        "assert args[args.index('-p') + 1] == '1,3-5'\n"
        "assert '-S' in args\n"
        "assert '-a' not in args\n"
        "assert args[-1] == 'https://example.com/media'\n"
        "root = Path(args[args.index('-O') + 1])\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "(root / 'output.cbz').write_bytes(b'archive')\n"
        "print('output.cbz', flush=True)\n"
    )
    binary.chmod(0o755)
    logs, updates = [], []

    async def on_log(message): logs.append(message)
    async def on_progress(data): updates.append(data)
    async def not_cancelled(): return False

    paths = asyncio.run(run_get_sauce(
        executable=str(binary), url="https://example.com/media",
        workdir=tmp_path / "job",
        global_settings={"workers": 6, "timeout_minutes": 20},
        overrides={"stream_index": 2, "merge": "cbz", "pages": "1,3-5", "subdirectory": True},
        on_progress=on_progress, on_log=on_log, is_cancelled=not_cancelled,
    ))
    assert len(paths) == 1
    assert paths[0].read_bytes() == b"archive"
    assert logs[0] == "Starting get-sauce"
    assert updates and updates[-1]["state"] == "downloading"


@pytest.mark.parametrize("invalid", [
    {"stream_index": -1}, {"stream_index": 10001}, {"stream_index": True},
    {"amount": -5}, {"workers": 0}, {"workers": "6"},
    {"timeout_minutes": 0}, {"timeout_minutes": 121},
    {"merge": ["cbz"]}, {"merge": "zip"}, {"subdirectory": "true"},
    {"pages": "../file"}, {"pages": "1,,3"}, {"pages": "0-2"},
    {"output_path": "/tmp/out"}, {"headers": "Cookie:secret"},
])
def test_get_sauce_settings_reject_invalid_values(invalid):
    with pytest.raises(ValueError):
        effective_settings({}, invalid)


def test_get_sauce_preset_and_job_api(client, auth_headers):
    options = client.get("/api/v1/engines/get-sauce/options", headers=auth_headers)
    assert options.status_code == 200
    assert {field["key"] for field in options.json()["fields"]} == set(DEFAULTS)
    saved = client.put("/api/v1/engines/get-sauce/settings", headers=auth_headers, json={
        "settings": {"workers": 6, "merge": "cbz", "subdirectory": True},
    })
    assert saved.status_code == 200, saved.text
    preset = client.post("/api/v1/presets", headers=auth_headers, json={
        "name": "Gallery settings", "engine": "get-sauce", "settings": {"stream_index": 1},
    })
    assert preset.status_code == 201, preset.text
    created = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/gallery", "engine": "get-sauce",
        "preset_id": preset.json()["id"], "overrides": {"amount": 3},
    })
    assert created.status_code == 202, created.text
    assert created.json()["engine"] == "get-sauce"
    assert created.json()["overrides"]["stream_index"] == 1
    assert created.json()["overrides"]["amount"] == 3
    invalid = client.put("/api/v1/engines/get-sauce/settings", headers=auth_headers, json={
        "settings": {"output_path": "/tmp/escape"},
    })
    assert invalid.status_code == 422
    assert "get-sauce" in ENGINE_DEFAULTS
