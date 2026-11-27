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

The queued-engine explanation from #177 is derived in `job_to_dict` from
`disabled_engines` and the existing QUEUED state; `queue_block_reason` is
`engine_disabled` only for blocked queued work, otherwise null. It updates
on list/detail/action/SSE reads, without persisted progress mutations or
scheduler polling writes. Active jobs remain unaffected and queued jobs
remain cancellable. Standalone job cards translate the fixed reason code;
Nextcloud presentation and additional requirement-gated reasons remain
separate integration work.

Do not claim the original stack or any umbrella issue complete until
all linked acceptance criteria and exact-head checks pass. Existing
#227 watch and #230 Streamlink adapter are already on main.

## #155 remaining provider and preview boundary

Administrative disable also blocks explicitly selected yt-dlp/gallery-dl
preview and aria2 manifest inspection before starting a subprocess or RPC
request (HTTP 409, fixed detail, no missing-user-requirement hint). Uses
`engine_permitted`; no new stored state or policy writer. The existing
bounded direct-file HEAD is a separate metadata operation, not an engine
activation. Tests exercise actual authenticated API routes with mocked
extractor/DNS/RPC boundaries and policy disable/re-enable; they do not prove
provider recognition or live credential acceptance.

Full #155 is still blocked: current Core registry has eight engines and no
Telegram adapter or owner-scoped connection routes. Legacy #173 explicitly
requires a real authorized account before activation; its instance-only
secret storage is insufficient for #157. Current vault stores encrypted
files but lacks provider-authenticated per-site Test/status/invalidation
(#156). Nextcloud #155 UI requires a typed Core recognition result with
active-engine precedence and exact settings targets; current signed preview
only accepts yt-dlp/gallery-dl, and cookie list/upload/delete do not implement
that provider contract. Do not equate metadata failures, generic URL matching,
file presence, or synthetic credentials with successful authorization.


## Narrow you-get fallback candidate (#177)

The candidate only retries an explicit unsupported-extractor failure for a
single exact-host YouTube video; unknown errors (including an unclassified
dethrottle traceback), auth, access, network, rate limits, playlists,
custom primary format/proxy, unavailable/disabled/saturated yt-dlp and
nondefault target-engine settings never trigger automatic fallback. The
worker changes the existing dispatcher reservation synchronously from
you-get to yt-dlp, within the original timeout and cancellation scope.
The primary workspace is discarded before the secondary attempt. The
same selected cookie file, if any, is passed through the existing
materializer; there is no new credential store or credential validation.
Fixed public error mapping identifies both engines and the secondary
category without reflecting diagnostics.

No new job state: STARTING/EXTRACTING/DOWNLOADING use the existing
app/job_states.py transition table and worker writer. The selected engine
remains immutable. The additive nullable output_engine column records the
alternate producer before manifest publication; no verified manifest
means the producer is cleared on failure or recovery. Verified re-delivery
retains it. Core JobOut and provenance expose it separately.

The legacy real YouTube acceptance and separate reviewed-patch requirement
remain open; deterministic subprocess and worker tests do not certify the
original upstream dethrottle incident, a real YouTube download, provider
authentication, or production exposure. Do not treat this candidate as
permission to enable gated engines or deploy through the target-host gate.


Pinned upstream source check: you-get v0.4.1743
[`youtube.py` lines 246–272](https://github.com/soimort/you-get/blob/v0.4.1743/src/you_get/extractors/youtube.py#L246-L272)
and lines 353–364 emit the exact fatal missing-stream-URL diagnostic
after the playability check. Recognize only that native explicit
extractor message (including its optional SGR coloring), not generic
exceptions or the missing-base.js diagnostic. Subprocess fixtures call
the installed pinned package's own logger. The normal CLI
[`common.py` lines 1800–1817](https://github.com/soimort/you-get/blob/v0.4.1743/src/you_get/common.py#L1800-L1817)
suppresses unexpected tracebacks, and debug mode logs the parsed
arguments. Do not enable debug to infer the historical dethrottle
incident or risk exposing source or cookie arguments.
