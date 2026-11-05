from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.get_sauce_updates import (
    apply_update, latest_stable_tag, pinned_tag, plan_update, version_tuple,
)

DOCKERFILE = """FROM golang:1.27-alpine AS get-sauce-build
RUN go install github.com/gan-of-culture/get-sauce@v1.10.0
RUN echo done
"""


def release(tag="v1.11.0", *, draft=False, prerelease=False, checksums=True):
    assets = [{"name": "checksums.txt"}] if checksums else []
    return {
        "tag_name": tag, "draft": draft, "prerelease": prerelease,
        "assets": assets,
    }


def test_pin_and_stable_release_ordering():
    assert pinned_tag(DOCKERFILE) == "v1.10.0"
    assert version_tuple("v1.10.10") > version_tuple("v1.10.9")
    assert version_tuple("v2.0.0") > version_tuple("v1.99.99")
    for invalid in ("1.2.3", "v1.02.3", "v1.2.3-rc1", "v1.2", "v1.2.3/evil"):
        with pytest.raises(ValueError):
            version_tuple(invalid)


def test_refuses_ambiguous_or_unpinned_dockerfile():
    with pytest.raises(ValueError, match="exactly one"):
        pinned_tag("RUN go install github.com/gan-of-culture/get-sauce@latest\n")
    with pytest.raises(ValueError, match="exactly one"):
        pinned_tag(DOCKERFILE + DOCKERFILE)


@pytest.mark.parametrize("bad", [
    release(draft=True),
    release(prerelease=True),
    release("v1.11.0-rc1"),
    release("not-a-tag"),
    release(checksums=False),
    {"tag_name": "v1.11.0", "assets": [{"name": "checksums.txt"}]},
])
def test_unverifiable_latest_release_fails_closed(bad):
    with pytest.raises(ValueError):
        latest_stable_tag(bad)


def test_candidate_only_advances_explicit_pinned_module():
    plan = plan_update(DOCKERFILE, release())
    assert plan["from"] == "v1.10.0"
    assert plan["to"] == "v1.11.0"
    assert plan["update_available"] is True
    patched = apply_update(DOCKERFILE, plan)
    assert "go install github.com/gan-of-culture/get-sauce@v1.11.0" in patched
    assert "FROM golang:1.27-alpine" in patched
    assert patched.count("get-sauce@") == 1
    with pytest.raises(ValueError, match="pin changed"):
        apply_update(patched, plan)


def test_identical_or_older_latest_release_never_updates():
    for tag in ("v1.10.0", "v1.9.99"):
        plan = plan_update(DOCKERFILE, release(tag))
        assert plan["update_available"] is False
        assert apply_update(DOCKERFILE, plan) == DOCKERFILE


def test_get_sauce_workflow_never_unattended_merges_without_smoke():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/get-sauce-release-watch.yml").read_text(encoding="utf-8")
    )
    assert workflow["permissions"] == {
        "contents": "write", "pull-requests": "write", "issues": "write"
    }
    assert workflow["concurrency"]["cancel-in-progress"] is False
    commands = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["release-watch"]["steps"]
    )
    assert "scripts/get_sauce_updates.py" in commands
    assert "engine-update-blocked" in commands
    assert "gh pr create" in commands
    assert "gh issue create" in commands
    assert "gh pr merge" not in commands
    assert "gh release create" not in commands
