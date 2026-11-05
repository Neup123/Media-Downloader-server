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
- The **Presets & routing** dashboard tab lets administrators create, edit and delete saved presets and URL rules. Editing preserves the record ID, and routing rules can be temporarily disabled without deleting them. Rule changes affect future automatic engine selection; already queued jobs keep their resolved engine and options.
- The per-engine graphical settings catalog is exposed at `/api/v1/engines/{engine}/options`. The dashboard groups common options into typed controls; site-specific gallery-dl options remain available in an advanced editor. The engines API reports installed versions.
- Batch submission is atomic: one invalid URL rejects the entire batch, so no partial set of jobs is queued.
- The three P0 engines are yt-dlp, aria2c, and gallery-dl. The P1 and P2 adapters are also implemented, with remaining site-specific acceptance gaps called out below.
- The P1 generic direct engine (curl) and P1 you-get fallback extractor are implemented. Both P2 adapters (svtplay-dl and get-sauce) are implemented behind validated engine option forms; live site-specific acceptance remains outstanding.
- The draft PR is intentionally not merged until its live and operational acceptance checks are complete.

A feature is not complete merely because an API request succeeds or a container builds. The output file must be readable and, where configured, verified at its delivery destination.

## Quick start

1. Copy `.env.example` to `.env`.
2. Generate `APP_API_TOKEN` with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it in `.env`.
3. For WebDAV delivery, generate `APP_ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable across restarts and backups.
   Use an HTTPS WebDAV endpoint. If Nextcloud is reachable only through a trusted internal Docker network, explicitly set `ALLOW_PRIVATE_WEBDAV_TARGETS=true` and, only for an HTTP endpoint, `ALLOW_INSECURE_WEBDAV=true`. These flags are independent of `ALLOW_PRIVATE_TARGETS`, which controls submitted media sources.
4. Start with `docker compose up --build -d`.
5. Open `http://127.0.0.1:8080`, enter the API token, and submit a link.

The port binds to loopback by default. Use an authenticated TLS reverse proxy before exposing the UI/API to other machines. Do not expose the API token or aria2 RPC port publicly.

Concurrency:
- `MAX_CONCURRENT_DOWNLOADS` sets the total worker limit.
- `MAX_CONCURRENT_YTDLP`, `MAX_CONCURRENT_ARIA2`, `MAX_CONCURRENT_GALLERY_DL`, `MAX_CONCURRENT_GENERIC`, `MAX_CONCURRENT_YOU_GET`, and `MAX_CONCURRENT_SVTPLAY_DL` set independent per-engine caps.

The scheduler excludes engines that are already at their concurrency cap before limiting which queued jobs to inspect. This allows an eligible job for another engine to start even when many earlier jobs belong to a busy engine.

## API and storage

- REST base path: `/api/v1`
- Authentication: `X-API-Key`
- API reference: `/api-reference` (Scalar, bundled in the local Vite build; no external CDN or Nextcloud required)
- Media metadata preview: `POST /api/v1/preview` inspects yt-dlp formats or the first 50 playlist entries without downloading; only allowlisted metadata is returned.
- Presets: `/api/v1/presets`
- Automatic URL routing rules: `/api/v1/engine-rules`
- Batch queue: `POST /api/v1/jobs/batch` (up to 50 items)
- Re-deliver a completed job: `POST /api/v1/jobs/{id}/redeliver`. The service verifies the original completion manifest and unchanged workspace files before queuing a new destination transfer, without fetching the media again. If the original download is missing or modified, the API returns HTTP 409 and leaves the completed job unchanged; submit a new download instead.
- Checked-in API contract: [openapi.yaml](openapi.yaml)
- API contract CI compares paths/methods, success and error statuses, API-key requirements, request-body constraints, and enforced job-response shapes against FastAPI/Pydantic.
- The browser formats structured Pydantic validation errors as field-specific messages and identifies invalid batch items without echoing submitted URLs; frontend error-format tests run as part of the Docker build. Detailed schema parity remains a separate acceptance gate.
- Health check: `/healthz`
- Persistent database, job work directories, cookies, and local output: `/data`
- Delivered local files: `/data/downloads/<job-id>/`
- WebDAV directory creation accepts `MKCOL` 201, or a 405 only when a follow-up Depth:0 `PROPFIND` verifies that the exact resource is a `DAV:collection` with a successful property status. This applies to nested output folders.

## Implemented engines

