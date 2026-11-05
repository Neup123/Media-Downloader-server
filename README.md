# Downloader Server

Downloader Server is a server-side media downloader with one web interface for established CLI engines. It manages jobs, engine settings, progress, logs, and file delivery. The core product runs as a standalone Docker Compose service. Nextcloud support is an optional integration maintained in a separate repository.

## Repositories

- Core: https://github.com/Neup123/Media-Downloader-server
- Nextcloud adaptation: https://github.com/Neup123/downloader-server-nextcloud

The core repository owns the API, web UI, queue/worker, engine adapters, generic local/WebDAV delivery, tests, and engine update automation. The Nextcloud repository is limited to AppAPI/ExApp packaging and other Nextcloud-specific integration.

## Current status

**Phase 0 blueprint is recorded. Phase 1 core MVP is in draft review.**

- [BLUEPRINT.md](BLUEPRINT.md) records the clean-room reference inventory, engine settings taxonomy, screen mapping, service architecture, and acceptance gates.
- The current Phase 1 branch includes the API, persistent queue, restart recovery, yt-dlp, aria2c, and gallery-dl adapters, the web UI, global engine settings, per-job overrides, cookie upload, local/WebDAV delivery, and standalone Docker packaging.
- All three currently implemented engines are classified P0 in the blueprint: yt-dlp, aria2c, and gallery-dl.
- wget/generic and you-get remain P1. svtplay-dl and get-sauce remain P2.
- The draft PR is intentionally not merged until its live and operational acceptance checks are complete.

A feature is not complete merely because an API request succeeds or a container builds. The output file must be readable and, where configured, verified at its delivery destination.

## Quick start

1. Copy \`.env.example\` to \`.env\`.
2. Generate \`APP_API_TOKEN\` with \`python -c "import secrets; print(secrets.token_urlsafe(48))"\` and set it in \`.env\`.
3. For WebDAV delivery, generate \`APP_ENCRYPTION_KEY\` with \`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\`. Keep it stable across restarts and backups.
4. Start with \`docker compose up --build -d\`.
5. Open \`http://127.0.0.1:8080\`, enter the API token, and submit a link.

The port binds to loopback by default. Use an authenticated TLS reverse proxy before exposing the UI/API to other machines. Do not expose the API token or aria2 RPC port publicly.

Concurrency:
- \`MAX_CONCURRENT_DOWNLOADS\` sets the total worker limit.
- \`MAX_CONCURRENT_YTDLP\`, \`MAX_CONCURRENT_ARIA2\`, and \`MAX_CONCURRENT_GALLERY_DL\` set independent per-engine caps.

## API and storage

- REST base path: \`/api/v1\`
- Authentication: \`X-API-Key\`
- API reference: \`/api-reference\` (Scalar)
- Checked-in API contract: [openapi.yaml](openapi.yaml)
- Health check: \`/healthz\`
- Persistent database, job work directories, cookies, and local output: \`/data\`
- Delivered local files: \`/data/downloads/<job-id>/\`

## Implemented P0 engines

- **yt-dlp:** structured progress output, output discovery, format selector, subtitle controls, cookie file support, and validated additional arguments.
- **aria2c:** loopback-only JSON-RPC daemon, generated RPC secret, progress polling, cancellation, and an allowlisted per-job option set.
- **gallery-dl:** gallery/image extraction, terminal progress parsing, output discovery, extractor options, cookies, and optional proxy. Output directory and progress settings remain service-managed.

The gallery-dl options UI accepts a JSON object to expose its large and site-specific configuration surface without hard-coding every extractor option. Credential-like option names and proxies with embedded credentials are rejected; use the private cookie-file upload for session cookies.

The full engine and settings parity target is documented in [BLUEPRINT.md](BLUEPRINT.md). Remaining engine adapters are not implemented yet.

## Delivery

Local delivery copies files into the job's output folder and checks the resulting file. Generic WebDAV delivery:

1. Creates missing destination folders using MKCOL.
2. Uploads using PUT.
3. Reads remote metadata using PROPFIND.
4. Verifies the destination file size.

The live CI acceptance workflow uses an ephemeral Nextcloud container and retrieves delivered files to compare their content hashes. It does not connect to or modify the eventual production Nextcloud instance.

## Security notes

- The API requires a random key. The default Compose port binds to localhost.
- aria2 RPC listens on the container loopback interface and uses a generated secret.
- WebDAV app passwords are encrypted at rest using \`APP_ENCRYPTION_KEY\`; the settings API never returns the password.
- Cookie exports are stored in the private data volume and are not returned by the API.
- Child processes receive argument arrays, not shell command strings.
- Private/local IP destinations and obvious internal hostnames are blocked by default. URL validation is not a network firewall: DNS rebinding and redirects must also be restricted with host/container egress controls before exposing the service to untrusted users.
- Logs redact common credential forms. Do not enable verbose engine traffic logging in production.
- Configure the service for trusted administrators until authentication/authorization scope and network isolation have been fully reviewed.

## Verification

The standard GitHub Actions workflow checks Python compilation, API/adapter/unit tests, OpenAPI YAML validation, and a Docker image build that includes the TypeScript/Vite production bundle.

The live Nextcloud E2E workflow starts a disposable Nextcloud server, submits real download jobs, and verifies delivered files by reading them back and comparing size and SHA-256. The previously completed live run verified yt-dlp and aria2c. The current workflow is being extended to include gallery-dl and to use the production Compose service with the Nextcloud-only overlay.

Run the unit suite locally:

\`\`\`bash
python -m compileall -q app tests
python -m pytest -q
\`\`\`

See [BLUEPRINT.md](BLUEPRINT.md) for the complete implementation sequence and acceptance criteria.
