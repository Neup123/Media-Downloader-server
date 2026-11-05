# Downloader Server

Downloader Server is a server-side media downloader with one web interface for established CLI engines. It manages jobs, settings, progress, logs, and file delivery. The core product runs as a standalone Docker Compose service. Nextcloud support is an optional adaptation maintained in a separate repository.

## Repositories

- Core: https://github.com/Neup123/Media-Downloader-server
- Nextcloud adaptation: https://github.com/Neup123/downloader-server-nextcloud

Core-owned code lives here: service API, web UI, job manager, engine adapters, generic delivery, tests, and engine update automation. The Nextcloud repository is limited to AppAPI/ExApp packaging and Nextcloud-specific integration.

## Current status

**Phase 0 is recorded. Phase 1 implementation is in review.**

- [BLUEPRINT.md](BLUEPRINT.md) records the clean-room reference inventory, engine settings taxonomy, screen mapping, service architecture, and acceptance gates.
- The `phase-1/core-mvp` branch contains the first API, persistent job queue, yt-dlp runner, aria2 JSON-RPC adapter, UI, global engine settings, per-job overrides, cookie upload, local/WebDAV delivery, and container packaging.
- The supported engine list in this first slice is yt-dlp and aria2c only. gallery-dl, wget, you-get, svtplay-dl, and get-sauce remain planned.
- Unit tests and static Python checks pass locally. The browser bundle, clean Docker build, live media downloads, and a real WebDAV/Nextcloud upload have not been verified in this environment.

A feature is not complete until its behavior is tested. A successful API response or a green syntax check is not proof that a delivered file exists and is readable.

## Quick start

1. Copy `.env.example` to `.env`.
2. Generate `APP_API_TOKEN` with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it in `.env`.
3. For WebDAV delivery, generate `APP_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable across restarts and backups.
4. Start with `docker compose up --build -d`.
5. Open `http://127.0.0.1:8080`, enter the API token, and submit a link.

The port binds to loopback by default. Use an authenticated TLS reverse proxy before exposing the UI/API to other machines. Do not expose the API token or aria2 RPC port publicly.

## API and storage

- REST base path: `/api/v1`
- Authentication: `X-API-Key`
- API reference: `/api-reference` (Scalar)
- Checked-in API contract: [openapi.yaml](openapi.yaml)
- Health check: `/healthz`
- Persistent database, job work directories, cookies, and local output: `/data`
- Delivered local files: `/data/downloads/<job-id>/`

## Implemented engine scope

- **yt-dlp:** structured progress output, output discovery, format selector, subtitle controls, cookie file support, and validated extra options.
- **aria2c:** loopback-only JSON-RPC daemon, generated RPC secret, progress polling, cancellation, and allowlisted per-job options.

The full engine and settings parity target is documented in [BLUEPRINT.md](BLUEPRINT.md). The remaining engine adapters are not implemented yet.

## Security notes

- The API requires a random key. The Compose port binds to localhost unless explicitly overridden.
- aria2 RPC listens on the container loopback interface and uses a generated secret.
- WebDAV app passwords are encrypted at rest using `APP_ENCRYPTION_KEY`; the settings API never returns the password.
- Child processes receive argument arrays, not shell command strings.
- Literal private/local IP destinations and obvious internal hostnames are blocked by default. URL validation is not a network firewall: DNS rebinding and redirects must also be restricted with host/container egress controls before the service is exposed to untrusted users.
- Logs redact common credential forms. Do not enable verbose engine traffic logging in production.
- Configure the service for trusted administrators until the full authentication/authorization model and network isolation have been reviewed.

## Verification status

The local suite currently covers API authentication, job submission/cancellation/retry, settings validation, cookie handling, URL filtering, structured yt-dlp progress, argument construction, aria2 RPC authentication parameters, queue recovery, local delivery, and mocked WebDAV MKCOL/PUT/PROPFIND size verification.

Run:

```bash
python -m compileall -q app tests
python -m pytest -q
```

The current local result is **21 passing tests**. The mocked WebDAV test does not replace the required live upload to a disposable WebDAV server. Docker Compose build/startup, frontend TypeScript/Vite build, real yt-dlp/aria2 smoke tests, and real WebDAV delivery remain required gates.

See [BLUEPRINT.md](BLUEPRINT.md) for the implementation sequence and full acceptance criteria.
