from __future__ import annotations

import pytest

from scripts.engine_updates import (
    apply_updates, newer, pins_from_requirements, plan_updates,
    select_stable_release,
)


def artifacts(version, yanked=False):
    return [{
        "packagetype": "bdist_wheel",
        "filename": f"engine-{version}-py3-none-any.whl",
        "yanked": yanked,
        "digests": {"sha256": "a" * 64},
    }]


def test_semantic_numeric_version_ordering():
    assert newer("2026.10.26", "2026.9.19")
    assert newer("1.32.19", "1.32.9")
    assert not newer("1.2", "1.2.0")
    assert not newer("1.9", "1.10")
    with pytest.raises(ValueError):
        newer("2.0rc1", "1.9")


def test_release_selection_skips_yanked_prereleases_and_missing_hashes():
    metadata = {"releases": {
        "1.9": artifacts("1.9"),
        "1.10rc1": artifacts("1.10rc1"),
        "1.10": artifacts("1.10", yanked=True),
        "1.11": [{"packagetype": "sdist", "yanked": False, "digests": {}}],
        "1.12": artifacts("1.12"),
    }}
    assert select_stable_release(metadata) == ("1.12", "a" * 64)


def test_version_updates_are_pinned_and_preserve_unrelated_requirements():
    original = "fastapi==0.128.2\nyt-dlp==2026.10.26\ngallery-dl==1.32.19\n"
    def read(name):
        versions = {"yt-dlp": "2026.11.5", "gallery-dl": "1.32.19"}
        return {"releases": {versions[name]: artifacts(versions[name])}}

    plan = plan_updates(original, read)
    assert plan["checked"] == {"yt-dlp": "2026.10.26", "gallery-dl": "1.32.19"}
    assert plan["updates"] == [{
        "engine": "yt-dlp", "from": "2026.10.26",
        "to": "2026.11.5", "artifact_sha256": "a" * 64,
    }]
    bumped = apply_updates(original, plan)
    assert bumped == "fastapi==0.128.2\nyt-dlp==2026.11.5\ngallery-dl==1.32.19\n"


def test_does_not_downgrade_when_registry_reports_older_version():
    original = "you-get==0.4.1743\n"
    def read(_name):
        return {"releases": {"0.4.1700": artifacts("0.4.1700")}}
    assert plan_updates(original, read)["updates"] == []


def test_duplicate_pins_and_non_engine_pins():
    with pytest.raises(ValueError, match="Duplicate"):
        pins_from_requirements("yt-dlp==1.2\nyt-dlp==1.3\n")
    with pytest.raises(ValueError, match="No pinned"):
        pins_from_requirements("requests==2.32.5\n")


def test_candidate_plan_detects_concurrent_pin_changes():
    plan = {"updates": [{"engine": "yt-dlp", "from": "1.0", "to": "1.1"}]}
    with pytest.raises(ValueError, match="Version changed"):
        apply_updates("yt-dlp==1.2\n", plan)



def test_update_workflow_is_gated_and_has_blocked_issue_path():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/pinned-engine-updates.yml").read_text())
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"] == {
        "contents": "write", "pull-requests": "write", "issues": "write"
    }
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    descriptions = [step.get("name", "") for step in steps]
    assert any("Detect stable" in text for text in descriptions)
    assert any("disposable Nextcloud" in text for text in descriptions)
    assert any("SHA-256" in text for text in descriptions)
    assert any("Require a real smoke test" in text for text in descriptions)
    assert any("Merge green candidate" in text for text in descriptions)
    assert any("blocked-update issue" in text for text in descriptions)
    for item in steps:
        if "Merge green candidate" in item.get("name", ""):
            assert "success()" in item["if"]
        if "blocked-update issue" in item.get("name", ""):
            assert "failure()" in item["if"]
