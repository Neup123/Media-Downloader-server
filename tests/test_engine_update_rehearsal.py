"""Pinned engine update promotion rehearsal must fail closed, without releases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.engine_update_rehearsal import pinned_pypi_digest, rehearse


ROOT = Path(__file__).resolve().parents[1]
PINS = "yt-dlp==2026.10.26\nsvtplay-dl==4.211\n"
DOCKER = (
    "FROM python:3.13-slim-trixie AS runtime\n"
    "RUN apt-get update \\\n"
    "    && apt-get install -y --no-install-recommends "
    "aria2=1.37.0+debian-3 curl ffmpeg ca-certificates \\\n"
    "    && rm -rf /var/lib/apt/lists/*\n"
)


def release_data(version="2026.10.26", *, yanked=False, digest="a" * 64):
    return {"releases": {version: [
        {"packagetype": "bdist_wheel", "yanked": yanked, "digests": {"sha256": digest}}
    ]}}


def test_green_and_red_rehearsal_uses_exact_known_pins_without_mutating_source():
    before = (PINS, DOCKER)
    py, deb, report = rehearse(PINS, DOCKER, lambda _: release_data())
    assert (PINS, DOCKER) == before
    assert py["updates"] == [
        {"engine": "yt-dlp", "from": "0.0.0", "to": "2026.10.26", "artifact_sha256": "a" * 64}
    ]
    assert deb["updates"] == [
        {"engine": "aria2c", "package": "aria2", "from": "0", "to": "1.37.0+debian-3"}
    ]
    assert report["green"]["candidate_engines"] == ["aria2c", "yt-dlp"]
    assert report["blocked_p2"]["rejected"]
    assert "svtplay-dl" in report["blocked_p2"]["reason"]
    assert report["blocked_duplicate"]["rejected"]
    for field in ("release_created", "source_files_modified", "candidate_pr_created"):
        assert report[field] is False


@pytest.mark.parametrize("bad", [
    release_data(yanked=True),
    release_data(digest="not-a-sha"),
    {"releases": {}},
    {"releases": []},
    release_data("2026.11.1"),
    {"releases": {"2026.10.26": [{"packagetype": "bdist_wheel", "yanked": False}]}},
])
def test_fails_closed_on_missing_invalid_or_yanked_pin_artifact(bad):
    with pytest.raises(ValueError):
        pinned_pypi_digest(bad, "2026.10.26")


def test_prefers_verified_wheel_to_sdist():
    data = {"releases": {"2026.10.26": [
        {"packagetype": "sdist", "yanked": False, "digests": {"sha256": "b" * 64}},
        {"packagetype": "bdist_wheel", "yanked": False, "digests": {"sha256": "a" * 64}},
    ]}}
    assert pinned_pypi_digest(data, "2026.10.26") == "a" * 64


@pytest.mark.parametrize("req,docker", [
    ("gallery-dl==1.32.19\n", DOCKER),
    (PINS, DOCKER.replace("aria2=1.37.0+debian-3", "aria2")),
    (PINS.replace("svtplay-dl==4.211", ""), DOCKER),
])
def test_does_not_accept_missing_real_pins(req, docker):
    with pytest.raises((ValueError, KeyError)):
        rehearse(req, docker, lambda _: release_data())


def test_rehearsal_mode_never_creates_candidate_pr_or_issue_or_release():
    workflow = (ROOT / ".github/workflows/pinned-engine-updates.yml").read_text()
    assert "rehearsal_only:" in workflow
    assert "python -m scripts.engine_update_rehearsal" in workflow
    assert "engine-update-rehearsal.json" in workflow
    assert "inputs.rehearsal_only != true" in workflow
    assert workflow.count("inputs.rehearsal_only != true") >= 4
    assert "gh release create" in workflow  # normal approved workflow only
    e2e = (ROOT / ".github/workflows/live-nextcloud-e2e.yml").read_text()
    assert "scripts.engine_update_rehearsal" in e2e
    assert "Verify real P0 engine downloads and delivery to Nextcloud" in e2e
    assert e2e.index("scripts.engine_update_rehearsal") < e2e.index(
        "Verify real P0 engine downloads and delivery to Nextcloud"
    )
    assert "git diff --exit-code -- requirements.txt Dockerfile" in e2e


def test_real_core_requirements_and_docker_are_eligible_for_rehearsal():
    req = (ROOT / "requirements.txt").read_text()
    docker = (ROOT / "Dockerfile").read_text()
    py, deb, plan = rehearse(req, docker, lambda _: release_data(
        version=next(
            row.split("==", 1)[1] for row in req.splitlines()
            if row.startswith("yt-dlp==")
        )
    ))
    assert json.loads(json.dumps(plan))["green"]["change_count"] == 2
    assert py["updates"][0]["to"] in req
    assert deb["updates"][0]["to"] in docker
