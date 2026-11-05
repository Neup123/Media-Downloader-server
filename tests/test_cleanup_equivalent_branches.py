"""Cleanup must delete only content-equivalent tips, not unmerged work."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.cleanup_merged_branches import (
    equivalent_branch_candidates, same_changed_files, tree_files,
)

REPO = "Neup123/Media-Downloader-server"


def branch(name, sha="a" * 40, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def tree(*entries):
    return {"truncated": False, "tree": [
        {"path": path, "sha": sha, "mode": mode, "type": "blob"}
        for path, sha, mode in entries
    ]}


class SafeEquivalentCleanupTests(unittest.TestCase):
    def test_identical_squash_branch_is_retired_but_unique_work_survives(self):
        main_tree = tree(("feature.txt", "same", "100644"), ("other.txt", "new", "100644"))
        equivalent_tree = tree(("feature.txt", "same", "100644"), ("other.txt", "old", "100644"))
        unique_tree = tree(("feature.txt", "unique", "100644"))
        calls = {
            "compare/main...ops%2Fidentical": {
                "status": "diverged", "ahead_by": 4,
                "files": [{"filename": "feature.txt"}],
            },
            "compare/main...feature%2Fnew": {
                "status": "diverged", "ahead_by": 5,
                "files": [{"filename": "feature.txt"}],
            },
            "git/trees/main?recursive=1": main_tree,
            "git/trees/ops%2Fidentical?recursive=1": equivalent_tree,
            "git/trees/feature%2Fnew?recursive=1": unique_tree,
        }
        def fake_get(_repo, endpoint, _token):
            return calls[endpoint]
        with patch("scripts.cleanup_merged_branches.get_json", fake_get):
            result = equivalent_branch_candidates(
                REPO,
                [branch("main"), branch("ops/identical"), branch("feature/new")],
                [], "token", set(),
            )
        self.assertEqual(result, [("ops/identical", "a" * 40)])

    def test_only_true_ancestors_can_skip_tree_checks(self):
        comparisons = {
            "compare/main...old-ancestor": {"status": "behind", "ahead_by": 0},
            "compare/main...new-branch": {"status": "ahead", "ahead_by": 1, "files": []},
        }
        def fake_get(_repo, endpoint, _token):
            if endpoint in comparisons:
                return comparisons[endpoint]
            if endpoint.startswith("git/trees/"):
                return tree(("path", "same", "100644"))
            raise AssertionError(endpoint)
        with patch("scripts.cleanup_merged_branches.get_json", fake_get):
            result = equivalent_branch_candidates(
                REPO, [branch("old-ancestor"), branch("new-branch")],
                [], "token", set(),
            )
        self.assertEqual(result, [("old-ancestor", "a" * 40)])

    def test_active_pr_protected_and_exact_candidates_are_not_inspected(self):
        with patch("scripts.cleanup_merged_branches.get_json", side_effect=AssertionError("network call")):
            result = equivalent_branch_candidates(
                REPO,
                [branch("main"), branch("open"), branch("protected", protected=True),
                 branch("exact")],
                [{"state": "open", "head": {
                    "ref": "open", "repo": {"full_name": REPO}
                }}],
                "token", {"exact"},
            )
        self.assertEqual(result, [])

    def test_compare_requires_complete_file_list_and_matching_mode(self):
        matching = {"a": ("sha", "100644", "blob")}
        different_mode = {"a": ("sha", "100755", "blob")}
        comparison = {"status": "diverged", "ahead_by": 1,
                      "files": [{"filename": "a"}]}
        self.assertTrue(same_changed_files(comparison, matching, matching))
        self.assertFalse(same_changed_files(comparison, matching, different_mode))
        self.assertFalse(same_changed_files({**comparison, "files": None}, matching, matching))
        self.assertFalse(same_changed_files({**comparison, "ahead_by": 250}, matching, matching))
        self.assertFalse(same_changed_files({
            **comparison, "files": [{"filename": "a", "previous_filename": "old"}]
        }, matching, matching | {"old": ("old", "100644", "blob")}))
        with self.assertRaises(ValueError):
            tree_files({"truncated": True, "tree": []})

    def test_duplicate_lookup_disallows_empty_comparison_file_list(self):
        self.assertFalse(same_changed_files(
            {"status": "diverged", "ahead_by": 1, "files": []}, {}, {}
        ))


if __name__ == "__main__":
    unittest.main()
