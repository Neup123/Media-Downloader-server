"""Detect stable PyPI releases of bundled CLI engines and prepare pinned bumps.

Engine Python distributions use exact version pins in requirements.txt. PyPI's
documented JSON endpoint is used only in CI; no version discovery occurs when
the downloader runs. Non-Python engines are explicitly out of scope.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

PYPI_ENGINES = ("yt-dlp", "gallery-dl", "you-get", "svtplay-dl")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s#]+)$")
STABLE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def _version_tuple(text: str) -> tuple[int, ...]:
    if not STABLE.fullmatch(text):
        raise ValueError(f"Unsupported or prerelease version: {text}")
    return tuple(int(part) for part in text.split("."))


def newer(candidate: str, current: str) -> bool:
    left = _version_tuple(candidate)
    right = _version_tuple(current)
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)) > right + (0,) * (size - len(right))


def pins_from_requirements(content: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in content.splitlines():
        match = PIN.fullmatch(line.strip())
        if match and match.group(1).lower().replace("_", "-") in PYPI_ENGINES:
            package = match.group(1).lower().replace("_", "-")
            if package in versions:
                raise ValueError(f"Duplicate pinned engine: {package}")
            versions[package] = match.group(2)
    if not versions:
        raise ValueError("No pinned Python engines found in requirements.txt")
    return versions


def fetch_release_data(package: str) -> dict[str, Any]:
    if package not in PYPI_ENGINES:
        raise ValueError("Package is not a bundled engine")
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{package}/json",
        headers={"Accept": "application/json", "User-Agent": "downloader-server-update-check/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def select_stable_release(metadata: dict[str, Any]) -> tuple[str, str]:
    """Select newest stable non-yanked release with a SHA-256-dist artifact."""
    found: list[tuple[str, str]] = []
    for version, artifacts in metadata["releases"].items():
        if not STABLE.fullmatch(version) or not isinstance(artifacts, list):
            continue
        eligible = [
            artifact for artifact in artifacts
            if not artifact.get("yanked")
            and artifact.get("digests", {}).get("sha256")
            and artifact.get("packagetype") in {"bdist_wheel", "sdist"}
        ]
        if eligible:
            preferred = next((file for file in eligible if file["packagetype"] == "bdist_wheel"), eligible[0])
            found.append((version, preferred["digests"]["sha256"]))
    if not found:
        raise ValueError("No stable, non-yanked release with a verified artifact digest")
    return max(found, key=lambda item: _version_tuple(item[0]))


def plan_updates(requirements: str, release_reader=fetch_release_data) -> dict[str, Any]:
    pins = pins_from_requirements(requirements)
    updates = []
    for package, current in sorted(pins.items()):
        latest, digest = select_stable_release(release_reader(package))
        if newer(latest, current):
            updates.append({"engine": package, "from": current, "to": latest, "artifact_sha256": digest})
    return {"updates": updates, "checked": pins, "unmanaged_engine": "aria2c (Debian pin; separate update track)"}


def apply_updates(requirements: str, plan: dict[str, Any]) -> str:
    replacements = {change["engine"]: change for change in plan["updates"]}
    lines = requirements.splitlines(keepends=True)
    result = []
    for line in lines:
        match = PIN.fullmatch(line.strip())
        if not match:
            result.append(line)
            continue
        name = match.group(1).lower().replace("_", "-")
        change = replacements.get(name)
        if change is None:
            result.append(line)
            continue
        if match.group(2) != change["from"]:
            raise ValueError(f"Version changed during update planning: {name}")
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        result.append(f"{match.group(1)}=={change['to']}{ending}")
    return "".join(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    original = args.requirements.read_text(encoding="utf-8")
    report = plan_updates(original)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.apply and report["updates"]:
        args.requirements.write_text(apply_updates(original, report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
