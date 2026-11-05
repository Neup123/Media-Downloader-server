# Downloader Server Blueprint

## 1. Scope and source snapshot

This document records the clean-room behavior and settings inventory used to design Downloader Server. It is a behavioral and interface blueprint only. No Media Downloader implementation code is copied or adapted.

Reference project: https://github.com/mhogomchungu/media-downloader  
Reference branch: main  
Reference commit inspected: 46e881e3a969b6a84d22c809805a63c3f3c3d6a4  
Reference commit date: 2026-11-04

The inspection covered the upstream README, engine adapters under src/engines, engine extension definitions under extensions, engine fixtures, mainwindow.ui, configure.cpp, settings.cpp, batchdownloader.cpp, and playlistdownloader.cpp. The README identifies yt-dlp, gallery-dl, you-get, svtplay-dl, aria2c, wget, and get-sauce as the supported downloader extensions.

The engine inventory and defaults below are taken from the upstream extension definitions and adapters. Any setting absent from an extension definition is unspecified here; it must not be guessed. The downloader's own current documentation is authoritative whenever it differs from this snapshot.

## 2. Service stack decision

**Decision: Python 3.13, FastAPI, SQLAlchemy 2, SQLite, and a React + TypeScript web UI.**

Reasons:
- Python provides a direct, well-supported subprocess interface and suits adapters for the Python-based yt-dlp, gallery-dl, you-get, and svtplay-dl tools.
- FastAPI provides typed request/response models and an automatically generated OpenAPI document. The repository will also maintain the required canonical openapi.yaml contract and test it against the served schema.
- Scalar will render the API reference from the OpenAPI document. Swagger UI and ReDoc are not the product's API-reference interface.
- SQLAlchemy 2 provides a persistence boundary. SQLite is the initial single-container database; a named Docker volume keeps job/settings state across restarts. The repository should avoid SQLite-specific assumptions in business logic.
- The job manager will use its own persistent worker loop and asyncio subprocesses. FastAPI request background tasks are not the durable job queue.
- Server-Sent Events (SSE) are the initial progress transport. Job state remains queryable over REST if a client disconnects.
- Docker Compose will install the complete standalone service. It must not depend on Nextcloud, Redis, Postgres, Coolify, or any other separately deployed service.

The API process will execute child programs with argument arrays, never a shell command assembled from user input. Each engine adapter owns command construction, progress parsing, cancellation, output discovery, and error normalization.

## 3. Engine inventory

### 3.1 P0 — yt-dlp

Role: primary site extractor and media downloader.

Upstream invocation and behavior:
- Executable: yt-dlp.
- Download defaults recorded upstream: --newline, --ignore-config, --no-playlist, and output template %(title).200s-%(id)s.%(ext)s.
- Format-selection argument: -f.
- Playlist item selection: --playlist-items.
- Batch input: -a.
- Browser-cookie argument: --cookies-from-browser.
- Cookie-file argument: --cookies.
- List/metadata mode uses --match-filter !playlist, --no-playlist, --newline, and --print with a JSON template.
- The metadata template includes HTTP headers, uploader, ID, thumbnail, duration, title, upload date, webpage URL, available formats, and playlist metadata.
- Subtitle discovery prints subtitles and automatic captions as JSON without downloading.
- Subtitle download default includes --embed-subs.
- Comment discovery uses --get-comments, --no-download, and --print.
- Version argument: --version.
- The upstream progress fixture shows structured fields including downloaded_bytes, ETA, total_bytes_estimate, total_bytes, progress.speed, and filename. The new adapter should use yt-dlp's documented --progress-template format rather than parse the terminal progress bar.

UI settings taxonomy:
- Format code / quality selection.
- Output filename template and output path.
- Playlist handling and playlist item range.
- Cookie source: browser or cookie file.
- Subtitle discovery and embedding.
- Comment discovery.
- Custom CLI options.
- Per-download overrides layered over the engine's global defaults.

Upstream extension defaults:
- AutoUpdate: true.
- Default download options: --newline --ignore-config --no-playlist -o %(title).200s-%(id)s.%(ext)s.
- Default list options: JSON metadata template described above.
- Default comments options: --get-comments --no-download --print with title/comments JSON.
- Default subtitle options: --no-download --print with title/automatic_captions/subtitles JSON.
- Default subtitle download options: --embed-subs.
- Options argument: -f.
- Playlist items argument: --playlist-items.
- Batch argument: -a.
- Version argument: --version.
- Cookie arguments: --cookies-from-browser and --cookies.

