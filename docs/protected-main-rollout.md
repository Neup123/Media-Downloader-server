# Protect Core main (issue #191)

The live repository currently has no active protection rules. This file
is a rollout specification, not evidence of enforcement. An administrator
must apply and verify the settings through GitHub.

In Settings > Rules > Rulesets, target only the default branch, main.
Require a pull request before merging, block force pushes and deletion,
require branches to be up to date, and require these GitHub Actions checks:

| Workflow | Check context |
|---|---|
| Test | python-tests |
| Test | container-build |
| Live Nextcloud WebDAV E2E | standalone-compose |
| Live Nextcloud WebDAV E2E | live-webdav |

Select GitHub Actions as the expected source of each check. Do not give
automation a blanket bypass or require unrelated optional/manual workflows.

The pinned-engine candidate workflow retains inline acceptance before PR
creation. Before promotion it now also verifies all four ordinary PR checks
via GitHub CLI and rechecks the immutable candidate head. A bot-created PR
may need approval to start CI; missing or pending results fail closed, leaving
the candidate for review. Manual workflow_dispatch evidence alone is not a
substitute for ordinary required PR checks. The existing release approval
variable and all real-engine acceptance requirements remain unchanged.

Acceptance before closing #191:
- Read back the active ruleset and main branch protection.
- Verify an ordinary candidate with missing or failing checks cannot merge.
- Verify a green, up-to-date PR can merge normally, without a bypass.
- Verify automation leaves an unapproved candidate unmerged.
- Retain evidence of these actual operations, not merely this document.

References:
https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
https://cli.github.com/manual/gh_pr_checks
https://docs.github.com/en/actions/concepts/security/github_token
