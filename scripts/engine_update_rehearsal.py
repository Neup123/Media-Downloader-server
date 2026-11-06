"""Live, non-publishing promotion rehearsal using the exact current engine pins.

The rehearsal deliberately simulates an older -> currently pinned version
transition. It never edits requirements, Dockerfiles or repository branches.
The pinned PyPI artifact digest is fetched from official release metadata; it
is NOT an installation hash-lock. This test proves the candidate policy,
while the existing workflow's Docker, engine and real WebDAV steps test the
actual current, known-good binaries. No real upgrade is asserted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from scripts.debian_engine_updates import package_pins
from scripts.engine_candidate_gate import CandidateBlocked, review_candidate
from scripts.engine_updates import fetch_release_data, pins_from_requirements


def pinned_pypi_digest(metadata: dict[str, Any], version: str) -> str:
    """Require a real, non-yanked digest in exact pinned release metadata."""
    from scripts.engine_candidate_gate import SHA256
    releases = metadata.get("releases")
    if not isinstance(releases, dict):
        raise ValueError("PyPI release metadata is missing the release map")
    artifacts = releases.get(version)
    if not isinstance(artifacts, list):
        raise ValueError("The exact pinned PyPI version has no release artifacts")
    eligible = sorted(
        (
            (0 if item.get("packagetype") == "bdist_wheel" else 1, item["digests"]["sha256"])
            for item in artifacts
            if isinstance(item, dict)
            and item.get("packagetype") in {"bdist_wheel", "sdist"}
            and item.get("yanked") is False
            and isinstance(item.get("digests"), dict)
            and isinstance(item["digests"].get("sha256"), str)
            and SHA256.fullmatch(item["digests"]["sha256"])
        )
    )
    if not eligible:
        raise ValueError("Pinned PyPI version lacks a non-yanked SHA-256 artifact")
    return eligible[0][1]


def rehearse(
    requirements: str,
    dockerfile: str,
    release_reader: Callable[[str], dict[str, Any]] = fetch_release_data,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Prove valid and blocked combined engine candidates without mutating pins."""
    pins = pins_from_requirements(requirements)
    docker_pins = package_pins(dockerfile)
    yt_pin = pins["yt-dlp"]
    aria_pin = docker_pins["aria2"]
    if not aria_pin or yt_pin == "0.0.0":
        raise ValueError("Rehearsal requires the actual pinned yt-dlp and aria2 versions")
    digest = pinned_pypi_digest(release_reader("yt-dlp"), yt_pin)
    python_report = {
        "updates": [{
            "engine": "yt-dlp", "from": "0.0.0", "to": yt_pin,
            "artifact_sha256": digest,
        }],
        "rehearsal": "synthetic previous version; real pinned current artifact",
    }
    debian_report = {
        "updates": [{
            "engine": "aria2c", "package": "aria2",
            "from": "0", "to": aria_pin,
        }],
        "rehearsal": "synthetic previous version; current Dockerfile Debian pin",
    }
    green = review_candidate(python_report, debian_report)
    if green["candidate_engines"] != ["aria2c", "yt-dlp"]:
        raise AssertionError("Rehearsal failed to exercise both pinned engine families")

    blocked = {
        "updates": [{
            "engine": "svtplay-dl", "from": "0.0.0",
            "to": pins["svtplay-dl"], "artifact_sha256": "f" * 64,
        }],
    }
    try:
        review_candidate(blocked, {"updates": []})
    except CandidateBlocked as exc:
        p2_reason = str(exc)
    else:
        raise AssertionError("An unverified P2 candidate unexpectedly passed")

    try:
        review_candidate({"updates": [python_report["updates"][0]] * 2}, {"updates": []})
    except ValueError as exc:
        duplicate_reason = str(exc)
    else:
        raise AssertionError("Duplicate candidate unexpectedly passed")

    report = {
        "status": "rehearsal-green-and-red; NOT actual engine upgrade",
        "green": green,
        "blocked_p2": {"rejected": True, "reason": p2_reason},
        "blocked_duplicate": {"rejected": True, "reason": duplicate_reason},
        "sources": {
            "pypi": "official exact pinned PyPI release artifact digest",
            "debian": "existing checked-in Dockerfile aria2 pin",
            "previous_versions": "synthetic fixtures, not recorded installed releases",
        },
        "source_files_modified": False,
        "candidate_pr_created": False,
        "release_created": False,
    }
    return python_report, debian_report, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--debian-report", type=Path, required=True)
    parser.add_argument("--rehearsal-report", type=Path, required=True)
    args = parser.parse_args()
    python_report, debian_report, rehearsal = rehearse(
        args.requirements.read_text(encoding="utf-8"),
        args.dockerfile.read_text(encoding="utf-8"),
    )
    for dest, result in (
        (args.python_report, python_report),
        (args.debian_report, debian_report),
        (args.rehearsal_report, rehearsal),
    ):
        dest.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("Pinned-version policy rehearsal: valid PyPI+Debian GREEN, unverified P2 RED, duplicate RED")
    print("No package versions, branches, PRs or releases were changed.")


if __name__ == "__main__":
    main()
