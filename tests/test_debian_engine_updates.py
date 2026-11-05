from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.debian_engine_updates import (
    apply_updates, package_pins, parse_candidates, plan_updates,
)

DOCKERFILE = """FROM python:3.13-slim-trixie AS runtime
RUN apt-get update \\
    && apt-get install -y --no-install-recommends aria2=1.37.0+debian-3 curl ffmpeg ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
"""
POLICY = """aria2:
  Installed: (none)
  Candidate: 1.37.0+debian-3
  Version table:
     1.37.0+debian-3 500
curl:
  Installed: (none)
  Candidate: 8.14.1-2+deb13u7
  Version table:
     8.14.1-2+deb13u7 500
"""


def test_trixie_apt_policy_parsing_and_curl_initial_pin():
    candidates = parse_candidates(POLICY)
    assert candidates == {"aria2": "1.37.0+debian-3", "curl": "8.14.1-2+deb13u7"}
    current = package_pins(DOCKERFILE)
    assert current == {"aria2": "1.37.0+debian-3", "curl": None}
    report = plan_updates(DOCKERFILE, candidates, newer=lambda *_: False)
    assert report["updates"] == [{
        "engine": "generic", "package": "curl",
        "from": "(unpinned)", "to": "8.14.1-2+deb13u7",
    }]
    updated = apply_updates(DOCKERFILE, report)
    assert "aria2=1.37.0+debian-3 curl=8.14.1-2+deb13u7 ffmpeg ca-certificates" in updated
    assert "FROM python:3.13-slim-trixie" in updated


def test_newer_aria2_candidate_is_pinned_not_just_detected():
    candidates = {"aria2": "1.37.0+debian-4", "curl": "8.14.1-2+deb13u7"}
    report = plan_updates(DOCKERFILE, candidates, newer=lambda new, old: new.endswith("-4"))
    assert [u["engine"] for u in report["updates"]] == ["aria2c", "generic"]
    updated = apply_updates(DOCKERFILE, report)
    assert "aria2=1.37.0+debian-4 curl=8.14.1-2+deb13u7" in updated


def test_downgrade_or_equal_apt_candidate_is_never_applied():
    pinned = DOCKERFILE.replace(" curl ffmpeg", " curl=8.14.1-2+deb13u7 ffmpeg")
    report = plan_updates(pinned, {
        "aria2": "1.37.0+debian-3",
        "curl": "8.14.1-2+deb13u5",
    }, newer=lambda *_: False)
    assert report["updates"] == []
    assert apply_updates(pinned, report) == pinned


def test_missing_or_malformed_apt_candidate_fails_closed():
    for invalid in (
        "curl:\\n  Candidate: 8.1\\n",
        POLICY.replace("8.14.1-2+deb13u7", "(none)"),
        POLICY + POLICY,
    ):
        with pytest.raises(ValueError):
            parse_candidates(invalid)


def test_dockerfile_must_have_unique_package_references_and_original_pin():
    with pytest.raises(ValueError):
        package_pins(DOCKERFILE.replace(" curl ffmpeg", " ffmpeg"))
    with pytest.raises(ValueError):
        package_pins(DOCKERFILE + DOCKERFILE)
    report = plan_updates(DOCKERFILE, parse_candidates(POLICY), newer=lambda *_: False)
    with pytest.raises(ValueError, match="changed during"):
        apply_updates(DOCKERFILE.replace("curl ffmpeg", "curl=1.0 ffmpeg"), report)


def test_debian_updates_share_the_python_docker_and_webdav_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/pinned-engine-updates.yml").read_text())
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    scripts = "\\n".join(step.get("run", "") for step in steps)
    assert "scripts/debian_engine_updates.py" in scripts
    assert "apt-cache policy aria2 curl" in scripts
    assert "git add requirements.txt Dockerfile" in scripts
    assert "tests/e2e_live_webdav.py" in scripts
    assert "debian-engine-update-report.json" in scripts
    source = (root / "tests/e2e_live_webdav.py").read_text()
    assert '("yt-dlp", "aria2c", "gallery-dl", "generic", "you-get")' in source
    assert "hashlib.sha256(response.content).hexdigest()" in source
