# Downloader Server

Downloader Server is a server-side media downloader with one web interface for multiple established CLI engines. It manages jobs, engine settings, progress, logs, and delivery. It is designed to run as a standalone Docker Compose service; Nextcloud integration is an optional adaptation maintained separately.

## Repositories

- Core: https://github.com/Neup123/Media-Downloader-server
- Nextcloud adaptation: https://github.com/Neup123/downloader-server-nextcloud

The core repository is the source of truth for the service, web UI, API, engine adapters, generic WebDAV delivery, tests, and engine update automation. The Nextcloud repository is limited to AppAPI/ExApp packaging and other Nextcloud-specific integration.

## Current status

**Phase 0 — Blueprint extraction: recorded.**

- [BLUEPRINT.md](BLUEPRINT.md) records the clean-room behavior inventory, upstream engine settings, screen inventory, service architecture, documentation references, and the Python service-stack decision.
- No application code or deployment configuration has been added yet.
- The next implementation phase is the core MVP: API, persistent job manager, yt-dlp and aria2 adapters, web UI, and generic WebDAV delivery.

A phase is complete only after the acceptance checks in BLUEPRINT.md pass. Documentation or a successful syntax check is not evidence that an end-to-end download and delivery works.

## Service stack decision

- Python 3.13 and FastAPI.
- SQLAlchemy 2 with SQLite for the initial single-container deployment.
- React and TypeScript for the web UI.
- Server-Sent Events for live job progress.
- A checked-in OpenAPI YAML contract rendered by Scalar.
- Docker Compose with persistent storage.

## Design constraints

- Download with established engines; do not reimplement site extractors.
- Expose global engine defaults and per-download overrides in the UI.
- Execute subprocesses using argument arrays, never shell strings built from user input.
- Keep credentials out of source control and redact them from logs.
- Deliver to local directories or generic WebDAV endpoints. Nextcloud files are delivered through WebDAV, never by writing to the Nextcloud data directory.
- Do not add Nextcloud-specific code to this repository.
- Do not mark a feature complete until its behavior is tested and the output is verified.

## Documentation

Start with [BLUEPRINT.md](BLUEPRINT.md). It lists engine-specific settings, invocation behavior, parser requirements, the initial UI scope, architecture decisions, acceptance gates, and authoritative documentation links.
