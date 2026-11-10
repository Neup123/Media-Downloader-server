"""Interpret red engine-update runs without guessing unclassified failures."""
from __future__ import annotations

PR_CREATION_PERMISSION_ERROR = (
    "GitHub Actions is not permitted to create or approve pull requests (createPullRequest)"
)
GENERIC_INTERPRETATION = (
    "Inspect the first failed gate and compare the relevant upstream release notes; "
    "the automated gate has not assumed a root cause."
)


def first_pass_interpretation(diagnostics: str) -> str:
    """Identify a known infrastructure blocker, not a bad engine candidate."""
    if PR_CREATION_PERMISSION_ERROR in diagnostics:
        return (
            "The first failed gate was candidate PR creation (gh pr create): "
            "GitHub rejected the createPullRequest operation. "
            'The repository Actions setting "Allow GitHub Actions to create and approve pull requests" '
            "is OFF (can_approve_pull_request_reviews=false). "
            "A repository owner must enable it under Settings > Actions > General > "
            "Workflow permissions. The workflow already declares pull-requests: write; "
            "no workflow-file permission change can override this repository setting. "
            "The candidate was never acceptance-tested."
        )
    return GENERIC_INTERPRETATION


def diagnostic_excerpt(diagnostics: str, *, limit: int = 12000) -> str:
    """Retain the permission error even when later diagnostic output is long."""
    if not diagnostics:
        return "Diagnostics not available"
    if len(diagnostics) <= limit:
        return diagnostics
    tail = diagnostics[-limit:]
    if PR_CREATION_PERMISSION_ERROR not in diagnostics or PR_CREATION_PERMISSION_ERROR in tail:
        return tail
    position = diagnostics.index(PR_CREATION_PERMISSION_ERROR)
    priority = diagnostics[max(0, position - 200):position + len(PR_CREATION_PERMISSION_ERROR) + 100]
    separator = "\n... [later diagnostic tail] ...\n"
    remaining = max(0, limit - len(priority) - len(separator))
    return priority + separator + (diagnostics[-remaining:] if remaining else "")


def blocked_issue_body(summary: str, run_url: str, diagnostics: str) -> str:
    excerpt = diagnostic_excerpt(diagnostics)
    interpretation = first_pass_interpretation(diagnostics)
    return (
        f"## Candidate\n{summary}\n\n## Failed acceptance run\n{run_url}\n\n"
        "Previous pinned versions remain deployed. The candidate PR is left unmerged.\n\n"
        "## Diagnostic excerpt (full artifact attached to workflow)\n"
        f"{chr(96)*3}text\n{excerpt}\n{chr(96)*3}\n"
        f"\n## First-pass interpretation\n{interpretation}\n"
    )
