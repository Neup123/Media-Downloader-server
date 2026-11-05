from __future__ import annotations

from uuid import uuid4

import pytest

from scripts.p2_smoke_support import (
    relative_webdav_output, validate_site_smoke_source, verify_output_sets,
)

JOB = str(uuid4())
URL = f"http://nextcloud/remote.php/dav/files/admin/Downloader-E2E/{JOB}/album/cover.jpg"
DIGEST = "a" * 64


def test_only_explicit_public_safe_p2_sources_are_accepted():
    assert validate_site_smoke_source("svtplay-dl", "https://example.org/show/episode") == "https://example.org/show/episode"
    assert validate_site_smoke_source("get-sauce", "https://example.org/album") == "https://example.org/album"
    for engine, url in [
        ("yt-dlp", "https://example.org/media"),
        ("get-sauce", "http://127.0.0.1/local"),
        ("get-sauce", "https://example.org/media?token=abc"),
        ("svtplay-dl", "https://user:pass@example.org/media"),
        ("svtplay-dl", "ftp://example.org/media"),
        ("get-sauce", "https://example.org/path#private"),
    ]:
        with pytest.raises(ValueError):
            validate_site_smoke_source(engine, url)


def test_webdav_remote_path_matches_exact_workspace_output():
    assert relative_webdav_output(URL, JOB) == "album/cover.jpg"
    for invalid in [
        URL.replace("nextcloud", "different"),
        URL.replace("/album/cover.jpg", "/album/../../private.txt"),
        URL.replace(f"/{JOB}/", "/unrelated-job/"),
        URL + "?auth=private",
    ]:
        with pytest.raises(ValueError):
            relative_webdav_output(invalid, JOB)


def test_manifest_and_delivery_sets_have_to_match_byte_for_byte():
    manifest = [{"relative": "album/cover.jpg", "size": 123, "sha256": DIGEST}]
    delivery = [{"path": URL, "size_bytes": 123, "verified": True}]
    assert verify_output_sets(manifest, delivery, JOB) == {
        "album/cover.jpg": manifest[0],
    }
    with pytest.raises(ValueError):
        verify_output_sets(manifest, delivery + delivery, JOB)
    with pytest.raises(ValueError):
        verify_output_sets(manifest, [{"path": URL, "size_bytes": 122, "verified": True}], JOB)
    with pytest.raises(ValueError):
        verify_output_sets(manifest, [{"path": URL, "size_bytes": 123, "verified": False}], JOB)
    with pytest.raises(ValueError):
        verify_output_sets([], delivery, JOB)


def test_manual_p2_ci_does_not_enable_p2_auto_update_gate():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/manual-p2-site-webdav.yml").read_text())
    assert "p2-site-smoke" in workflow["jobs"]
    code = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["p2-site-smoke"]["steps"]
    )
    assert "tests/e2e_p2_site_webdav.py" in code
    assert "docker compose" in code
    assert "gh pr merge" not in code
    from scripts.engine_smoke_coverage import PYPI_ENGINE_SMOKE_VERIFIED
    assert "svtplay-dl" not in PYPI_ENGINE_SMOKE_VERIFIED
