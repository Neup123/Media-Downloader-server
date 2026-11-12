# Background services — A/B contract

## Settings and status

Use the existing authenticated `GET`/`PUT /api/v1/engines/{engine}/settings`
for `engine = pyload` or `cobalt`. The exact persisted key is **`mode`**:

```json
{"settings":{"mode":"off"}}
```

Allowed strings: **`off` / `always` / `on-demand`**. Missing mode defaults to
`off`, including existing installations with old boolean opt-ins. The old
`/policy` PUT returns 422 for these two engines. Other engines are unchanged.
Mode is administrative global configuration; presets and job overrides cannot
grant permission. Save applies immediately: off stops, always starts, on-demand
does not start a service without a job. Switching always to on-demand stops an
idle service; active/waiting jobs keep their references until handover.

Settings responses and engine registry rows expose `state`:
`stopped`, `starting`, `running`, `stopping`, plus nullable `service_error`.
Registry rows also expose `mode`. A failed start retains the selected mode and
reports the error; saving it again retries. The next job also retries.
The API does not prescribe how B renders these fields.

Job `progress.phase` is `starting_service` while acquiring/readiness-waiting,
with `progress.service = pyload|cobalt`; normal engine progress changes phase
to `downloading`. Delivery clears this phase. The last job is already completed
during the 180-second grace. When its idle timer actually stops the service,
that job briefly reports `stopping_service`, then clears it without changing
its completed status. Engine live state remains the authority during grace.

## Runtime

Core owns foreground children within its container; no Docker socket or sibling
container. Cobalt source and its lockfile are pinned in Dockerfile. Its Node
runtime is included in the Core image. Services remain off on fresh installs.

pyLoad requires an owner-configured profile at `/data/services/pyload`, an
owner-generated `pl_` REST key, and its storage root. Configure:

```
PYLOAD_API_URL=http://127.0.0.1:8000
PYLOAD_API_KEY=pl_...
PYLOAD_SHARED_ROOT=/data/pyload-downloads
COBALT_API_URL=http://127.0.0.1:9000
```

Keep pyLoad's profile web UI bound to loopback, matching that API URL/port.
The guarded Compose profile permits only the default IPv4 loopback API ports
8000 and 9000; keep these endpoints at their defaults in that profile.
The default launch passes `--storagedir` to match the configured shared root.
It disables upstream PID-file handling (`--pidfile ''`): Core owns and reaps
the child itself, so stale/reused upstream PID files cannot select or kill an
unrelated process. An occupied API port is specifically reported as an unowned
or stale process; free it and retry. Core never blindly kills a PID from disk.
Readiness polls the authenticated pyLoad API or cobalt root for up to 30 seconds.

Optional deployment-only `PYLOAD_COMMAND` / `COBALT_COMMAND` are JSON argv arrays
for foreground wrappers; they are never accepted from job/settings payloads.
Overrides must honor the configured loopback endpoint and stay in foreground.
Stdout and stderr are drained together into retained, redacted job logs. TERM
gets 10 seconds, then KILL gets 10 seconds; forced/failed stops appear in logs
and engine `service_error`, preserving completed job results.

Concurrent jobs use one process and idempotent job-ID references. Captcha jobs
retain a reference between worker invocations and restore it at Core startup.
The lease is released after verified delivery, or after failure/cancellation;
terminal captcha jobs are reconciled by the worker. The last release schedules
a 180-second idle timer, cancelled if another job needs the service.

Cobalt is available explicitly or as a permission-checked fallback after a
yt-dlp extraction failure. Its resolved redirect URL is validated through Core's
normal network policy. Only a tunnel on the configured loopback API origin and
`/tunnel` path can use the local exception. Picker responses are reported as
unsupported rather than guessing which media to download.

## Verification limits

Automated tests exercise actual subprocess readiness/reaping and deterministic
worker handover, captcha, modes, concurrency and fallback behavior. Real hoster
downloads, manual captcha interaction, and idle RAM measurements require the
integration rig and its owner-configured pyLoad profile/hoster credentials.
