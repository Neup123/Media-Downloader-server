"""P2 acceptance must prove persistence and SHA-256-stable re-delivery."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tests/e2e_p2_site_webdav.py").read_text(encoding="utf-8")


def manifest_checker():
    # Load the exact pure acceptance function without requiring live Docker,
    # network, credentials or importing the surrounding manual-only E2E.
    module = ast.parse(SOURCE)
    selected = [
        item for item in module.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "assert_same_completed_manifest"
    ]
    assert len(selected) == 1
    compiled = compile(
        ast.Module(body=selected, type_ignores=[]),
        str(ROOT / "tests/e2e_p2_site_webdav.py"), "exec",
    )
    env = {}
    exec(compiled, env)
    return env["assert_same_completed_manifest"]


def files():
    return [
        {"relative": "album/a.mp4", "size": 1250, "sha256": "a" * 64},
        {"relative": "album/b.jpg", "size": 800, "sha256": "b" * 64},
    ]


def test_stable_manifest_allows_reordering_but_never_content_changes():
    check = manifest_checker()
    check(files(), list(reversed(files())))
    for changed in (
        [],
        files()[:1],
        [{**files()[0], "sha256": "c" * 64}, files()[1]],
        [{**files()[0], "size": 1251}, files()[1]],
        [{**files()[0], "relative": "other.mp4"}, files()[1]],
    ):
        with pytest.raises(RuntimeError, match="changed across restart"):
            check(files(), changed)


def test_manual_p2_acceptance_runs_live_restart_and_verified_redelivery():
    source = SOURCE
    for required in (
        "restart_downloader_compose()",
        "check_persisted_job(job, restored)",
        "assert_same_completed_manifest(manifest, restored_manifest)",
        "assert_same_completed_manifest(manifest, final_manifest)",
        'client.post(f"/jobs/{job[\'id\']}/redeliver")',
        "verify_remote_files(restored, restored_manifest)",
        "verify_remote_files(redelivered, final_manifest)",
        "inspect_original_files(job",
        'delivery.json().get("password_configured") is not True',
        "NC_PASSWORD in delivery.text",
        "redelivered[\"id\"] != job[\"id\"]",
    ):
        assert required in source


def test_site_logs_and_job_failure_details_never_go_to_actions_output():
    source = SOURCE
    assert "inspect private diagnostics" in source
    assert 'job["error"]' not in source
    assert "print(job)" not in source
    assert "print(source)" not in source
    assert "print(url)" not in source
    # We still require the real remote streaming SHA-256 comparison and
    # media parsing; this is not a status-only E2E.
    assert "hashlib.sha256()" in source
    assert "digest.hexdigest() != expected[\"sha256\"]" in source
    assert '["ffprobe", "-v", "error"' in source


def test_manual_only_p2_workflow_preserves_ephemeral_credentials_and_cleanup():
    import yaml

    path = ROOT / ".github/workflows/manual-p2-site-webdav.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert "schedule" not in workflow and "push" not in workflow
    steps = workflow["jobs"]["p2-site-smoke"]["steps"]
    code = "\n".join(step.get("run", "") for step in steps)
    assert "tests/e2e_p2_site_webdav.py" in code
    assert "NEXTCLOUD_ADMIN_PASSWORD" in code
    assert "down --volumes --remove-orphans" in code
    assert all("gh pr merge" not in step.get("run", "") for step in steps)
    assert all("gh release" not in step.get("run", "") for step in steps)
    assert any(step.get("if") == "always()" for step in steps)
