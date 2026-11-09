from pathlib import Path

import pytest
import yaml

from scripts.engine_candidate_gate import review_candidate
from scripts.engine_update_demonstration import prepare

PINS = "yt-dlp==2.0\ngallery-dl==2.0\nyou-get==2.0\nsvtplay-dl==1.0\n"


def metadata(version):
    return {"releases": {version: [{"packagetype": "bdist_wheel", "yanked": False,
                                   "digests": {"sha256": "a" * 64}}]}}


def test_real_upgrade_excludes_p2_and_pins_candidate_exactly():
    updated, plan, evidence = prepare(PINS, "published", lambda _: metadata("2.1"))
    assert evidence["is_version_bump"]
    assert not evidence["production_promotion_permitted"]
    assert "yt-dlp==2.1" in updated
    assert "svtplay-dl==1.0" in updated
    assert review_candidate(plan, {"updates": []})["change_count"] == 3


def test_current_published_fallback_requires_explicit_revalidation():
    updated, plan, evidence = prepare(PINS, "published", lambda _: metadata("2.0"))
    assert updated == PINS
    assert evidence["no_newer_upstream_release"]
    assert not evidence["is_version_bump"]
    with pytest.raises(ValueError, match="unchanged"):
        review_candidate(plan, {"updates": []})
    assert review_candidate(plan, {"updates": []}, revalidate_current=True)["current_pin_revalidation"]


def test_broken_candidate_fails_even_with_revalidation():
    updated, plan, _ = prepare(PINS, "broken", lambda _: metadata("2.1"))
    assert "yt-dlp==9999.0.0" in updated
    with pytest.raises(ValueError, match="SHA-256"):
        review_candidate(plan, {"updates": []}, revalidate_current=True)


@pytest.mark.parametrize("version", ["2.0", "2.0.1", "10.0"])
def test_upstream_comparison_drives_fallback(version):
    _, _, evidence = prepare(PINS, "published", lambda _: metadata(version))
    assert evidence["is_version_bump"] == (version in {"2.0.1", "10.0"})


def test_demonstrations_cannot_open_pr_merge_or_release_but_red_records_issue():
    workflow = yaml.safe_load(Path('.github/workflows/pinned-engine-updates.yml').read_text())
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    for command in ("gh pr create", "gh pr merge", "gh release create"):
        step = next(s for s in steps if command in s.get("run", ""))
        assert "inputs.demonstration == '' || inputs.demonstration == 'none'" in step["if"]
    blocked = next(s for s in steps if "gh issue create" in s.get("run", ""))
    assert "failure()" in blocked["if"]
    assert "inputs.demonstration" not in blocked["if"]
    detect = next(s for s in steps if s.get("id") == "detect")
    assert 'test "$GITHUB_REF" != "refs/heads/main"' in detect["run"]
    for command in ("python -m pytest -q", "docker build --tag", "python tests/e2e_live_webdav.py"):
        step = next(s for s in steps if command in s.get("run", ""))
        assert step["if"] == "steps.detect.outputs.changed == 'true'"


@pytest.mark.parametrize("version", ["1.9", "2.0.0"])
def test_fallback_never_downgrades_or_substitutes_a_different_pin(version):
    with pytest.raises(ValueError, match="refusing downgrade"):
        prepare(PINS, "published", lambda _: metadata(version))
