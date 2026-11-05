"""Keep production frontend installation locked to npm's verified resolution."""
from __future__ import annotations

import json
from pathlib import Path


def test_frontend_lock_matches_exact_direct_dependencies_and_docker_uses_npm_ci():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "web/package-lock.json").read_text(encoding="utf-8"))

    assert lock["lockfileVersion"] >= 3
    assert lock["name"] == package["name"]
    assert lock["version"] == package["version"]
    root_entry = lock["packages"][""]
    assert root_entry["dependencies"] == package["dependencies"]
    assert root_entry["devDependencies"] == package["devDependencies"]
    for group in ("dependencies", "devDependencies"):
        for name, version in package[group].items():
            assert lock["packages"]["node_modules/" + name]["version"] == version

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY web/package.json web/package-lock.json ./" in dockerfile
    assert "RUN npm ci --no-audit --no-fund" in dockerfile
    assert "RUN npm install" not in dockerfile
