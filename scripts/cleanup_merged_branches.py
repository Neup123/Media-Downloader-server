"""Safely retire GitHub branches whose exact heads are in merged pull requests.

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete eligible refs with a SHA lease")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    branches = list_pages(repository, "branches", token)
    pulls = list_pages(repository, "pulls?state=all&sort=updated&direction=desc", token)
    candidates = cleanup_candidates(repository, branches, pulls)
    print(f"Reviewed {len(branches)} branches and {len(pulls)} PRs; "
          f"{len(candidates)} exact merged heads eligible")
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
