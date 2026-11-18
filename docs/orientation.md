# Current architecture and repository orientation

This document describes the **implemented development baseline**, not a production deployment certificate. The historical [blueprint](../BLUEPRINT.md) describes intended behavior; [acceptance status](../ACCEPTANCE_STATUS.md) distinguishes proven implementation from open release gates.

## Repository ownership

| System | Authoritative repository | Scope |
|---|---|---|
| Core downloader | [Media-Downloader-server](https://github.com/Neup123/Media-Downloader-server) | API, authentication, job lifecycle, queue/worker, engines, file delivery, standalone React UI, database schema, security policy, CI and engine update automation |
| Nextcloud adapter | [downloader-server-nextcloud](https://github.com/Neup123/downloader-server-nextcloud) | Signed AppAPI ExApp gateway, user/administrator entitlement, native Nextcloud PHP bridge and UI, user-scoped WebDAV identity, search/notification integration |

**UI ownership is a hard architectural boundary:**
- **Core repository:** owns the standalone downloader web application and its own task-oriented download UX. This interface runs against Core and is not a Nextcloud plugin screen.
- **Nextcloud repository:** owns *only* the Nextcloud plugin's native UI surfaces (Files actions, header, Dashboard widget, Library integration, personal/admin plugin settings). These surfaces call the signed AppAPI adapter and must not contain, embed, reconstruct, or redirect to Core's standalone downloader UI.
- **Shared contract is not shared UI:** job statuses, preview data, settings capabilities, errors, and permitted operations must agree across Core and the plugin through versioned/generated contracts. A generated browser-side job-state helper inside the Nextcloud plugin is a contract consumer, not a second downloader application.
- A download launched inside Nextcloud still uses Core to execute; only its **plugin-facing controls** are rendered by Nextcloud. Core's standalone UI remains independently maintained in the Core repository.

Do not implement download engines or duplicate routing in the Nextcloud repository. Core source changes are imported by its separately verified core-sync workflow; each repository owns its own tests, PRs and deployment gates.

## Trust-boundary diagram

```text
Standalone operator browser (administrator token)
      |  TLS reverse proxy, API key in X-API-Key
      v
Core FastAPI /api/v1 ------------------------> SQLAlchemy / SQLite (persistent /data)
      |                                                 |
      | queue / state transitions                        | job records and settings
      v                                                 |
JobWorker ----> engine subprocesses ----> temporary job workspace
      |                (network egress policy required)
      v
Local output OR verified generic WebDAV PUT/PROPFIND/readback
      |
      +----> Nextcloud Files (when gateway-configured)

Nextcloud browser (no Core token)
      | same-origin native Files/Library/Settings
      v
Native Nextcloud PHP bridge (separate AGPL app)
      | signed AppAPI owner identity
      v
Nextcloud ExApp gateway (server-only Core token, scoped job owner)
      | validates per-user entitlement and destination
      +----> Core FastAPI ------------------------------> encrypted per-owner delivery snapshot
      |
      +----> native notifications / search indexing
```

**Security boundaries:** A browser interacting with the standalone administrator UI is privileged. Nextcloud regular users must never receive the Core token or be redirected to the Core administrator UI. The ExApp checks signed user identity and entitlement before proxying permitted owner-scoped requests. WebDAV credentials for a per-user job are encrypted at rest and must not appear in API job responses. The API's authentication is not equivalent to network isolation: subprocess egress protection is a separate production gate.

## Major code directories

- `app/main.py` exposes the Core FastAPI API, authentication, engine settings, job operations, delivery and system checks.
- `app/schemas.py` and `openapi.yaml` define and check the external request/response contract.
- `app/security.py` and `app/redaction.py` validate source and outbound destinations and minimize sensitive diagnostics.
- `app/db.py` owns the SQLAlchemy models, SQLite persistence and durable records.
- `app/job_states.py` defines canonical job state transitions. `app/worker.py` dispatches, resumes and finalizes jobs through those transitions.
- `app/adapters/` isolates the individual CLI download engines, their option validation and progress callbacks.
- `app/completion.py` verifies complete downloaded output before recovery or redelivery; `app/delivery.py` transfers and verifies local or WebDAV destinations.
- `web/src/` is the standalone React/TypeScript UI. `web/tests/` contains UI contracts; `tests/` contains Python unit, contract and acceptance tests.
- `scripts/` holds release/acceptance checks and candidate engine update automation. `.github/workflows/` gates builds, API drift, browser/WebDAV checks and publishing.

## Request-to-file lifecycle

1. An authenticated request is validated by Pydantic and by Core source, engine and options policy. Automatic engine selection resolves a saved priority/specificity routing rule or a safe default.
2. Core persists the queued job in SQLite. A successful submit means **queued**, never that the file has arrived.
3. The worker selects jobs within global and per-engine concurrency limits, transitions QUEUED → STARTING → DOWNLOADING (or other documented active states), and streams progress; cancellation and failures are terminal states.
4. Completed files are recorded in a verifiable manifest. On restart, unverified leftovers are discarded; a verified completed download can be reused for delivery without redownloading the source.
5. Delivery writes to a local output path or performs WebDAV upload and remote verification. Only verified delivery allows COMPLETED status and a confirmed file record. A delivery failure can retain verified partial output for diagnosis.
6. The Nextcloud adapter can reconcile native user notifications and indexing separately from the Core job lifecycle. Frontend polling and UI wording must not assume immediate delivery.

The durable job state contract and transition rules are authoritative; UI controls must derive what they may do from the API rather than guessing status.

## Installation and deployment paths

**Local/trusted standalone development:** Create `.env` from `.env.example`, provision `APP_API_TOKEN` and (for encrypted delivery) stable `APP_ENCRYPTION_KEY` outside source control, then use `compose.yaml`. Its default HTTP listener binds to 127.0.0.1; use authenticated TLS reverse proxy before remote access. Persistent data lives in the named `downloader-server-data` Docker volume, mapped to `/data` in the container. Never put token values in issues, logs or documentation.

**Guarded Linux Compose:** `compose.egress.yaml`, `Dockerfile.egress` and [egress guide](../EGRESS_GUARD.md) define a separate Linux network isolation setup with dedicated negative tests. Do not confuse passing CI on Linux with acceptance on Docker Desktop or the actual production host.

**Nextcloud:** Install a compatible Core image and separately versioned ExApp plus native PHP bridge following [Nextcloud adaptation README](https://github.com/Neup123/downloader-server-nextcloud) and its per-user installation guide. The core-to-ExApp API token is server-only. Nextcloud AppAPI/Deploy Daemon verification and a real-target WebDAV readback are required before enabling users.

**Release and upgrades:** See [engine update guidance](../ENGINE_UPDATES.md) and the GitHub Actions workflows. Candidate dependency updates are gated by exact-version tests, image build and disposable Nextcloud file readback; a bot PR or green unit suite alone is not permission to publish. Do not merge or deploy open draft engine integrations automatically.

## Verification surfaces and exclusions

- `GET /healthz` is **HTTP process liveness**, not proof of download readiness. CI's real disposable WebDAV/browser gate is stronger but still not a production-site acceptance.
- `GET /readyz` (when included in the deployed version) checks database and scheduler readiness. It still does **not** prove reachability of arbitrary public media sites, engine health, WebDAV production delivery or host egress policy.
- `tests/e2e_live_webdav.py` exercises real local fixture-to-disposable-Nextcloud readback, including size/hash checks. `tests/e2e_p2_site_webdav.py` requires an authorized live site URL and is a separate opt-in gate.
- Production remains unapproved for untrusted source submissions while [egress issue #72](https://github.com/Neup123/Media-Downloader-server/issues/72), [live P2 acceptance #63](https://github.com/Neup123/Media-Downloader-server/issues/63), and the real Nextcloud target verification remain unresolved.
- See the current [Core Issues](https://github.com/Neup123/Media-Downloader-server/issues) and [Nextcloud Issues](https://github.com/Neup123/downloader-server-nextcloud/issues) for open work; do not infer completion from a plan or unmerged PR.

## Maintenance discipline

Update this orientation in the same PR when component responsibilities, authoritative contracts, trust boundaries, deployment routes or verification gates change. Keep historical plans labeled as historical; do not silently rewrite the acceptance record to imply external verification that did not occur.