### 3.2 P0 — aria2c

Role: direct HTTP/HTTPS/FTP/SFTP downloads, BitTorrent, and magnet links.

Upstream invocation and behavior:
- Executable: aria2c.
- Upstream command adapter adds -d with the configured download folder if no destination directory is supplied.
- Upstream output recognizes progress lines beginning with [DL: or [# and extracts completed file names from Download complete messages.
- The new service will use aria2's JSON-RPC interface as its source of truth for job status and progress. The aria2 daemon must listen only on the container loopback interface, use a generated RPC secret, and never expose its RPC port publicly.
- RPC fields of interest include GID, status, totalLength, completedLength, downloadSpeed, uploadSpeed, connections, and errorCode/errorMessage where available.
- Global and per-job options map to aria2's documented option names. Values in aria2 option objects are strings.

UI settings taxonomy:
- Global aria2 options editor organized by categories from aria2's documented option set.
- Per-download option overrides.
- Destination directory and filename.
- Connections/split settings, bandwidth limits, retry/timeouts, proxy, authentication/cookies, BitTorrent and Metalink-specific controls when applicable.
- Advanced raw option editor remains available, but normal settings must be graphical.

Upstream extension defaults:
- AutoUpdate: false.
- Download options and list options: empty arrays.
- Batch-file argument: empty.
- Playlist support: false.
- Version argument: --version.
- Upstream progress recognition is CLI-output based; the server implementation uses JSON-RPC instead.

### 3.3 P0 — gallery-dl

Role: image galleries and supported image-board extractors.

Upstream invocation and behavior:
- Executable name varies by platform/package; the extension declares gallery-dl.bin on generic Linux, gallery-dl_macos on macOS, and gallery-dl.exe on Windows.
- Cookie source arguments: --cookies-from-browser and --cookies.
- Additional option override: -o KEY=VALUE.
- Metadata discovery uses --no-skip, --no-download, --quiet, and the metadata postprocessor with event=prepare and filename=-.
- Upstream adapter parses JSON metadata records and normalizes direct_url, fileName, title, and thumbnail.
- Progress output is line-based; keep versioned parser fixtures rather than depending on a single human-readable layout.

UI settings taxonomy:
- Global extractor configuration, including category/site settings from gallery-dl's JSON configuration tree.
- Browser cookies or cookie file.
- Per-download extractor option overrides.
- Filename/path rules and postprocessor settings.
- Archive/skip behavior and request delays where supported by the current gallery-dl docs.

Upstream extension defaults:
- AutoUpdate: true.
- Download options and list options: empty arrays.
- Playlist support: true.
- Cookie arguments: --cookies-from-browser and --cookies.
- Options are passed with -o.
- Metadata extraction arguments use the metadata postprocessor described above.
- Version argument: --version.

### 3.4 P1 — generic / wget

Role: plain direct-file downloads and fallback for URLs that already resolve to files.

Upstream wget behavior:
- Executable: wget.
- Batch-file argument: -i.
- Default download option includes --progress=bar:force.
- The upstream adapter distinguishes wget 1.x, legacy wget2, and current wget2 output and parses their progress formats separately.
- Wget progress reports downloaded amount, speed, and sometimes time remaining and total length. Version detection is required before choosing a parser.

The generic adapter is a direct-download fallback, not a website extractor. It should use a documented CLI engine rather than duplicate a general-purpose downloader in project code. The exact generic fallback policy will be specified with the P1 implementation.

Upstream wget extension defaults:
- AutoUpdate: false.
- Batch argument: -i.
- Default download options: --progress=bar:force.
- Version argument: --version.
- Playlist support: false.

### 3.5 P1 — you-get

Role: fallback site extractor, especially for sites where it remains useful.

Upstream invocation and behavior:
- Executable: you-get, with Python invocation on Windows in the upstream extension.
- Format argument: --format.
- Batch argument: -a.
- List argument: --json.
- Cookie argument: --cookies.
- Proxy argument in the adapter: --http-proxy.
- JSON extraction exists, but the upstream project warns that its JSON schema is not stable. Treat the parser as versioned and fixture-backed.
- Human output includes title, stream information, quality, container, size, and progress.

Upstream extension defaults:
- AutoUpdate: true.
- Batch argument: -a.
- Default list options: --json.
- Options argument: --format.
- Cookie argument: --cookies.
- Playlist support: false.
- Version argument: --version.

### 3.6 P2 — svtplay-dl

Role: niche Swedish public-service media extractor.

Upstream invocation and behavior:
- Executable: svtplay-dl.
- Quality argument: -q.
- Batch argument: -a.
- List options: --list-quality.
- Progress output includes item counts and ETA; parser fixtures are required.
- Current command-line documentation includes output and filename control, authentication, tokens, HTTP headers, cookies, proxy, socket timeout, max retries, quality/resolution, audio/video selection, subtitle selection, remuxing and output format.

UI settings taxonomy:
- Quality/resolution and preferred stream format.
- Subtitle download, merge, all-subtitles, forced/required subtitles.
- Audio language and video role.
- Cookie/header/proxy options.
- Timeout and retry controls.
- Post-processing and output container format.

Upstream extension defaults:
- AutoUpdate: true.
- Batch argument: -a.
- Default list options: --list-quality.
- Options argument: -q.
- Default comments, subtitle discovery, and subtitle-download options: empty arrays.
- Version argument: --version.
- Playlist support: false.

### 3.7 P2 — get-sauce

Role: niche extractor for supported image/media sources.

Upstream invocation and behavior:
- Executable: get-sauce.
- Selection argument: -s.
- List options: -j for JSON extraction.
- The CLI supports options for amount, captions, URL-list input, user headers, info, JSON, merge mode, output name/path, page ranges, quiet mode, stream selection, subdirectories, timeout, version, and worker count.
- JSON extraction should be used for metadata where supported; download progress parsing must have fixtures.

Upstream extension defaults:
- AutoUpdate: true.
- Default list options: -j.
- Options argument: -s.
- Default download options: empty.
- Version argument: -v.
- Playlist support: false.

## 4. Cross-engine settings model

The settings UI is a core feature. A settings page that only exposes a config-file path is not acceptable.

Every engine has:
1. A typed global settings object, backed by a versioned engine schema.
2. A visible list of default command options, including add, edit, remove, and mark-as-default actions.
3. A per-download override panel using the same setting schema.
4. An advanced raw-option editor for options not represented by typed fields.
5. Cookie and proxy controls where the engine supports them.
6. A version indicator and a clear statement of whether the engine executable is installed and runnable.

Effective options are resolved deterministically:
- Engine built-in defaults.
- Service safe defaults.
- Saved global engine settings.
- Per-download overrides.
- Required service-managed arguments (working directory, output path, progress format, and safety restrictions).

Required arguments must not be silently replaced by user input. The UI must make the effective option set inspectable before a job is submitted.

### Engine extension definition fields

The upstream extension JSON files expose the following recurring fields. The new implementation will use a new clean schema; these names are an inventory, not a requirement to copy the upstream JSON format.

- AutoUpdate and executable download source metadata.
- BackendPath and platform/architecture-specific command definitions.
- BatchFileArgument and playlist capability.
- VersionArgument and version-output parsing hints.
- OptionsArgument and PlaylistItemsArgument.
- DefaultDownLoadCmdOptions and DefaultListCmdOptions.
- DefaultCommentsCmdOptions, DefaultSubstitlesCmdOptions, and DefaultSubtitleDownloadOptions where supported.
- CookieArgument and CookieArgumentTextFile.
- ControlJsonStructure, SplitLinesBy, SkipLineWithText, RemoveText, and ReplaceOutputWithProgressReport, used by the desktop app's output parser.
- Engine name, extension definition version, and minimum desktop-app version.

The service's adapter contract must normalize these into engine capabilities, settings schema, command construction, progress events, output files, and errors.

## 5. Shared settings and configuration taxonomy

The desktop Configure screen contains these categories. Server-relevant items are retained; desktop-only presentation and operating-system features are translated or excluded explicitly.

### Retain or adapt

- Download destination.
- Maximum concurrent downloads and concurrent-download enablement.
- Per-engine default options.
- Per-engine option history.
- Per-engine browser cookie source or cookie-file path.
- Per-engine text encoding.
- Preset option sets, with a name, description/site association, and option string.
- URL-to-default-engine rules.
- Proxy modes: system configuration, manually configured proxy, environment-derived proxy, and no proxy.
- Update channel/check status for bundled engines.
- Save download history.
- Auto-hide completed jobs.
- Batch metadata display and batch queue behavior.
- Playlist list/range history and archive semantics, where implemented.

### Desktop-only settings that do not transfer literally

- System tray icon and desktop notifications.
- Native file/folder dialogs.
- OS clipboard polling.
- Theme folder and desktop UI scaling controls.
- Local media-player integration.
- Single-instance desktop application behavior.
- Localized desktop window dimensions and tab restoration.

The server UI may provide browser-native equivalents where useful, but must not introduce hidden host dependencies.

## 6. Screen inventory and server mapping

| Reference screen | Reference behavior | Server product mapping |
|---|---|---|
| Basic Downloader | Submit one URL; choose engine, format code, and options; inspect metadata; start download | Submit page with URL, engine picker, format/quality discovery, per-job overrides, and submit action |
| Batch Downloader | Add multiple URLs; show extracted metadata; select media/format; manage batch rows; cancel and inspect per-item results | Batch submission and grouped jobs; each item has independent status, output, and log |
| Playlist Downloader | Fetch playlist listing; choose range and options; manage subscriptions and archive file | Playlist-aware submission and selection. Subscription polling is later scope, not MVP |
| Library | Browse downloaded items and local download folder | Download history, output files, delivery state, and retry/re-deliver actions |
| Configure | General, engine options, presets, extensions, URL manager, proxy, UI scaling | Settings pages for service, engines, presets, URL rules, delivery, and proxy |
| Log view | Inspect command output and download events | Per-job structured event log and live log panel |

Important table/job actions observed upstream include show media options, show subtitles, show comments, download a media part, rename output, retry/force download, and inspect logs. These should be capability-gated by the selected engine.

## 7. Output parsing and progress contract

Every adapter emits the same normalized progress event shape:
- job_id and engine.
- state: queued, starting, extracting, downloading, post-processing, delivering, completed, failed, cancelled, or interrupted.
- item/file identity where a URL expands to multiple files.
- downloaded_bytes and total_bytes when known.
- percent when computable.
- speed_bytes_per_second and eta_seconds when known.
- current filename and output path.
- timestamp and sanitized message.

Prefer structured output:
- yt-dlp: documented progress-template and JSON metadata.
- aria2: JSON-RPC status and notifications.
- gallery-dl: metadata JSON and tested output parser.
- get-sauce and you-get: JSON discovery where available, with parser fixtures because output/schema stability is limited.
- svtplay-dl and wget: version-aware text parsers covered by recorded fixtures.

Unknown total size must remain null. Never display a fabricated 100% or estimate. Parser fixtures must be stored in the repository and run offline.

## 8. Architecture decisions for implementation

1. API: versioned REST API for job submission, listing, detail, cancellation, retry, engine definitions/settings, presets, delivery settings, and health.
2. API contract: checked-in openapi.yaml. Scalar renders this contract; a test checks it against the implemented API.
3. Job manager: persistent queue and worker loop, global and per-engine concurrency limits, durable state transitions, cancellation, retry, recovery after restart, per-job logs.
4. Engine adapters: one adapter per engine; declarative schemas where the CLI permits it.
5. Settings store: typed global engine settings plus per-job overrides; secrets encrypted at rest using an operator-supplied key and always redacted from logs.
6. Web UI: submit, batch/playlist discovery, queue, progress, job details/logs, engine settings, presets, URL rules, and delivery configuration.
7. Delivery: generic local-directory or WebDAV target. WebDAV delivery uses authenticated PUT to the destination file path, creates missing directories using WebDAV operations, then verifies the delivered file by reading destination metadata. Never write directly into a Nextcloud data directory.
8. Packaging: single standalone Docker Compose installation with persistent data and download volumes, health check, restart policy, and a non-root runtime user.
9. Observability: structured service logs plus per-job stdout/stderr records with secrets and authentication headers removed.

## 9. Nextcloud adaptation boundary

The core repository remains Nextcloud-agnostic. Its WebDAV target is generic and configurable.

The Nextcloud repository contains only:
- AppAPI ExApp packaging and lifecycle integration, if supported by the target instance.
- Nextcloud UI integration and user-context handoff.
- Preconfigured WebDAV delivery settings and destination folder.
- Nextcloud-specific setup, update, uninstall, and notification behavior.

Preferred deployment is an AppAPI ExApp. The target server must be inspected before choosing its final packaging. Verify the installed Nextcloud version, AppAPI presence/enabled state, Deploy Daemon registration and reachability, reverse-proxy/HaRP feasibility, and WebDAV authentication before committing to the ExApp path.

Fallback order:
1. AppAPI ExApp with a registered Deploy Daemon.
2. Standalone service container on the same Docker host, embedded using the official External Sites app.
3. A thin PHP bridge only if both prior options are unavailable. The bridge must not contain download logic.

No target-instance facts are assumed in this blueprint.

## 10. Engine update automation

Implement in the core repository using GitHub Actions:
- Weekly scheduled check and manual dispatch.
- Detect upstream releases for every bundled engine.
- Pin engine versions and checksums; never use floating downloads at runtime.
- Create a dedicated candidate branch and pull request for each update batch.
- Run per-engine smoke tests against stable legal targets, parser fixture tests, and a WebDAV delivery test against a disposable target.
- Merge and publish a patch release only after the complete gate passes.
- On failure, leave the previous known-good version in place and open an issue labelled engine-update-blocked with both versions, failed checks, log excerpts, artifact links, and a first-pass upstream change summary.
- Workflow permissions are least-privilege; runs are serialized; secrets never appear in logs.

## 11. Acceptance gates

A feature is complete only when its behavior is demonstrated. Required gates:
- Engine smoke tests and offline fixture parser tests.
- Job state persistence and worker recovery across service restart.
- Cancellation and retry tests.
- Per-engine global settings and per-job overrides are both exercised through the UI/API.
- API contract test against openapi.yaml.
- Clean Docker Compose build and startup from the repository's own compose file.
- Submit a real download through the API, observe progress, verify the output file is readable, deliver it to a destination, and verify the delivered file exists with the expected size.
- WebDAV delivery test verifies destination metadata after PUT, not just an HTTP success status.
- No unverified build or deployment configuration is enabled permanently.

## 12. Source references

### Behavioral blueprint
- Media Downloader repository and README: https://github.com/mhogomchungu/media-downloader
- Engine implementations: https://github.com/mhogomchungu/media-downloader/tree/main/src/engines
- Extension definitions: https://github.com/mhogomchungu/media-downloader/tree/main/extensions
- Main UI and configuration screens: https://github.com/mhogomchungu/media-downloader/blob/main/src/mainwindow.ui and https://github.com/mhogomchungu/media-downloader/blob/main/src/configure.cpp
- Settings defaults: https://github.com/mhogomchungu/media-downloader/blob/main/src/settings.cpp

### Current engine documentation
- yt-dlp options, output templates, JSON, progress templates, cookies: https://github.com/yt-dlp/yt-dlp/blob/master/README.md
- gallery-dl options: https://github.com/mikf/gallery-dl/blob/master/docs/options.md
- gallery-dl configuration: https://gdl-org.github.io/docs/configuration.html
- aria2 manual and JSON-RPC: https://github.com/aria2/aria2/blob/master/doc/manual-src/en/aria2c.rst
- you-get CLI and JSON behavior: https://github.com/soimort/you-get/blob/develop/README.md
- svtplay-dl options: https://svtplay-dl.se/options/
- get-sauce options: https://pkg.go.dev/github.com/gan-of-culture/get-sauce

### Service and deployment documentation
- FastAPI metadata/OpenAPI: https://fastapi.tiangolo.com/tutorial/metadata/
- FastAPI container deployment: https://fastapi.tiangolo.com/deployment/docker/
- SQLAlchemy asyncio and SQLite: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html and https://docs.sqlalchemy.org/en/20/dialects/sqlite.html
- Scalar FastAPI integration: https://scalar.com/docs-for/fastapi
- Docker Compose health checks and volumes: https://docs.docker.com/reference/compose-file/services/ and https://docs.docker.com/reference/compose-file/volumes/

### Nextcloud documentation
- AppAPI and ExApps: https://docs.nextcloud.com/server/stable/admin_manual/exapps_management/AppAPIAndExternalApps.html
- Nextcloud WebDAV operations: https://github.com/nextcloud/documentation/blob/master/developer_manual/client_apis/WebDAV/basic.rst

---

**Phase status:** Phase 0 blueprint and service-stack decision. No application code is included in this phase.
