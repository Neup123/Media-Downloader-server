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
from pathlib import Path
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


AUDITED_SUPERSEDED_CORE_HEADS = {
    "ops/retire-audited-core-pre-main-branches":
        "2b86dd5709657f910effd1da2481e96690fc4ec9",
    "contract/enforce-engine-cookie-delivery-responses":
        "b6addeb586111b35d21e7f0a43461ee69ebb0e35",
    "feature/svtplay-dl-engine":
        "71bac03d1777e6a0c050f8e78ba3e99ea73705f3",
    "test/openapi-request-parity-svtplay":
        "16bc000d2a96099487a76a559997e8edb5af34a4",
    "test/openapi-request-schema-parity":
        "f8cc1a89ccc586fcf78b7d9179eebdd322d418fc",
    "test/openapi-request-schema-parity-ready":
        "fee7f0427562c93c0f8a4ae0922bcf51e0e7ca8d",
    "test/openapi-request-schema-parity-v2":
        "fee7f0427562c93c0f8a4ae0922bcf51e0e7ca8d",
}


# Historical #102 branch predates restored Hebrew/RTL. Its replacement was
# reviewed and merged in PR #129. This SHA-locked exception must never
# generalize to branches with unmerged work.
AUDITED_SUPERSEDED_ONEOFF_HEADS = {
    "ui/102-oneoff-destination-subfolder":
        "4b6db8fc0ef2efb7ce0be825712183d42d3960ea",
}


def audited_oneoff_port_verified(root: Path | None = None) -> bool:
    """Prove the safe #102 replacement (including RTL) is already on main."""
    root = root or Path(__file__).resolve().parents[1]
    required = {
        "app/db.py": ("delivery_subfolder", "ALTER TABLE jobs ADD COLUMN delivery_subfolder"),
        "app/schemas.py": ("validate_oneoff_subfolder", "delivery_subfolder"),
        "app/main.py": ("delivery_subfolder=payload.delivery_subfolder",
                        "Nextcloud user destinations are selected by the trusted gateway"),
        "app/delivery.py": ("subfolder = job.delivery_subfolder if job and not per_user else None",
                             "target_dir = (root / subfolder / job_id).resolve()"),
        "web/src/App.tsx": ("Folder for this download (optional)", "delivery_subfolder: deliverySubfolder.trim()"),
        "web/src/i18n.ts": ("he", "translate"),
        "tests/test_oneoff_delivery_subfolder.py": (
            "test_local_oneoff_folder_verified_and_regular_destination_unchanged",
            "test_webdav_oneoff_mkcol_put_and_profind_verified_under_saved_root",
            "test_api_persists_folder_but_rejects_per_user_gateway_override",
        ),
        "tests/e2e_browser_engine_settings.py": (
            "one-off destination subfolder + verified Nextcloud WebDAV bytes PASS",
            'select_option("he")',
        ),
    }
    for relative, markers in required.items():
        path = root / relative
        if not path.is_file():
            return False
        content = path.read_text(encoding="utf-8")
        if not all(marker in content for marker in markers):
            return False
    return True


def audited_oneoff_candidates(
    branches: list[dict], pulls: list[dict], *, replacements_verified: bool,
) -> list[tuple[str, str]]:
    """Only the historical exact SHA, never a changed or currently reviewed head."""
    if not replacements_verified:
        return []
    open_heads = {
        (pr.get("head") or {}).get("ref")
        for pr in pulls if pr.get("state") == "open"
    }
    return sorted(
        (name, AUDITED_SUPERSEDED_ONEOFF_HEADS[name])
        for branch in branches
        if (name := branch.get("name")) in AUDITED_SUPERSEDED_ONEOFF_HEADS
        and name not in open_heads and not branch.get("protected")
        and (branch.get("commit") or {}).get("sha") == AUDITED_SUPERSEDED_ONEOFF_HEADS[name]
    )


def audited_core_port_verified() -> bool:
    """Require replacement source, more complete tests and response models."""
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "app/main.py", root / "app/schemas.py",
        root / "app/adapters/svtplay.py",
        root / "tests/test_openapi_request_shapes.py",
        root / "tests/test_typed_engine_cookie_delivery_responses.py",
        root / "tests/test_audited_core_legacy_cleanup.py",
    ]
    if not all(path.is_file() for path in files):
        return False
    main = files[0].read_text()
    schemas = files[1].read_text()
    svtplay = files[2].read_text()
    api_tests = files[3].read_text()
    response_tests = files[4].read_text()
    return all([
        "response_model=list[EngineStatusOut]" in main,
        "response_model=CookieUploadedOut" in main,
        "response_model=DeliverySettingsOut" in main,
        "class EngineOptionsOut(BaseModel)" in schemas,
        "class CookieDeletedOut(BaseModel)" in schemas,
        "class DeliverySettingsOut(BaseModel)" in schemas,
        '"--config", "/dev/null"' in svtplay,
        "not isinstance(result[\"output_format\"], str)" in svtplay,
        "test_checked_in_request_body_constraints_match_runtime_validation" in api_tests,
        "test_job_response_contracts_match_fastapi_output_models" in api_tests,
        "test_all_documented_response_models_match_runtime_openapi" in response_tests,
    ])


def audited_core_candidates(
    branches: list[dict], pulls: list[dict], *, replacements_verified: bool,
) -> list[tuple[str, str]]:
    """One-off SHA-locked retirement; no guessing about divergent work."""
    if not replacements_verified:
        return []
    open_heads = {
        (pr.get("head") or {}).get("ref")
        for pr in pulls if pr.get("state") == "open"
    }
    return sorted(
        (name, AUDITED_SUPERSEDED_CORE_HEADS[name])
        for branch in branches
        if (name := branch.get("name")) in AUDITED_SUPERSEDED_CORE_HEADS
        and name not in open_heads and not branch.get("protected")
        and (branch.get("commit") or {}).get("sha") == AUDITED_SUPERSEDED_CORE_HEADS[name]
    )



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete eligible refs with a SHA lease")
    parser.add_argument("--prune-equivalent", action="store_true", help="Retire ancestor and identical-file branches, never unique work")
    parser.add_argument("--retire-audited-core", action="store_true", help="Retire exact, separately reviewed, SHA-locked superseded Core branches")
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
    equivalent = len(candidates) - exact
    if args.retire_audited_core:
        excluded = {name for name, _ in candidates}
        candidates += [
            pair for pair in audited_core_candidates(
                branches, pulls, replacements_verified=audited_core_port_verified(),
            )
            if pair[0] not in excluded
        ]
    if args.retire_audited_core:
        excluded = {name for name, _ in candidates}
        candidates += [
            pair for pair in audited_oneoff_candidates(
                branches, pulls, replacements_verified=audited_oneoff_port_verified(),
            )
            if pair[0] not in excluded
        ]
    print(f"Reviewed {len(branches)} branches and {len(pulls)} PRs; "
          f"{exact} exact merged heads, {equivalent} equivalent heads "
          f"and {len(candidates) - exact - equivalent} audited legacy heads eligible")
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
