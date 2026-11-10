"""Offline red/green acceptance for the combined engine promotion policy."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.engine_candidate_gate import CandidateBlocked, review_candidate
from scripts.engine_update_diagnostics import (
    GENERIC_INTERPRETATION, PR_CREATION_PERMISSION_ERROR,
    blocked_issue_body, diagnostic_excerpt,
)


ROOT = Path(__file__).resolve().parents[1]


def pypi(engine="yt-dlp", *, old="2026.10.26", new="2026.11.1"):
    return {"updates": [{
        "engine": engine, "from": old, "to": new,
        "artifact_sha256": "a" * 64,
    }]}


def debian(engine="generic"):
    return {"updates": [{
        "engine": engine,
        "package": "curl" if engine == "generic" else "aria2",
        "from": "(unpinned)" if engine == "generic" else "1.37.0+debian-3",
        "to": "8.14.1-2+deb13u7" if engine == "generic" else "1.37.0+debian-4",
    }]}


def test_green_combined_python_debian_candidate_retains_all_required_gates():
    plan = review_candidate(pypi(), debian())
    assert plan["candidate_engines"] == ["generic", "yt-dlp"]
    assert plan["change_count"] == 2
    assert plan["changes"]["generic"]["source"] == "debian"
    assert plan["changes"]["yt-dlp"]["source"] == "pypi"
    assert len(plan["required_gates"]) >= 5
    assert not plan["pypi_artifact_sha256_is_pip_hash_lock"]
    assert not plan["auto_release_authorized"]


def test_green_single_engine_from_each_supported_distribution():
    for engine in ("yt-dlp", "gallery-dl", "you-get"):
        assert review_candidate(pypi(engine), {"updates": []})["candidate_engines"] == [engine]
    for engine in ("generic", "aria2c"):
        assert review_candidate({"updates": []}, debian(engine))["candidate_engines"] == [engine]


def test_red_unverified_p2_engine_never_automatically_merges():
    with pytest.raises(CandidateBlocked, match="svtplay-dl"):
        review_candidate(pypi("svtplay-dl"), debian())
    # get-sauce belongs to an independent smoke-blocked Go workflow.
    with pytest.raises(ValueError, match="Unknown"):
        review_candidate(pypi("get-sauce"), {"updates": []})


@pytest.mark.parametrize("bad", [
    {"updates": []},
    {"updates": [{"engine": "aria2c", "from": "1.0", "to": "1.1", "artifact_sha256": "a" * 64}]},
    {"updates": [{"engine": "yt-dlp", "from": "1.0", "to": "1.1", "artifact_sha256": "bad"}]},
    {"updates": [{"engine": "yt-dlp", "from": "1.0", "to": "1.0", "artifact_sha256": "a" * 64}]},
    {"updates": [{"engine": "yt-dlp", "from": "1.0", "to": "1.1\nCRLF", "artifact_sha256": "a" * 64}]},
    {"updates": [{"engine": "yt-dlp", "from": "1.0", "to": "1.1", "artifact_sha256": "a" * 64}] * 2},
])
def test_red_malformed_or_duplicate_python_updates(bad):
    with pytest.raises(ValueError):
        review_candidate(bad, {"updates": []})


@pytest.mark.parametrize("bad", [
    {"updates": [{"engine": "generic", "package": "aria2", "from": "(unpinned)", "to": "1.2"}]},
    {"updates": [{"engine": "aria2c", "package": "aria2", "from": "1.0", "to": "1.0"}]},
    {"updates": [{"engine": "generic", "package": "curl", "from": "1.0", "to": "1.1; rm"}]},
    {"updates": [{"engine": "unknown", "package": "curl", "from": "1.0", "to": "1.1"}]},
    {"updates": [{"engine": "generic", "package": [], "from": "1.0", "to": "1.1"}]},
])
def test_red_malformed_debian_changes(bad):
    with pytest.raises((ValueError, TypeError)):
        review_candidate({"updates": []}, bad)


def test_red_empty_candidate_refuses_to_create_a_release():
    with pytest.raises(ValueError, match="No eligible"):
        review_candidate({"updates": []}, {"updates": []})


def test_cli_green_and_red_no_partial_acceptance_artifacts(tmp_path: Path):
    py_file = tmp_path / "python.json"
    deb_file = tmp_path / "debian.json"
    result = tmp_path / "promotion.json"
    py_file.write_text(json.dumps(pypi()))
    deb_file.write_text(json.dumps(debian()))
    base = [sys.executable, "-m", "scripts.engine_candidate_gate",
            "--python-report", str(py_file), "--debian-report", str(deb_file),
            "--output", str(result)]
    proc = subprocess.run(base, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(result.read_text())["change_count"] == 2
    result.unlink()
    py_file.write_text(json.dumps(pypi("svtplay-dl")))
    blocked = subprocess.run(base, capture_output=True, text=True, check=False)
    assert blocked.returncode != 0
    assert not result.exists()


def test_workflow_checks_candidate_before_python_docker_webdav_and_release():
    workflow = (ROOT / ".github/workflows/pinned-engine-updates.yml").read_text()
    assert "python -m scripts.engine_candidate_gate" in workflow
    assert "engine-promotion-report.json" in workflow
    assert workflow.index("python -m scripts.engine_candidate_gate") < workflow.index("python -m pytest -q")
    assert workflow.index("python -m scripts.engine_candidate_gate") < workflow.index("gh pr merge")
    assert 'vars.DOWNLOADER_AUTOMATED_RELEASES_APPROVED == \'true\'' in workflow
    assert "gh pr view" in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert '--target "$merged_sha"' in workflow
    assert '--target main' not in workflow


def test_blocked_issue_identifies_actions_pr_creation_setting_not_candidate_rejection():
    error = "pull request create failed: GraphQL: " + PR_CREATION_PERMISSION_ERROR
    diagnostics = "===== engine-update-pr.log =====\nbranch pushed\n" + error + "\n"
    body = blocked_issue_body(
        "generic (unpinned) -> 8.14.1-2+deb13u7",
        "https://github.com/Neup123/Media-Downloader-server/actions/runs/42842171411",
        diagnostics,
    )
    assert error in body
    assert 'Allow GitHub Actions to create and approve pull requests' in body
    assert "can_approve_pull_request_reviews=false" in body
    assert "Settings > Actions > General > Workflow permissions" in body
    assert "no workflow-file permission change" in body
    assert "never acceptance-tested" in body
    assert GENERIC_INTERPRETATION not in body


def test_unclassified_blocker_keeps_original_generic_interpretation():
    body = blocked_issue_body("yt-dlp 1 -> 2", "https://example.invalid/run", "unknown failed gate")
    assert GENERIC_INTERPRETATION in body
    assert "can_approve_pull_request_reviews" not in body
    assert "unknown failed gate" in body


def test_long_diagnostic_excerpt_retains_first_failed_permission_gate():
    error = "pull request create failed: GraphQL: " + PR_CREATION_PERMISSION_ERROR
    diagnostics = "===== engine-update-pr.log =====\n" + error + "\n" + ("later log data\n" * 1600)
    excerpt = diagnostic_excerpt(diagnostics)
    assert len(excerpt) <= 12000
    assert error in excerpt


def test_workflow_captures_unteeed_gates_and_keeps_credential_masks_out_of_stdout_log():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/pinned-engine-updates.yml").read_text())
    assert workflow["permissions"] == {
        "contents": "write", "packages": "write", "pull-requests": "write", "issues": "write"
    }
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    for name, log in (
        ("Detect stable PyPI and Debian trixie release candidates", "engine-update-detect-gate.log"),
        ("Open a dedicated candidate PR (the remaining steps test that exact candidate)", "engine-update-pr.log"),
        ("Install pinned dependencies and test requirements", "engine-update-install-gate.log"),
        ("Create a deterministic legal media fixture from the candidate image", "engine-update-fixture-gate.log"),
        ("Merge green candidate (normal branch protection still applies)", "engine-update-promote-gate.log"),
        ("Cut a patch release from the tested merged main", "engine-update-release-gate.log"),
    ):
        step = next(s for s in steps if s.get("name") == name)
        assert step["run"].startswith("set -euo pipefail\n{\n")
        assert step["run"].rstrip().endswith("} 2>&1 | tee " + log)
    credentials = next(s for s in steps if s.get("name") == "Create ephemeral credentials (never committed)")
    assert "2>engine-update-credentials-gate.log" in credentials["run"]
    assert "2>&1 | tee engine-update-credentials-gate.log" not in credentials["run"]
    collector = next(s for s in steps if s.get("name") == "Collect sanitized diagnostic logs and release manifest")
    for log in ("engine-update-pr.log", "engine-update-fixture-gate.log",
                "engine-update-install-gate.log", "engine-update-credentials-gate.log",
                "engine-update-promote-gate.log", "engine-update-release-gate.log"):
        assert '"' + log + '"' in collector["run"]
    issue = next(s for s in steps if s.get("name") == "Open or refresh blocked-update issue on any red gate")
    assert "blocked_issue_body(summary, url, diagnostics)" in issue["run"]
    assert "failure()" in issue["if"]
