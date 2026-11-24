"""Review-only discovery of new-engine/component releases; never change pins.

The existing pinned engine workflow remains the only candidate promotion path.
Missing integration pins are reported, not interpreted as installed engines.
Binary/service installers must expose their actual immutable version via the
named Dockerfile ARG (not an independent watch-only version).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import Version
from scripts.engine_updates import PIN, select_stable_release

SOURCES = {
    "pyLoad": ("pypi", "pyload-ng", "pyload/pyload"),
    "Streamlink": ("pypi", "streamlink", "streamlink/streamlink"),
    "N_m3u8DL-RE": ("github", "N_M3U8DL_RE_VERSION", "nilaoda/N_m3u8DL-RE"),
    "cobalt": ("github", "COBALT_VERSION", "imputnet/cobalt"),
    "BBDown": ("github", "BBDOWN_VERSION", "nilaoda/BBDown"),
    "musicdl": ("pypi", "musicdl", "CharlesPikachu/musicdl"),
    "ddddocr": ("pypi", "ddddocr", "sml2h3/ddddocr"),
    "Telegram": ("pypi", "telethon", "LonamiWebs/Telethon"),
}
VERSION_TEXT = re.compile(r"[vV]?[0-9][0-9A-Za-z.+-]{0,79}\Z")
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


def integration_pin(requirements: str, dockerfile: str, source: tuple[str, str, str]) -> str | None:
    kind, name, _ = source
    if kind == "pypi":
        pins = [m.group(2) for line in requirements.splitlines()
                if (m := PIN.fullmatch(line.strip())) and m.group(1).lower().replace("_", "-") == name]
    else:
        pins = re.findall(r"^ARG " + re.escape(name) + r"=([^\s#]+)[ \t]*$", dockerfile, re.M)
    if len(pins) > 1:
        raise ValueError("Duplicate integration pin")
    if not pins:
        return None
    if not VERSION_TEXT.fullmatch(pins[0]):
        raise ValueError("Invalid integration version")
    Version(pins[0])
    return pins[0]


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json",
                                   "User-Agent": "downloader-server-release-review/1"})
    with urlopen(request, timeout=20) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Release metadata exceeds bound")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected release object")
    return data


def check_engine(engine: str, requirements: str, dockerfile: str, reader=fetch_json) -> dict:
    kind, name, repo = SOURCES[engine]
    source_url = (f"https://pypi.org/pypi/{name}/json" if kind == "pypi"
                  else f"https://api.github.com/repos/{repo}/releases/latest")
    current = integration_pin(requirements, dockerfile, SOURCES[engine])
    result = {"engine": engine, "source": source_url, "upstream": f"https://github.com/{repo}",
              "current": current, "candidate": None, "review_required": False}
    if current is None:
        return {**result, "outcome": "not_integrated"}
    data = reader(source_url)
    if kind == "pypi":
        try:
            latest, digest = select_stable_release(data)
        except ValueError as exc:
            if str(exc) != "No stable, non-yanked release with a verified artifact digest":
                raise
            return {**result, "outcome": "no_eligible_stable_artifact"}
        if not SHA256.fullmatch(digest):
            raise ValueError("Missing valid distribution artifact digest")
        result["artifact_sha256"] = digest
    else:
        if data.get("draft") is not False or data.get("prerelease") is not False:
            raise ValueError("Release is not published stable")
        latest = data.get("tag_name")
    if not isinstance(latest, str) or not VERSION_TEXT.fullmatch(latest):
        raise ValueError("Unrecognized release tag")
    candidate = Version(latest)
    if candidate.is_prerelease or candidate.is_devrelease:
        raise ValueError("Candidate is not stable")
    update = candidate > Version(current)
    return {**result, "candidate": latest, "review_required": update,
            "outcome": "new_stable_release" if update else "no_new_stable_release"}


def plan(requirements: str, dockerfile: str, reader=fetch_json) -> list[dict]:
    results = []
    for engine in SOURCES:
        try:
            results.append(check_engine(engine, requirements, dockerfile, reader))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Do not expose raw upstream response or treat failed discovery as up-to-date.
            results.append({"engine": engine, "outcome": "discovery_failed",
                            "review_required": False})
    return results


def issue_title(row: dict) -> str:
    if row.get("engine") not in SOURCES or not VERSION_TEXT.fullmatch(row.get("candidate") or ""):
        raise ValueError("Invalid review record")
    # Candidate only, independent of the old pin or whether a prior issue is closed.
    return f"engine-update-blocked: {row['engine']} {row['candidate']}"


def record_reviews(rows: list[dict], run_url: str, gh=subprocess.check_output) -> None:
    for row in rows:
        if row.get("review_required") is not True:
            continue
        title = issue_title(row)
        existing = json.loads(gh(["gh", "issue", "list", "--state", "all", "--limit", "100",
                                  "--search", f'"{title}" in:title', "--json", "number,title"], text=True))
        if any(item["title"] == title for item in existing):
            continue
        body = ("Refs #153. Stable upstream version discovered; not an approved update.\n\n"
                f"Current pin: {row['current']}\nCandidate: {row['candidate']}\n"
                f"Official upstream: {row['upstream']}\nMetadata: {row['source']}\nRun: {run_url}\n\n"
                "Review license/activation and CLI/API compatibility before preparing a candidate PR. "
                "Require engine-specific legal download and WebDAV integrity acceptance, then the existing "
                "candidate gate. Never merge or release merely because a version was discovered.")
        gh(["gh", "issue", "create", "--title", title, "--label", "engine-update-blocked",
            "--body", body], text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--record-reviews", action="store_true")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    rows = plan(Path("requirements.txt").read_text(), Path("Dockerfile").read_text())
    args.report.write_text(json.dumps(rows, indent=2) + "\n")
    if args.record_reviews:
        record_reviews(rows, args.run_url)
    if any(row["outcome"] == "discovery_failed" for row in rows):
        raise SystemExit("At least one integrated engine could not be checked; see report")


if __name__ == "__main__":
    main()
