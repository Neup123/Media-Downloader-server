"""Retire only explicitly audited legacy core heads after replacements pass."""
from __future__ import annotations

from pathlib import Path

from scripts.cleanup_merged_branches import (
    AUDITED_SUPERSEDED_CORE_HEADS, audited_core_candidates,
    audited_core_port_verified,
)


def branch(name, sha, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def test_core_replacements_are_present_before_retiring_legacy_code():
    assert audited_core_port_verified()


def test_all_seven_reviewed_legacy_heads_are_explicit():
    assert len(AUDITED_SUPERSEDED_CORE_HEADS) == 7
    assert AUDITED_SUPERSEDED_CORE_HEADS["feature/svtplay-dl-engine"] == (
        "71bac03d1777e6a0c050f8e78ba3e99ea73705f3"
    )
    assert AUDITED_SUPERSEDED_CORE_HEADS["ops/retire-audited-core-pre-main-branches"] == (
        "2b86dd5709657f910effd1da2481e96690fc4ec9"
    )
    assert len(set(AUDITED_SUPERSEDED_CORE_HEADS)) == 7
    assert all(len(sha) == 40 for sha in AUDITED_SUPERSEDED_CORE_HEADS.values())


def test_retirement_is_exact_and_never_deletes_new_or_protected_work():
    heads = [branch(name, sha) for name, sha in AUDITED_SUPERSEDED_CORE_HEADS.items()]
    assert audited_core_candidates(heads, [], replacements_verified=True) == sorted(
        AUDITED_SUPERSEDED_CORE_HEADS.items()
    )
    assert audited_core_candidates(heads, [], replacements_verified=False) == []
    for name, sha in AUDITED_SUPERSEDED_CORE_HEADS.items():
        assert audited_core_candidates(
            [branch(name, "f"*40)], [], replacements_verified=True
        ) == []
        assert audited_core_candidates(
            [branch(name, sha, protected=True)], [], replacements_verified=True
        ) == []
        assert audited_core_candidates(
            [branch(name, sha)], [{"state": "open", "head": {"ref": name}}],
            replacements_verified=True
        ) == []
    assert audited_core_candidates(
        [branch("feature/unreviewed", "a"*40)], [], replacements_verified=True
    ) == []


def test_cleanup_runs_audited_scan_and_sha_lease_only_on_main():
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/safe-merged-branch-cleanup.yml").read_text()
    assert "branches: [main]" in workflow
    assert workflow.count("--retire-audited-core") == 2
    assert "--force-with-lease=refs/heads" in (
        Path(__file__).resolve().parents[1] /
        "scripts/cleanup_merged_branches.py").read_text()
