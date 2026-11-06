# Downloader Server — acceptance ledger

The source of truth for requested behavior is [BLUEPRINT.md](BLUEPRINT.md), the
open GitHub issues, and the CI evidence linked below. This is a **development
status checklist**, not a production release certificate. A merged PR alone is
not proof that its external deployment acceptance has passed.

Status key: **Verified** = exercised in a relevant CI integration test;
**Implemented / partial** = code or unit tests exist, but a required live or
operator demonstration remains; **Blocked** = prerequisite still unsatisfied.

## Core blueprint implementation and release acceptance

| Blueprint / issue | Evidence and implementation | Status / remaining acceptance |
|---|---|---|
| REST API, OpenAPI and Scalar (§8, [#1](https://github.com/Neup123/Media-Downloader-server/issues/1)) | Versioned FastAPI routes, `openapi.yaml`, contract tests, Scalar page | **Implemented / partial** — automated contract tests exist; full browser workflow acceptance is not claimed |
| P0 yt-dlp, aria2c, gallery-dl (§3, #1) | `tests/e2e_live_webdav.py` runs all three in production Compose and compares output to disposable Nextcloud by size and SHA-256 | **Verified** against local legal fixtures; this is not proof of arbitrary public media-site compatibility |
| P1 curl/generic and you-get (§3) | Same real container/WebDAV acceptance using deterministic fixture | **Verified** against the controlled fixture |
| P2 svtplay-dl ([#18](https://github.com/Neup123/Media-Downloader-server/issues/18)), get-sauce ([#30](https://github.com/Neup123/Media-Downloader-server/issues/30)) | Both pinned adapters, settings/UI mappings, unit/CLI tests and production Docker build implemented | **Implemented / partial** — no confirmed legal, real-site green acceptance; retain the issues |
| Settings/presets/routing (§4–§6) | Engine settings, saved presets, URL rules and per-job override forms; [Chromium UI + WebDAV acceptance #78](https://github.com/Neup123/Media-Downloader-server/pull/78) verifies the original P0 yt-dlp/aria2c default and override flows | **Verified for the original Phase 1 MVP**; other engines' comprehensive browser interaction remains later-scope acceptance |
| Persistent queue, retry, cancellation, recovery (§8, #1) | Worker recovery/cancellation/retry tests and durable completion manifests; [PR #77](https://github.com/Neup123/Media-Downloader-server/pull/77) passed real Compose restart, five-engine persistence and verified WebDAV re-delivery | **Verified for the original MVP criteria**; exhaustive every-engine in-flight interruption remains later-scope hardening |
| Standalone Compose (§8, #1) | `compose.yaml` builds the non-root, persistent, health-checked service; standalone-only Docker CI starts it without Nextcloud and verifies health, authentication and empty queue | **Verified** by the [PR #77 standalone Compose gate](https://github.com/Neup123/Media-Downloader-server/pull/77) |
| Local and WebDAV delivery (§8, #1) | Local copy tests, WebDAV remote metadata and SHA-256 readback; disposable Nextcloud real integration | **Verified** for local fixtures and tested paths; actual target Nextcloud remains unverified |
| Playlist/batch, library and detailed engine capabilities (§6) | Batch jobs, yt-dlp playlist discovery/selection, history, logs, redelivery and per-engine controls | **Implemented / partial** — desktop reference parity is not fully proven; subscription polling is expressly later scope |
| Engine update automation (§10, [#20](https://github.com/Neup123/Media-Downloader-server/issues/20), [#59](https://github.com/Neup123/Media-Downloader-server/issues/59), [#61](https://github.com/Neup123/Media-Downloader-server/issues/61)) | PyPI, Debian and get-sauce candidate detection/workflows; tested candidate generation and blocked issues | **Implemented / partial** — combined PyPI/Debian validation, exact-commit release targeting and a GitHub Actions current-pin red/green policy rehearsal with real disposable WebDAV acceptance are included. The rehearsal is not an actual version upgrade: real update promotion/release, live P2 sites and unattended deployment approval remain open |
| P2 real-site acceptance ([#63](https://github.com/Neup123/Media-Downloader-server/issues/63)) | Opt-in manual P2 workflow with SHA-256 WebDAV verification | **Blocked** pending an authorized, stable public test URL and a green real-site run |
| Network-layer egress restriction ([#9](https://github.com/Neup123/Media-Downloader-server/issues/9), [#72](https://github.com/Neup123/Media-Downloader-server/issues/72)) | URL/DNS checks, curl connection pinning and proxy-variable stripping are defense in depth | **Implemented / partial** — alternative [guarded Linux Compose](EGRESS_GUARD.md) now has kernel-level IPv4/IPv6 policy and live negative CI tests from the downloader namespace. Docker Desktop/target-platform acceptance, guarded real-site downloads and full deployment review remain blocked |
| Redundant legacy branches ([#73](https://github.com/Neup123/Media-Downloader-server/issues/73)) | SHA-leased cleanup, audited equivalent tips and [PR #79](https://github.com/Neup123/Media-Downloader-server/pull/79) for seven fixed-SHA legacy heads | **Pending post-merge cleanup verification** — mark complete only when GitHub branch inventory confirms the deletions |

## Nextcloud adaptation

| Nextcloud issue / requirement | Verified vs still required |
|---|---|
| [Target readiness #1](https://github.com/Neup123/downloader-server-nextcloud/issues/1), [deployment #19](https://github.com/Neup123/downloader-server-nextcloud/issues/19) | Disposable Nextcloud 35.0.2 WebDAV and AppAPI real ExApp install/enable/disable/unregister have passed CI. **Actual Docker Desktop target is not verified**: record server/AppAPI version, Deploy Daemon/HaRP, administrator Test Deploy, authenticated WebDAV upload/readback, reverse proxy, HTTPS and credential rotation |
| [Immutable ExApp artifacts #24](https://github.com/Neup123/downloader-server-nextcloud/issues/24) | Source-SHA image tags and separately validated OCI digest provenance are implemented. **Native digest-pinned AppAPI XML is not supported by the observed docker-install format**, and fully pinned base images/transitive dependencies and target digest verification remain open |
| Sync and branch lifecycle | Verified merge of core `main` into Nextcloud `main` via the [sync workflow](https://github.com/Neup123/downloader-server-nextcloud/actions/workflows/bootstrap-upstream.yml), with isolated core/downstream test suites and conservative branch cleanup. Core-only workflows are never imported into Nextcloud |

## Release decision and next sizable PR groups

**Not production-approved.** Until #9/#72 has a demonstrated egress boundary,
keep the API bound to a trusted administrator interface. Until live P2 acceptance,
do not put svtplay-dl/get-sauce on auto-upgrade release allowlists.

Further work should be grouped into complete, testable PRs, not individual
fixes without broader acceptance:

1. **MVP release acceptance** ([#1](https://github.com/Neup123/Media-Downloader-server/issues/1)): **completed** and issue closed after passing standalone Compose, real restart/re-delivery and Chromium UI/engine-override acceptance (#77 and #78). This is not production-security approval.
2. **Engine acceptance and updates** (#18, #20, #30, #59, #61, #63): finish real P2 fixture authorization, acceptance and release red/green scenarios.
3. **Security and deployment boundary** (#9, #72): enforce container/host egress policy with negative DNS/redirect/IPv6 tests before external exposure.
4. **Nextcloud production adoption** (Nextcloud #1, #19, #24): target verification, HaRP/Deploy Daemon, UI/user context, release image provenance and installation evidence.

### Evidence

- [Python/Docker workflow](https://github.com/Neup123/Media-Downloader-server/actions/workflows/test.yml)
- [Disposable Nextcloud WebDAV workflow](https://github.com/Neup123/Media-Downloader-server/actions/workflows/live-nextcloud-e2e.yml)
- [Manual P2 workflow](https://github.com/Neup123/Media-Downloader-server/actions/workflows/manual-p2-site-webdav.yml)
- [Nextcloud real AppAPI lifecycle](https://github.com/Neup123/downloader-server-nextcloud/actions/workflows/appapi-docker-deploy-e2e.yml)
- [Nextcloud source-SHA image provenance](https://github.com/Neup123/downloader-server-nextcloud/actions/workflows/publish-exapp-commit-image.yml)

Update statuses only when supported by the corresponding acceptance evidence.
