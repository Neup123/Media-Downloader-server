"""Every live WebDAV E2E run must test its own commit, not block on an old PR SHA."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_live_webdav_workflow_does_not_serialize_distinct_pr_commits():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/live-nextcloud-e2e.yml").read_text(encoding="utf-8")
    )
    group = workflow["concurrency"]["group"]
    assert "${{ github.ref }}" in group
    assert "${{ github.sha }}" in group
    assert workflow["concurrency"]["cancel-in-progress"] is False
    jobs = workflow["jobs"]
    assert "live-webdav" in jobs
    steps = jobs["live-webdav"]["steps"]
    combined = "\n".join(str(step.get("run", "")) for step in steps)
    assert "tests/e2e_live_webdav.py" in combined
    assert "docker compose" in combined
    assert "down --volumes" in combined
