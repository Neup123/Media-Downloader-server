# Streamlink recording adapter integration (#160)

Current implementation is a one-shot recording adapter with explicit standalone
API admission controlled by the existing engine policy (disabled by default).
Automatic provider detection and native Nextcloud UI remain separate work. The dependency is pinned to Streamlink 8.6.3; see
https://pypi.org/project/streamlink/8.6.3/ and
https://streamlink.github.io/cli.html. Uses the maintained CLI, not a custom
HLS extractor. The adapter disables local config, plugin cache/sideloading,
web browser and ambient HTTP proxy configuration. No credentials are accepted
in this slice. For later vault integration prefer the documented domain-scoped
Netscape cookie-file option rather than transmitting every cookie to every host.

Local CLI lifecycle (shared `app/adapters/common_cli.py`):

| Input | Action | Exit |
| --- | --- | --- |
| Normal end | Wait for process | 0 and valid media |
| Explicit Stop | Send SIGINT once, allow bounded finalization | 0 or handled exit 130 and valid media |
| Cancel | Terminate process group, discard unverified output | Cancelled; never return output |
| Stop timeout / other error | Terminate and fail | No delivery |

The worker owns the durable Stop request and existing job transitions; the
adapter does not write job state. Standalone Stop is wired; native Nextcloud
and provider requirements remain gated. The adapter parses the local recording
with bounded FFprobe and returns a filename matching its detected container.

## Acceptance and installation

The Docker image installs the pinned dependency; explicit standalone recording
requires enabling the existing administrator policy. In CI, `tests/e2e_streamlink_adapter.py` runs inside the actual
built image with no external network: generate a legal HLS fixture, serve it
over loopback, record a finite playlist, request graceful Stop on an ongoing
playlist, and decode a video frame from both. The unit suite checks the fixed
CLI, quality restrictions and Stop/Cancel/error semantics. This adapter test alone does not verify destination WebDAV. The disposable
WebDAV workflow additionally runs the real standalone browser Stop scenario
and a finite HLS recording that must end without Stop, decode a frame, cover
the eight-second synthetic fixture, and match the durable local manifest to
streaming remote size/SHA-256. It restores the prior engine policy. No external
provider, credentials, native Nextcloud UI or target egress is tested. Those are separate release criteria under #160/#155/#156/#72.

## Operator use

Explicit standalone jobs can be enabled by an administrator. Stop finalizes
playable bytes and proceeds through verified delivery; Cancel discards
unverified output. The installed binary alone is not evidence that an engine
is enabled or a public provider is supported. Public source detection and Live labels must be wired in
the native and standalone flows without duplicating the engine.
