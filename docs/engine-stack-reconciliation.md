# Reconcile original new-engine stack (#173, #175, #177)

Original #173 head 7b24b5dc, common ancestor eb6a28b8, main b42d0a53
(55 commits ahead of the common ancestor). Fourteen files changed on both
sides. Do not merge the obsolete registry, worker or request contracts over
the current main. Keep the original branches as source references until
all their useful changes have replacement PRs.

| Slice | Integration decision |
| --- | --- |
| Shared engine admission | This PR extracts the persistent switch from #173 and uses current canonical registry; current job transitions untouched. |
| aria2 metadata | Current main already follows payload GIDs and distinguishes metadata. Compare existing tests before porting; don't reintroduce the old unbounded retry loop. |
| you-get fallback | Separate reviewed patch, only if explicit YouTube extractor failure, isolated fresh output and supported settings; preserve engine provenance and don't conflate source/auth failure with transport failure. |
| Telegram | Separate owner-scoped credential and revocation contract; original instance-only secret table is insufficient for #157 user-first precedence. Real authorized test account required before activation. |
| pyLoad and cobalt | Port #175 lifecycle into one explicit state machine, off by default. Verify real pinned service API, cancellation ownership and isolated output before enabling; do not blindly copy legacy polling/captcha flow. |
| musicdl | Hold runtime installation pending upstream PolyForm Noncommercial eligibility or separate optional package decision. |
| ddddocr and paid solver | Local component, not a persistent background service. Separate consent, credential and manual challenge ownership requirements; no automatic purchase. |
| #177 failure messages | Reconcile with current app/user_failures.py, preserve current structured reasons; no second error mapper. |
| #182 Nextcloud UI | Rebase after accepted Core contracts; preserve localization and owner-reviewed design. |

The admission switch is not a new job status. Its transition table:
missing/enabled -> disabled (administrator PUT); disabled -> enabled
(administrator PUT); repeat writes are idempotent. Registry and new job
admission read the same policy; scheduler excludes disabled engines before
LIMIT and rechecks before STARTING; already active jobs finish with the
existing transition_job path. Queued jobs remain cancellable. This is a
start-admission switch, not a promise to revoke active credentials or
stop a daemon. Future new-engine requirement policy must extend this
single admission path, not duplicate it.

Do not claim the original stack or any umbrella issue complete until
all linked acceptance criteria and exact-head checks pass. Existing
#227 watch and #230 Streamlink adapter are already on main.