- **yt-dlp:** structured progress output, output discovery, format selector, subtitle controls, cookie file support, validated additional arguments, and opt-in playlist downloads with a bounded index/range selector. The web UI can preview formats and select playlist entries. Advanced CLI options are intentionally restricted to reviewed flags and `--option=value` forms; separate positional values, extra source URLs, external downloaders, arbitrary config/output paths, proxy overrides, and other unreviewed flags are rejected. Use typed engine settings for format/subtitles/playlist controls. Extend the reviewed option list in `app/security.py` with tests before supporting additional flags.
- **aria2c:** loopback-only JSON-RPC daemon, generated RPC secret, progress polling, cancellation, and an allowlisted per-job option set.
- **gallery-dl:** gallery/image extraction, terminal progress parsing, output discovery, extractor options, cookies, and optional proxy. Output directory and progress settings remain service-managed.
- **you-get:** fallback site extractor using the pinned `0.4.1743` CLI, with format selection, service-managed output directory, optional cookie file, and proxy control. Progress reports observed bytes without inventing totals.
- **generic / curl:** direct HTTP(S) file downloads with managed filenames, retries, optional redirects, and observed-byte progress (unknown total is not fabricated). curl is already shipped in the base image for health checks. It was chosen as the generic CLI backend instead of adding the Debian trixie `wget` package, which currently has unresolved security advisories; see https://security-tracker.debian.org/tracker/source-package/wget. No custom protocol client was implemented.

- **svtplay-dl:** P2 Nordic video/broadcaster adapter pinned to `4.211`. Global and per-job graphical controls cover quality, preferred resolutions, subtitles, all episodes and MP4/MKV output. The service manages configuration and output paths; live broadcaster acceptance tests remain to be completed.
- **get-sauce:** P2 specialized extractor built from the upstream `v1.10.0` Go release with a pinned Go 1.27 builder. Exposes stream index, worker count, timeout, image limit, page selection, subfolders, and merge modes. The output path is service-managed; its live site-specific smoke test remains outstanding.

The gallery-dl options UI accepts a JSON object to expose its large and site-specific configuration surface without hard-coding every extractor option. Credential-like option names and proxies with embedded credentials are rejected; use the private cookie-file upload for session cookies.

The full engine and settings parity target is documented in [BLUEPRINT.md](BLUEPRINT.md). All seven bundled adapters are implemented; site-specific P2 smoke coverage, external-network egress enforcement, and deployment acceptance remain outstanding.

## Automated Python engine updates

The `Pinned engine updates` GitHub Actions workflow checks tracked PyPI engine releases weekly and prepares exact-version candidate PRs. A candidate may merge and publish a patch release only after the complete Python, Docker, and disposable-Nextcloud/WebDAV acceptance gate succeeds. Failures retain the previous pin and produce an `engine-update-blocked` issue. The workflow is not active until present on the repository's default branch, and apt/Go engines still need separate update tracks. See [ENGINE_UPDATES.md](ENGINE_UPDATES.md) for implementation and limitations.

## Restart and retry safety

The worker requeues interrupted downloads on restart. Files in a job directory are **not** considered completed merely because they have a normal extension. After an engine reports success, the service atomically writes a completion manifest containing each output path, size, modification time, and SHA-256 digest. New manifests verify the file contents again before recovery or re-delivery, so same-size corruption with a preserved timestamp cannot silently reuse damaged bytes. Older manifests without a digest remain supported for compatibility and retain their original size/mtime-only assurance. Reusing previously downloaded files (for example, after a WebDAV delivery failure) requires a valid manifest and unchanged output files. Otherwise, stale partial output is discarded before downloading again. Manual cancellation is preserved; stale queued cancellation flags from an unclean service shutdown are cleared during startup. When delivery was interrupted after individual files were verified, their audit records remain visible during recovery if the completed-download manifest is still valid. Re-verifying an already recorded destination replaces its entry rather than duplicating it. If the completed-download manifest is missing or invalid, stale delivery records are cleared before a fresh download; explicitly retrying a failed/cancelled job also clears the previous attempt's output records.

## Delivery

Local delivery copies files into the job's output folder and checks the resulting file. Relative paths beneath the download workspace are preserved: `album-a/image.jpg` and `album-b/image.jpg` remain separate files. Outputs outside the workspace and symlinked output paths are rejected. Generic WebDAV delivery:

1. Creates missing destination folders using MKCOL.
2. Uploads using PUT.
3. Reads remote metadata using PROPFIND.
4. Verifies the destination file size.

