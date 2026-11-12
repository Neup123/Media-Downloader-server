#!/usr/bin/env python3
"""Read the installed cobalt package version without starting its API."""
import json
import sys
from pathlib import Path

if sys.argv[1:] != ["--version"]:
    raise SystemExit(2)
root = Path("/opt/cobalt/api")
try:
    if not (root / "src/cobalt.js").is_file():
        raise ValueError("cobalt source missing")
    package = json.loads((root / "package.json").read_text())
    print("cobalt " + package["version"])
except (OSError, ValueError, KeyError):
    raise SystemExit(1)
