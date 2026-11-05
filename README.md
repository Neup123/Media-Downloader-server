# Downloader Server

Downloader Server is a server-side media downloader with one web interface for established CLI engines. It manages jobs, engine settings, progress, logs, and file delivery. The core product runs as a standalone Docker Compose service. Nextcloud support is an optional integration maintained in a separate repository.

## Repositories

- Core: https://github.com/Neup123/Media-Downloader-server
- Nextcloud adaptation: https://github.com/Neup123/downloader-server-nextcloud

The core repository owns the API, web UI, queue/worker, engine adapters, generic local/WebDAV delivery, tests, and engine update automation. The Nextcloud repository is limited to AppAPI/ExApp packaging and other Nextcloud-specific integration.

## Current status

**Phase 0 blueprint is recorded. Phase 1 core MVP is in draft review.**

- [BLUEPRINT.md](BLUEPRINT.md) records the clean-room reference inventory, engine settings taxonomy, screen mapping, service architecture, and acceptance gates.
- The current Phase 1 branch includes the API, persistent queue, restart recovery, yt-dlp, aria2c, and gallery-dl adapters, the web UI, global engine settings, per-job overrides, cookie upload, saved presets, URL-to-engine rules, batch submission (up to 50 URLs), local/WebDAV delivery, and standalone Docker packaging.
- Automatic engine selection uses saved host-suffix/path-prefix rules with priority and specificity ordering. FTP/magnet URLs default to aria2c; unmatched URLs default to yt-dlp unless a selected preset supplies the engine.
- Saved presets store validated engine-specific settings. Job-level options override preset settings.
- Batch submission is atomic: one invalid URL rejects the entire batch, so no partial set of jobs is queued.
- All three currently implemented engines are classified P0 in the blueprint: yt-dlp, aria2c, and gallery-dl.
- wget/generic and you-get remain P1. svtplay-dl and get-sauce remain P2.
- The draft PR is intentionally not merged until its live and operational acceptance checks are complete.

A feature is not complete merely because an API request succeeds or a container builds. The output file must be readable and, where configured, verified at its delivery destination.

## Quick start

1. Copy `.env.example` to `.env`.
2. Generate `APP_API_TOKEN` with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it in `.env`.
3. For WebDAV delivery, generate `APP_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable across restarts and backups.
4. Start with `docker compose up --build -d`.
5. Open `http://127.0.0.1:8080`, enter the API token, and submit a link.

The port binds to loopback by default. Use an authenticated TLS reverse proxy before exposing the UI/API to other machines. Do not expose the API token or aria2 RPC port publicly.

Concurrency:
- `MAX_CONCURRENT_DOWNLOADS` sets the total worker limit.
- `MAX_CONCURRENT_YTDLP`, `MAX_CONCURRENT_ARIA2`, and `MAX_CONCURRENT_GALLERY_DL` set independent per-engine caps.

The scheduler excludes engines that are already at their concurrency cap before limiting which queued jobs to inspect. This allows an eligible job for another engine to start even when many earlier jobs belong to a busy engine.

## API and storage

- REST base path: `/api/v1`
- Authentication: `X-API-Key`
- API reference: `/api-reference` (Scalar)
- Presets: `/api/v1/presets`
- Automatic URL routing rules: `/api/v1/engine-rules`
- Batch queue: `POST /api/v1/jobs/batch` (up to 50 items)
- Checked-in API contract: [openapi.yaml](openapi.yaml)
- API contract CI compares path/method coverage, documented success statuses, and API-key requirements against the implemented FastAPI routes. Detailed schema parity remains a separate acceptance gate.
- Health check: `/healthz`
- Persistent database, job work directories, cookies, and local output: `/data`
- Delivered local files: `/data/downloads/<job-id>/`

## Implemented P0 engines

- **yt-dlp:** structured progress output, output discovery, format selector, subtitle controls, cookie file support, and validated additional arguments.
- **aria2c:** loopback-only JSON-RPC daemon, generated RPC secret, progress polling, cancellation, and an allowlisted per-job option set.
- **gallery-dl:** gallery/image extraction, terminal progress parsing, output discovery, extractor options, cookies, and optional proxy. Output directory and progress settings remain service-managed.

The gallery-dl options UI accepts a JSON object to expose its large and site-specific configuration surface without hard-coding every extractor option. Credential-like option names and proxies with embedded credentials are rejected; use the private cookie-file upload for session cookies.

The full engine and settings parity target is documented in [BLUEPRINT.md](BLUEPRINT.md). Remaining engine adapters are not implemented yet.

## Restart and retry safety

The worker requeues interrupted downloads on restart. Files in a job directory are **not** considered completed merely because they have a normal extension. After an engine reports success, the service atomically writes a completion manifest containing each output path, size, and modification time. Reusing previously downloaded files (for example, after a WebDAV delivery failure) requires a valid manifest and unchanged output files. Otherwise, stale partial output is discarded before downloading again. Manual cancellation is preserved; stale queued cancellation flags from an unclean service shutdown are cleared during startup.

## Delivery

Local delivery copies files into the job's output folder and checks the resulting file. Relative paths beneath the download workspace are preserved: `album-a/image.jpg` and `album-b/image.jpg` remain separate files. Outputs outside the workspace and symlinked output paths are rejected. Generic WebDAV delivery:

1. Creates missing destination folders using MKCOL.
2. Uploads using PUT.
3. Reads remote metadata using PROPFIND.
4. Verifies the destination file size.

Cancellation during delivery is cooperative: the service checks cancellation between local-copy and WebDAV upload chunks, and between remote operations. Local files are copied to temporary paths and atomically published after size verification. Completed uploads remain listed in the job record if a subsequent upload is cancelled; cancellation **cannot guarantee deletion** of files already written to a remote server, and an interrupted WebDAV PUT may leave a partial remote file. Retrying a cancelled job attempts delivery again.

The live CI acceptance workflow uses the production downloader Compose service plus an ephemeral Nextcloud container. It runs yt-dlp, aria2c, and gallery-dl against a test fixture, retrieves each delivered file, and compares its size and SHA-256 hash. It does not connect to or modify the eventual production Nextcloud instance.

## Security notes

- The API requires a random key. The default Compose port binds to localhost.
- aria2 RPC listens on the container loopback interface and uses a generated secret.
- WebDAV app passwords are encrypted at rest using `APP_ENCRYPTION_KEY`; the settings API never returns the password.
- Cookie exports are stored in the private data volume and are not returned by the API.
- Child processes receive argument arrays, not shell command strings.
- Private/local IP destinations and obvious internal hostnames are blocked by default. URL validation is not a network firewall: DNS rebinding and redirects must also be restricted with host/container egress controls before exposing the service to untrusted users.
- Logs redact common credential forms. Do not enable verbose engine traffic logging in production.
- Configure the service for trusted administrators until authentication/authorization scope and network isolation have been fully reviewed.

## Verification

The latest standard GitHub Actions workflow passed on the current PR head: Python compilation, **31 tests**, OpenAPI YAML validation (15 documented paths), and the full Docker image build including the TypeScript/Vite production bundle.

The live Nextcloud E2E workflow has passed for all three P0 engines (yt-dlp, aria2c, and gallery-dl) using the production Compose downloader service. Each file was fetched back from disposable Nextcloud and matched against the original fixture by size and SHA-256. The temporary containers and volumes were removed after the run.

Run the unit suite locally:

```bash
python -m compileall -q app tests
python -m pytest -q
```

See [BLUEPRINT.md](BLUEPRINT.md) for the complete implementation sequence and acceptance criteria.
