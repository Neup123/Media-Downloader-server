"""Plan pinned get-sauce Go-module release updates from verified GitHub metadata.

The Docker build compiles a module release with Go's module verification.
GitHub release asset checksums are not installation hashes for `go install`.
This module does not authorize unattended upgrades without site-specific E2E.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

UPSTREAM = "gan-of-culture/get-sauce"
RELEASE_URL = "https://api.github.com/repos/gan-of-culture/get-sauce/releases/latest"
GO_INSTALL = re.compile(
    r"^RUN go install github[.]com/gan-of-culture/get-sauce@(v[0-9]+[.][0-9]+[.][0-9]+)[ \t]*$",
    re.MULTILINE,
)
STABLE_TAG = re.compile(r"v(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)")


def version_tuple(tag: str) -> tuple[int, int, int]:
    match = STABLE_TAG.fullmatch(tag)
    if match is None:
        raise ValueError("get-sauce version must be a stable vMAJOR.MINOR.PATCH release")
    return tuple(int(part) for part in match.groups())


def pinned_tag(dockerfile: str) -> str:
    matches = GO_INSTALL.findall(dockerfile)
    if len(matches) != 1:
        raise ValueError("Dockerfile must contain exactly one pinned get-sauce go install line")
    return matches[0]


def fetch_release() -> dict[str, Any]:
    request = Request(
        RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "downloader-server-get-sauce-release-check/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("GitHub release response exceeds size limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("GitHub release response must be a JSON object")
    return result


def latest_stable_tag(release: dict[str, Any]) -> str:
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Latest GitHub release must be published and stable")
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("Latest GitHub release has no valid version tag")
    version_tuple(tag)
    assets = release.get("assets")
    if not isinstance(assets, list) or not any(
        isinstance(asset, dict) and asset.get("name") == "checksums.txt"
        for asset in assets
    ):
        raise ValueError("Latest get-sauce release has no published checksum manifest")
    return tag


def plan_update(dockerfile: str, release: dict[str, Any]) -> dict[str, Any]:
    old = pinned_tag(dockerfile)
    latest = latest_stable_tag(release)
    return {
        "engine": "get-sauce",
        "from": old,
        "to": latest,
        "update_available": version_tuple(latest) > version_tuple(old),
        "upstream": RELEASE_URL,
        "status": "smoke-gated; no automatic merge or release",
    }


def apply_update(dockerfile: str, plan: dict[str, Any]) -> str:
    old = pinned_tag(dockerfile)
    if not plan.get("update_available"):
        return dockerfile
    if old != plan.get("from") or version_tuple(str(plan["to"])) <= version_tuple(old):
        raise ValueError("get-sauce pin changed or candidate is not newer")
    replacement = "RUN go install github.com/gan-of-culture/get-sauce@" + str(plan["to"])
    modified, count = GO_INSTALL.subn(replacement, dockerfile)
    if count != 1:
        raise ValueError("Expected one get-sauce source pin")
    return modified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = args.dockerfile.read_text(encoding="utf-8")
    plan = plan_update(original, fetch_release())
    args.report.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if args.apply and plan["update_available"]:
        args.dockerfile.write_text(apply_update(original, plan), encoding="utf-8")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
