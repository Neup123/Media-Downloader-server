"""Safely retire exact merged heads and byte-equivalent legacy branches.

This is conservative: squash-merged heads are eligible only if no new commits
were added after PR merge, no open PR references the branch, and the PR's target
was merged into main. Deletion uses a remote SHA lease; it cannot erase an
unexpected branch update. All other branches are left untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from urllib.parse import quote
from urllib.request import Request, urlopen


def list_pages(repository: str, collection: str, token: str) -> list[dict]:
    result: list[dict] = []
    for page in range(1, 21):
        url = (
            f"https://api.github.com/repos/{repository}/{collection}"
            f"{'&' if '?' in collection else '?'}per_page=100&page={page}"
        )
        request = Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("Unexpected GitHub list response")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("Pagination exceeded safety limit; not deleting any branches")


def cleanup_candidates(
    repository: str, branches: list[dict], pulls: list[dict],
) -> list[tuple[str, str]]:
    """Find exact, closed and merged PR heads; never guess from branch names."""
    open_heads: set[str] = set()
    eligible: set[tuple[str, str]] = set()
    phase_integrated = any(
        pr.get("merged_at")
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("head", {}).get("ref") == "phase-1/core-mvp"
        for pr in pulls
    )
    for pr in pulls:
        head = pr.get("head") or {}
        ref = head.get("ref")
        source_repo = (head.get("repo") or {}).get("full_name")
        if not ref or source_repo != repository:
            continue
        if pr.get("state") == "open":
            open_heads.add(ref)
            continue
        base = (pr.get("base") or {}).get("ref")
        if (
            pr.get("merged_at")
            and (base == "main" or (base == "phase-1/core-mvp" and phase_integrated))
            and isinstance(head.get("sha"), str)
        ):
            eligible.add((ref, head["sha"]))
    output: list[tuple[str, str]] = []
    for branch in branches:
        name = branch.get("name")
        sha = (branch.get("commit") or {}).get("sha")
        if (
            name and name != "main" and not branch.get("protected")
            and name not in open_heads and (name, sha) in eligible
        ):
            output.append((name, sha))
    return sorted(output)



def get_json(repository: str, endpoint: str, token: str) -> dict:
    """Fetch GitHub metadata with the same token as the checked-out repository."""
    url = f"https://api.github.com/repos/{repository}/{endpoint}"
    request = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Unexpected GitHub object response")
    return payload


def tree_files(tree: dict) -> dict[str, tuple[str, str, str]]:
    """Reject a truncated response; blob content AND file mode must match."""
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise ValueError("GitHub Git tree was truncated or missing")
    return {
        entry["path"]: (entry["sha"], entry["mode"], entry["type"])
        for entry in tree["tree"] if entry.get("type") in ("blob", "commit")
    }


def same_changed_files(
    comparison: dict, main: dict[str, tuple[str, str, str]],
    branch: dict[str, tuple[str, str, str]],
) -> bool:
    """Proof of *no unique file changes*, even with different squash ancestry.

    The compare endpoint lists differences between the merge base and the
    candidate head. Every one must already match main byte-for-byte and mode-
    for-mode. Never accept incomplete pagination or an absent file listing.
    """
    files = comparison.get("files")
    ahead = comparison.get("ahead_by")
    if (
        comparison.get("status") not in ("ahead", "diverged")
        or not isinstance(ahead, int) or not 0 < ahead < 250
        or not isinstance(files, list) or not files or len(files) >= 250
    ):
        return False
    paths = set()
    for item in files:
        path = item.get("filename")
        if not isinstance(path, str) or not path:
            return False
        paths.add(path)
        if item.get("previous_filename"):
            paths.add(item["previous_filename"])
    return all(main.get(path) == branch.get(path) for path in paths)


def equivalent_branch_candidates(
    repository: str, branches: list[dict], pulls: list[dict],
    token: str, excluded: set[str],
) -> list[tuple[str, str]]:
    """Delete ancestors and exact-content squash duplicates; retain all new work."""
    open_heads = {
        (pr.get("head") or {}).get("ref")
        for pr in pulls if pr.get("state") == "open"
        and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == repository
    }
    main_tree: dict[str, tuple[str, str, str]] | None = None
    candidates = []
    for branch in branches:
        name = branch.get("name")
        sha = (branch.get("commit") or {}).get("sha")
        if (
            not isinstance(name, str) or not isinstance(sha, str)
            or name == "main" or branch.get("protected")
            or name in open_heads or name in excluded
        ):
            continue
        ref = quote(name, safe="")
        comparison = get_json(repository, f"compare/main...{ref}", token)
        # A strict ancestor cannot have unmerged changes.
        if comparison.get("status") in ("behind", "identical") and comparison.get("ahead_by") == 0:
            candidates.append((name, sha))
            continue
        if comparison.get("status") not in ("ahead", "diverged"):
            continue
        if main_tree is None:
            main_tree = tree_files(get_json(repository, "git/trees/main?recursive=1", token))
        branch_tree = tree_files(get_json(repository, f"git/trees/{ref}?recursive=1", token))
        if same_changed_files(comparison, main_tree, branch_tree):
            candidates.append((name, sha))
    return sorted(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete eligible refs with a SHA lease")
    parser.add_argument("--prune-equivalent", action="store_true", help="Retire ancestor and identical-file branches, never unique work")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    branches = list_pages(repository, "branches", token)
    pulls = list_pages(repository, "pulls?state=all&sort=updated&direction=desc", token)
    candidates = cleanup_candidates(repository, branches, pulls)
    exact = len(candidates)
    if args.prune_equivalent:
        candidates += equivalent_branch_candidates(
            repository, branches, pulls, token, {name for name, _ in candidates}
        )
    print(f"Reviewed {len(branches)} branches and {len(pulls)} PRs; "
          f"{exact} exact merged heads and {len(candidates) - exact} content-equivalent heads eligible")
    failures = []
    for name, sha in candidates:
        print(f"{'Deleting' if args.apply else 'Dry run'}: {name} at {sha[:12]}")
        if not args.apply:
            continue
        # This compare-and-swap prevents deleting a newer branch head.
        command = [
            "git", "push", f"--force-with-lease=refs/heads/{name}:{sha}",
            "origin", f":refs/heads/{name}",
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            failures.append(name)
            print(f"Lease or deletion failed for {name}; branch left intact")
    if failures:
        raise SystemExit(f"Could not delete {len(failures)} branch(es); inspect workflow logs")


if __name__ == "__main__":
    main()
