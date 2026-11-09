"""Prepare a branch-only published-version or intentionally broken gate demonstration.

No newer release: explicitly revalidate current published versions. Never fabricate
an old version or claim this fallback is a version bump. Workflow promotion is
unconditionally disabled for both modes, independently of repository approval.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.engine_updates import (
    apply_updates, fetch_release_data, newer, pins_from_requirements, select_stable_release,
)
from scripts.engine_smoke_coverage import PYPI_ENGINE_SMOKE_VERIFIED


def prepare(requirements: str, mode: str, release_reader=fetch_release_data):
    if mode not in {"published", "broken"}:
        raise ValueError("Unknown demonstration mode")
    pins = pins_from_requirements(requirements)
    checked = {}
    changes = []
    for engine in sorted(PYPI_ENGINE_SMOKE_VERIFIED):
        version, digest = select_stable_release(release_reader(engine))
        checked[engine] = {"pinned": pins[engine], "published": version, "artifact_sha256": digest}
        if newer(version, pins[engine]):
            changes.append({"engine": engine, "from": pins[engine], "to": version,
                            "artifact_sha256": digest})
    fallback = not changes
    if fallback:
        changes = [{"engine": engine, "from": pins[engine], "to": data["published"],
                    "artifact_sha256": data["artifact_sha256"]}
                   for engine, data in checked.items() if data["published"] == pins[engine]]
        if not changes:
            raise ValueError("No current or newer published candidate; refusing downgrade")
    if mode == "broken":
        # Deliberately absent provenance must fail the preliminary policy gate.
        changes = [{"engine": "yt-dlp", "from": pins["yt-dlp"], "to": "9999.0.0"}]
    plan = {"updates": changes, "checked": checked}
    evidence = {"mode": mode, "checked": checked,
                "no_newer_upstream_release": fallback,
                "is_version_bump": mode == "published" and not fallback,
                "production_promotion_permitted": False}
    return apply_updates(requirements, plan), plan, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("published", "broken"))
    args = parser.parse_args()
    requirements = Path("requirements.txt")
    updated, plan, evidence = prepare(requirements.read_text(encoding="utf-8"), args.mode)
    requirements.write_text(updated, encoding="utf-8")
    for name, data in (("engine-update-report.json", plan),
                       ("debian-engine-update-report.json", {"updates": []}),
                       ("engine-update-demonstration.json", evidence)):
        Path(name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
