"""Guard real Compose, restart, and WebDAV acceptance from regression."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
from unittest.mock import patch

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _record(job_id="original-id", filename="sample.mp4", sha="a"):
    return {
        "id": job_id,
        "engine": "yt-dlp",
        "status": "completed",
        "log_text": "download finished\n",
        "output_files": [{
            "filename": filename,
            "path": f"http://nextcloud/dav/{filename}",
            "size_bytes": 2000,
            "verified": True,
        }],
    }


def test_real_restart_contract_checks_identity_and_delivered_files():
    with patch.dict(os.environ, {
        "APP_API_TOKEN": "offline-token",
        "NEXTCLOUD_ADMIN_PASSWORD": "offline-password",
    }):
        ns = runpy.run_path(str(ROOT / "tests/e2e_live_webdav.py"))
    check = ns["check_persisted_job"]
    original = _record()
    check(original, _record())
    for changed in (
        _record(job_id="changed"),
        _record(filename="different.mp4"),
        {**_record(), "status": "queued"},
        {**_record(), "output_files": []},
        {**_record(), "log_text": ""},
    ):
        with pytest.raises(RuntimeError):
            check(original, changed)


def test_live_acceptance_runs_real_compose_restart_and_reuses_manifest():
    source = (ROOT / "tests/e2e_live_webdav.py").read_text()
    assert '"restart", "downloader-server"' in source
    assert '"/redeliver"' in source or "/redeliver" in source
    assert "check_persisted_job" in source
    assert "verify_downloaded_files(restored)" in source
    assert "verify_downloaded_files(redelivered)" in source


def test_standalone_compose_needs_no_nextcloud_and_rejects_anon_api():
    source = (ROOT / "tests/e2e_standalone_compose.py").read_text()
    assert 'anonymous.status_code == 401' in source
    assert 'jobs.json() == []' in source
    assert '"/healthz"' in source
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/live-nextcloud-e2e.yml").read_text()
    )
    assert {"standalone-compose", "live-webdav"} <= set(workflow["jobs"])
    standalone = workflow["jobs"]["standalone-compose"]
    steps = "\n".join(str(s) for s in standalone["steps"])
    assert "compose.yaml up -d --build downloader-server" in steps
    assert "compose.yaml down --volumes --remove-orphans" in steps
    assert "e2e_standalone_compose.py" in steps
    assert "compose.integration.yaml" not in steps
    assert "NEXTCLOUD_ADMIN_PASSWORD" not in steps
    assert any(step.get("if") == "always()" for step in standalone["steps"])
