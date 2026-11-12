#!/usr/bin/env python3
"""Engine version probes: validate installed Python packages without running them."""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGES = {
    "telegram-engine": "Telethon",
    "musicdl-engine": "musicdl",
    "ddddocr-engine": "ddddocr",
}


def main() -> int:
    executable = Path(sys.argv[0]).name
    if executable not in PACKAGES or sys.argv[1:] != ["--version"]:
        print("Usage: telegram-engine|musicdl-engine|ddddocr-engine --version", file=sys.stderr)
        return 2
    try:
        installed_version = version(PACKAGES[executable])
    except PackageNotFoundError:
        print("Not installed", file=sys.stderr)
        return 1
    print(f"{PACKAGES[executable]} {installed_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
