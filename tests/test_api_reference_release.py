"""Guarantee the served reference and product release stay synchronized."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from app.main import create_app
from app.version import product_version

ROOT = Path(__file__).resolve().parents[1]


def test_openapi_version_follows_release_metadata(settings):
    release = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert release == product_version()
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["info"]["version"] == release
    assert spec["info"]["title"] == "Downloader Server API"
    assert "X-API-Key" in spec["info"]["description"]
    assert "Authentication" in spec["info"]["description"]
    assert release != "0.1.0"
    assert [tag["name"] for tag in spec["tags"]] == ["jobs", "engines", "settings", "delivery"]
    assert all(tag["description"] for tag in spec["tags"])

    manifest = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "web/package-lock.json").read_text(encoding="utf-8"))
    assert manifest["version"] == lock["version"] == lock["packages"][""]["version"] == release

    app = create_app(settings, start_worker=False)
    try:
        assert app.version == release
    finally:
        app.state.engine.dispose()


def test_latest_release_workflow_guards_the_version_source():
    releases = list((ROOT / ".github/workflows").glob("release-v*.yml"))
    assert releases
    latest = max(releases, key=lambda path: tuple(int(part) for part in path.stem.removeprefix("release-v").split(".")))
    workflow = latest.read_text(encoding="utf-8")
    release = re.search(r"^\s+VERSION:\s+(\d+\.\d+\.\d+)\s*$", workflow, re.MULTILINE)
    assert release and release.group(1) == product_version()
    assert 'test "$(cat VERSION)" = "$VERSION"' in workflow


def test_served_openapi_info_reports_product_release(client):
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    spec = yaml.safe_load(response.text)
    assert spec["info"]["version"] == product_version()
    assert spec["info"]["title"] == "Downloader Server API"


def test_reference_link_is_admin_only_and_named_for_developers():
    source = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    assert source.count('href="/api-reference"') == 1
    assert '>For developers</a>' in source
    assert source.index('href="/api-reference"') > source.index("tab === 'administration' && connected")
    assert source.index('href="/api-reference"') < source.index("adminArea === 'access'")


def test_fallback_home_page_does_not_expose_developer_link(settings, monkeypatch, tmp_path):
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "WEB_DIST", tmp_path)
    app = main.create_app(settings, start_worker=False)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '/api-reference' not in response.text
    app.state.engine.dispose()
