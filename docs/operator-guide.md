# Standalone downloader: daily operator guide

This describes the Core web dashboard only. The Nextcloud Files/Dashboard/Library plugin is a **separate interface** in the [Nextcloud adaptation](https://github.com/Neup123/downloader-server-nextcloud).

## Start a download

1. Sign in to the standalone Core web dashboard using the administrator credential stored outside browser assets. The service is administrative; only trusted operators should use this surface.
2. Paste a public source link into the download field. Inspect the result of the metadata preview before starting. A successfully queued job **does not mean** its files have been delivered.
3. Choose a supported download engine or let automatic routing select it. yt-dlp handles supported media sites, aria2 handles magnets and compatible direct links, gallery-dl handles supported galleries, and the generic engine handles HTTP(S) files.
4. For a playlist, select only the required entries. Flat metadata may omit thumbnail, duration or byte size; unknown values are not a reason to invent an estimate.
5. When supported, select a preset or reviewed quality/format options, then submit. The service snapshots the chosen engine and settings into each queued job.
6. Follow the job in Library until delivery is verified. A completed transfer must be checked at the configured local/WebDAV destination, not inferred from the disappearance of the progress bar.

The interface is being simplified through a separate interactive [link-first prototype PR](https://github.com/Neup123/Media-Downloader-server/pull/200). That prototype is **not** a deployed replacement or verified production UI.

## Where files go

The administrator configures the delivery destination under Delivery. Local output defaults to the persistent `/data/downloads/<job-id>/` path. A configured WebDAV destination uses authenticated upload and readback verification. The supported standalone one-off subfolder is a single safe child name, not an arbitrary remote path or URL.

A destination failure does not prove a media engine failure. Check that the target folder exists, is writable, has quota, and that the WebDAV host and credentials are valid. Use the built-in destination verification procedure to prove write, readback and cleanup before trusting the destination.

The Nextcloud plugin supplies owner-scoped server-side WebDAV credentials; do not enter a user's app password into the Core browser or log it.

## Library: understand and recover

| Status | Meaning | Next action |
| --- | --- | --- |
| Queued/starting | Accepted, not yet transferred | Monitor or cancel if no longer wanted |
| Downloading or processing | Engine is running | Wait, or pause only when the advertised engine capability permits |
| Delivering | Engine finished; destination verification is still pending | Inspect delivery failures before retrying |
| Completed | Output and delivery verified according to the configured mode | Open/read the real destination file |
| Failed | Engine or delivery stopped with an error | Read the specific failure; use Retry only after resolving its cause |
| Cancelled | An operator deliberately stopped the job | Do not bulk-retry; start a new action only intentionally |

A failed job may be retried; the service uses the canonical lifecycle transition rather than rewriting its status directly. A completed job can be **re-delivered without fetching the source again** only while the completion manifest and original bytes remain verifiable. If Core reports that the workspace is missing or changed, submit a new download rather than treating the stale manifest as valid.

Use status, error detail, and verified output together. Logs can contain sensitive source details; never paste unredacted content into public issues.

## Presets, engine settings and automatic routing

Presets hold reviewed, validated engine options. Per-job choices override preset values only within the permitted option catalog. Automatic routing selects an engine using saved hostname suffix/path-prefix rules and a documented priority/specificity order. Disabling or changing a rule affects **future** jobs, not already queued ones.

The ordinary workflow is link → preview → selection → submit → Library. Keep experimental engine flags, site-specific extractor settings and routing rules out of the primary path.

## Cookies and accounts

Use the private cookie-upload mechanism only when an authorized source requires it. Do not paste cookies, proxy credentials, session tokens or app passwords into issue descriptions, terminal command history, screenshot annotations or diagnostic logs. A cookie file is an authentication artifact; invalidate and replace it if exposed.

## When the service looks healthy but downloads do not move

The HTTP `/healthz` endpoint checks process liveness only. A queue can still be unhealthy if the database, scheduler or transfer engine cannot work. Once deployed, the separately proposed `/readyz` endpoint checks database and scheduler readiness, not remote sites, host egress, or final WebDAV delivery.

For a reproducible diagnosis: record the affected job's **redacted** ID and status, inspect whether the queue/scheduler is advancing, run a disposable fixture download into a test destination, and verify actual bytes by readback. Escalate unresolved failures with sanitized steps and an exact commit identifier.

Do not expose the downloader to arbitrary untrusted source URLs while the host-level egress safety and real-site acceptance gates remain open. See [installation/acceptance](installation-acceptance.md).
