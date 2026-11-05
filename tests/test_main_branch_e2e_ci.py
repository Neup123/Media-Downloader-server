"""Main-branch core changes must retain real disposable Nextcloud acceptance."""
from pathlib import Path
import yaml


def test_live_webdav_ci_gates_main_pr_and_merged_default_branch():
    root = Path(__file__).resolve().parents[1]
    source = (root / ".github/workflows/live-nextcloud-e2e.yml").read_text()
    workflow = yaml.safe_load(source)
    triggers = workflow.get("on") or workflow.get(True)
    assert "main" in triggers["pull_request"]["branches"]
    assert "main" in triggers["push"]["branches"]
    assert "app/**" in triggers["push"]["paths"]
    assert ".github/workflows/live-nextcloud-e2e.yml" in triggers["push"]["paths"]
    assert workflow["jobs"]["live-webdav"]["steps"]
    assert "tests/e2e_live_webdav.py" in source
    assert "downloader-server:local" in source
    assert "docker compose --env-file" in source


def test_python_docker_and_webdav_checks_all_have_main_branch_path():
    root = Path(__file__).resolve().parents[1]
    testing = yaml.safe_load((root / ".github/workflows/test.yml").read_text())
    e2e = yaml.safe_load((root / ".github/workflows/live-nextcloud-e2e.yml").read_text())
    standard = testing.get("on") or testing.get(True)
    live = e2e.get("on") or e2e.get(True)
    assert "main" in standard["push"]["branches"]
    assert "pull_request" in standard and standard["pull_request"] is None
    assert "main" in live["pull_request"]["branches"]
