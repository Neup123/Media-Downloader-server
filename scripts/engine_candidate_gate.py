"""Fail-closed candidate acceptance before any engine-update Docker/WebDAV gate.

Version discovery and pin changes are separate. This validates the *complete*
combined PyPI + Debian candidate, including smoke coverage and provenance.
Passing this preliminary gate is NOT release approval: Python/Docker/real
disposable-Nextcloud acceptance and the explicit repo variable are still needed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scripts.engine_smoke_coverage import PYPI_ENGINE_SMOKE_VERIFIED

PYPI_ENGINES = frozenset({"yt-dlp", "gallery-dl", "you-get", "svtplay-dl"})
DEBIAN_PACKAGES = {"aria2": "aria2c", "curl": "generic"}
VERSION = re.compile(r"[0-9][0-9A-Za-z.:+_~-]{0,120}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CandidateBlocked(ValueError):
    """A candidate has no reviewed legal WebDAV engine fixture."""


def _changes(report: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("updates"), list):
        raise ValueError(f"{label} report must contain an updates array")
    return report["updates"]


def review_candidate(
    python_report: dict[str, Any], debian_report: dict[str, Any],
) -> dict[str, Any]:
    """Reject invalid/mixed/untested engine changes before expensive gates."""
    approved: dict[str, dict[str, str]] = {}
    all_changes: list[tuple[str, dict[str, Any]]] = [
        *(("pypi", change) for change in _changes(python_report, "PyPI")),
        *(("debian", change) for change in _changes(debian_report, "Debian")),
    ]
    if not all_changes:
        raise ValueError("No eligible pinned engine changes were proposed")

    for source, item in all_changes:
        if not isinstance(item, dict):
            raise ValueError("Each engine change must be an object")
        engine = item.get("engine")
        old, new = item.get("from"), item.get("to")
        if not isinstance(engine, str) or (
            source == "pypi" and engine not in PYPI_ENGINES
        ) or (
            source == "debian" and engine not in DEBIAN_PACKAGES.values()
        ):
            raise ValueError("Unknown or incorrectly sourced engine update")
        if (
            not isinstance(old, str) or not isinstance(new, str)
            or (not VERSION.fullmatch(old) and not (source == "debian" and old == "(unpinned)"))
            or not VERSION.fullmatch(new) or new == old
        ):
            raise ValueError("Invalid or unchanged version in engine candidate")
        if engine in approved:
            raise ValueError("Duplicate engine in combined candidate reports")
        if source == "pypi":
            digest = item.get("artifact_sha256")
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                raise ValueError("PyPI candidate is missing its exact artifact SHA-256")
        else:
            package = item.get("package")
            if not isinstance(package, str) or DEBIAN_PACKAGES.get(package) != engine:
                raise ValueError("Debian engine/package mapping is inconsistent")

        approved[engine] = {"source": source, "from": old, "to": new}

    # Go get-sauce has no unattended release path; the independent watcher
    # never merges, and P2 svtplay-dl is blocked pending real site-specific E2E.
    allowed = set(PYPI_ENGINE_SMOKE_VERIFIED) | set(DEBIAN_PACKAGES.values())
    unsupported = sorted(set(approved) - allowed)
    if unsupported:
        raise CandidateBlocked(
            "No verified legal Nextcloud WebDAV smoke fixture for: " +
            ", ".join(unsupported)
        )
    return {
        "status": "candidate-smoke-eligible; NOT release-approved",
        "candidate_engines": sorted(approved),
        "change_count": len(approved),
        "changes": {name: approved[name] for name in sorted(approved)},
        "required_gates": [
            "full Python and parser fixtures",
            "production Docker image build",
            "real disposable Nextcloud WebDAV delivery SHA-256",
            "explicit manual repository release approval",
            "exact merged commit == remote main before creating tag",
        ],
        "pypi_artifact_sha256_is_pip_hash_lock": False,
        "auto_release_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-report", type=Path, required=True)
    parser.add_argument("--debian-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = review_candidate(
        json.loads(args.python_report.read_text(encoding="utf-8")),
        json.loads(args.debian_report.read_text(encoding="utf-8")),
    )
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Accepted candidate source validation for: " + ", ".join(plan["candidate_engines"]))
    print("Full live acceptance and explicit release approval still required.")


if __name__ == "__main__":
    main()
