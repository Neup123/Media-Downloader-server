from __future__ import annotations

from scripts.cleanup_merged_branches import cleanup_candidates


REPO = "Neup123/Media-Downloader-server"


def pr(ref, sha, *, base="phase-1/core-mvp", merged=True, state="closed", repo=REPO):
    return {
        "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo}},
        "base": {"ref": base},
        "state": state,
        "merged_at": "2026-11-05T20:00:00Z" if merged else None,
    }


def branch(name, sha, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def test_only_exact_merged_heads_on_integrated_base_are_removed():
    branches = [
        branch("main", "main"), branch("phase-1/core-mvp", "phase"),
        branch("feature/merged", "head"), branch("feature/changed-after-merge", "new"),
        branch("feature/active-pr", "active"), branch("feature/unmerged", "unmerged"),
        branch("feature/fork-same-name", "fork"), branch("feature/protected", "guard", True),
        branch("feature/no-pr", "nopull"),
    ]
    pulls = [
        pr("phase-1/core-mvp", "phase", base="main"),
        pr("feature/merged", "head"),
        pr("feature/changed-after-merge", "old"),
        pr("feature/active-pr", "active", state="open", merged=False),
        pr("feature/unmerged", "unmerged", merged=False),
        pr("feature/fork-same-name", "fork", repo="another-user/fork"),
        pr("feature/protected", "guard"),
    ]
    assert cleanup_candidates(REPO, branches, pulls) == [
        ("feature/merged", "head"), ("phase-1/core-mvp", "phase"),
    ]


def test_phase_branches_not_eligible_until_phase_is_merged_into_main():
    pulls = [pr("feature/merged", "head")]
    assert cleanup_candidates(REPO, [branch("feature/merged", "head")], pulls) == []


def test_an_open_pr_blocks_deleting_same_head_even_with_old_merged_pr():
    pulls = [pr("feature/active", "head", base="main"),
             pr("feature/active", "head", base="main", merged=False, state="open")]
    assert cleanup_candidates(REPO, [branch("feature/active", "head")], pulls) == []


def test_cleanup_requires_mutable_branch():
    pulls = [pr("feature/review", "head", base="main")]
    assert cleanup_candidates(REPO, [branch("feature/review", "head", protected=True)], pulls) == []