The dashboard offers **Re-deliver** for completed jobs. This operation uses the current local/WebDAV settings, retains the same job ID, and never removes files already present at an earlier destination. It is only available while the completed-download manifest and original source files are still valid. The previous job output list is replaced with results from the new transfer; the job log records the re-delivery request.

Cancellation during delivery is cooperative: the service checks cancellation between local-copy and WebDAV upload chunks, and between remote operations. Local files are copied to temporary paths and atomically published after size verification. Completed uploads remain listed in the job record if a subsequent upload is cancelled; cancellation **cannot guarantee deletion** of files already written to a remote server, and an interrupted WebDAV PUT may leave a partial remote file. Retrying a cancelled job attempts delivery again.

The live CI acceptance workflow uses the production downloader Compose service plus an ephemeral Nextcloud container. It runs yt-dlp, aria2c, and gallery-dl against a test fixture, retrieves each delivered file, and compares its size and SHA-256 hash. It does not connect to or modify the eventual production Nextcloud instance.

## Security notes

- The API requires a random key. The default Compose port binds to localhost.
- aria2 RPC listens on the container loopback interface and uses a generated secret.
- WebDAV app passwords are encrypted at rest using `APP_ENCRYPTION_KEY`; the settings API never returns the password.
- WebDAV destinations default to HTTPS and cannot point at literal private/reserved IPs or obvious internal hostnames. `ALLOW_PRIVATE_WEBDAV_TARGETS` is a **separate**, explicit opt-in for a trusted private Nextcloud destination; it does not enable private download sources. `ALLOW_INSECURE_WEBDAV` is a separate opt-in for plain HTTP, which sends Basic Auth credentials without transport encryption. The service rechecks destination policy at delivery and resolves public-target DNS to reject private/reserved answers, but DNS answers are not pinned in HTTPX; host-level egress filtering remains necessary against DNS rebinding.
- Cookie exports are stored in the private data volume and are not returned by the API. New uploads are staged in mode-0600 temporary files and atomically published so partially written secrets are not exposed, and existing destination symlinks are replaced rather than followed; cookie directories that are themselves symlinks are refused.
- Child processes receive argument arrays, not shell command strings.
- Downloader subprocesses and metadata previews discard inherited `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `FTP_PROXY`, and `NO_PROXY` variables (case-insensitively). The aria2 loopback RPC client and authenticated WebDAV delivery also disable HTTPX environment-proxy discovery. An explicitly configured gallery-dl proxy remains supported; host-level proxy environment settings are not silently trusted as download routing or credential handling policy. This is defense in depth and does **not** replace network egress restrictions.
- Private/local IP destinations and obvious internal hostnames are blocked by default. The shared URL validator also rejects ambiguous numeric IPv4 spellings (for example `127.1` or `0x7f000001`), malformed ports, control characters, and percent-encoded hostnames to avoid disagreement between Python URL parsing and downloader clients. Before media previews and before starting non-generic downloader engines, the server now checks all initial HTTP(S) source DNS answers (and FTP/FTPS sources used by aria2c) for private or reserved addresses; a failed preflight prevents the subprocess from starting. Recovery that reuses verified completed files does not re-query the original source host. **These checks cannot pin the downloader subprocess's later TCP connections or validate its redirects and discovered media URLs.** The generic curl adapter additionally checks **all** DNS answers before connecting, pins the approved addresses with curl `--resolve`, disables inherited proxy settings, and rejects automatic redirects unless the operator explicitly allows private targets. This guards the first direct-download connection against DNS rebinding; it does **not** secure the other engines, dynamic linked media URLs, or every outbound network path. Container/host egress restrictions are mandatory before allowing untrusted users to submit jobs.
- Logs redact common credential forms. Do not enable verbose engine traffic logging in production.
- Configure the service for trusted administrators until authentication/authorization scope and network isolation have been fully reviewed.

## Verification

The CI acceptance gate runs Python compilation and the full unit suite, verifies the checked-in OpenAPI contract, and builds the Docker image including the TypeScript/Vite production bundle. Verify the current feature-branch checks in GitHub Actions before merging; test counts are not pinned in this document.

The live Nextcloud E2E workflow has passed for all three P0 engines (yt-dlp, aria2c, and gallery-dl) using the production Compose downloader service. Each file was fetched back from disposable Nextcloud and matched against the original fixture by size and SHA-256. The temporary containers and volumes were removed after the run.

Run the unit suite locally:

```bash
python -m compileall -q app tests
python -m pytest -q
```

See [BLUEPRINT.md](BLUEPRINT.md) for the complete implementation sequence and acceptance criteria.
