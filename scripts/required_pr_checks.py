"""Fail closed unless each normal Core PR CI job has succeeded."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = frozenset({
    ("Test", "python-tests"),
    ("Test", "container-build"),
    ("Live Nextcloud WebDAV E2E", "standalone-compose"),
    ("Live Nextcloud WebDAV E2E", "live-webdav"),
})


def verify_checks(rows: object) -> None:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Malformed PR check evidence")
    for workflow, name in sorted(REQUIRED):
        matches = [row for row in rows
                   if row.get("workflow") == workflow and row.get("name") == name]
        if not matches or any(row.get("state") != "SUCCESS" for row in matches):
            raise ValueError(f"Required PR check is missing or not successful: {workflow} / {name}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.required_pr_checks CHECKS.json")
    try:
        verify_checks(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print("All four Core PR checks succeeded")


if __name__ == "__main__":
    main()
