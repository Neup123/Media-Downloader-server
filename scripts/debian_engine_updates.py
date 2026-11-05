"""Plan pinned Debian aria2/curl updates from runtime-base apt-cache metadata.

The workflow retrieves apt policy inside python:3.13-slim-trixie. This parser
does not query a random host distribution, and dpkg performs Debian version
comparison. It cannot by itself guarantee that every architecture has the
same candidate package or that an image tag is immutable.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Callable

PACKAGES = {"aria2": "aria2c", "curl": "generic"}
VERSION = re.compile(r"[0-9][A-Za-z0-9.+:~\-]*")
INSTALL_PREFIX = "&& apt-get install -y --no-install-recommends "


def parse_candidates(policy: str) -> dict[str, str]:
    found: dict[str, str] = {}
    section: str | None = None
    visited: set[str] = set()
    for line in policy.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            section = line[:-1].strip()
            if section in PACKAGES:
                if section in visited:
                    raise ValueError(f"Repeated Debian policy section: {section}")
                visited.add(section)
            continue
        if section not in PACKAGES or not line.lstrip().startswith("Candidate:"):
            continue
        candidate = line.split("Candidate:", 1)[1].strip()
        if VERSION.fullmatch(candidate) is None:
            raise ValueError(f"No valid Debian candidate for {section}")
        if section in found:
            raise ValueError(f"Duplicate Debian candidate for {section}")
        found[section] = candidate
    if set(found) != set(PACKAGES):
        raise ValueError("Missing Debian apt candidates: " + ", ".join(sorted(set(PACKAGES) - set(found))))
    return found


def package_pins(dockerfile: str) -> dict[str, str | None]:
    lines = [line for line in dockerfile.splitlines() if INSTALL_PREFIX in line]
    if len(lines) != 1:
        raise ValueError("Exactly one Debian runtime install line is required")
    line = lines[0].split(INSTALL_PREFIX, 1)[1]
    tokens = line.split()
    result: dict[str, str | None] = {}
    for package in PACKAGES:
        matches = [token for token in tokens if token == package or token.startswith(package + "=")]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {package} package in the runtime install line")
        token = matches[0]
        version = token.split("=", 1)[1] if "=" in token else None
        if version is not None and VERSION.fullmatch(version) is None:
            raise ValueError(f"Invalid pinned Debian version for {package}")
        result[package] = version
    return result


def debian_is_newer(candidate: str, current: str) -> bool:
    for value in (candidate, current):
        if VERSION.fullmatch(value) is None:
            raise ValueError("Invalid Debian version string")
    result = subprocess.run(
        ["dpkg", "--compare-versions", candidate, "gt", current],
        capture_output=True, timeout=10, check=False,
    )
    if result.returncode not in {0, 1}:
        raise ValueError("dpkg could not compare Debian package versions")
    return result.returncode == 0


def plan_updates(
    dockerfile: str,
    candidates: dict[str, str],
    *,
    newer: Callable[[str, str], bool] = debian_is_newer,
) -> dict:
    current = package_pins(dockerfile)
    if set(candidates) != set(PACKAGES):
        raise ValueError("Both aria2 and curl candidates are required")
    updates = []
    for package, engine in PACKAGES.items():
        version = candidates[package]
        if VERSION.fullmatch(version) is None:
            raise ValueError(f"Invalid Debian candidate: {package}")
        pinned = current[package]
        if pinned is None or newer(version, pinned):
            updates.append({
                "engine": engine, "package": package,
                "from": pinned or "(unpinned)", "to": version,
            })
    return {
        "updates": updates,
        "checked": current,
        "candidates": candidates,
        "source": "apt-cache policy in python:3.13-slim-trixie (CI architecture)",
    }


def apply_updates(dockerfile: str, plan: dict) -> str:
    current = package_pins(dockerfile)
    updated = dockerfile
    for item in plan["updates"]:
        package = item["package"]
        if package not in PACKAGES or current[package] != (None if item["from"] == "(unpinned)" else item["from"]):
            raise ValueError(f"Debian engine package changed during update planning: {package}")
        new_version = item["to"]
        if VERSION.fullmatch(new_version) is None:
            raise ValueError(f"Invalid Debian package version: {package}")
        old_token = package if current[package] is None else f"{package}={current[package]}"
        new_token = f"{package}={new_version}"
        lines = updated.splitlines(keepends=True)
        matching = [index for index, line in enumerate(lines) if INSTALL_PREFIX in line]
        if len(matching) != 1:
            raise ValueError("Expected one runtime apt install command")
        index = matching[0]
        line = lines[index]
        pattern = re.compile(r"(?<!\S)" + re.escape(old_token) + r"(?=\s|$)")
        replaced, count = pattern.subn(new_token, line)
        if count != 1:
            raise ValueError(f"Could not update unique Debian token for {package}")
        lines[index] = replaced
        updated = "".join(lines)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = args.dockerfile.read_text(encoding="utf-8")
    candidates = parse_candidates(args.policy.read_text(encoding="utf-8"))
    report = plan_updates(original, candidates)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.apply and report["updates"]:
        args.dockerfile.write_text(apply_updates(original, report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
