"""Offline red/green acceptance for the combined engine promotion policy."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.engine_candidate_gate import CandidateBlocked, review_candidate


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
    base = [sys.executable, str(ROOT / "scripts/engine_candidate_gate.py"),
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
    assert "scripts/engine_candidate_gate.py" in workflow
    assert "engine-promotion-report.json" in workflow
    assert workflow.index("scripts/engine_candidate_gate.py") < workflow.index("python -m pytest -q")
    assert workflow.index("scripts/engine_candidate_gate.py") < workflow.index("gh pr merge")
    assert 'vars.DOWNLOADER_AUTOMATED_RELEASES_APPROVED == \'true\'' in workflow
    assert "gh pr view" in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert '--target "$merged_sha"' in workflow
    assert '--target main' not in workflow
