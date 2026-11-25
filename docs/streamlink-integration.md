# Streamlink recording adapter integration (#160)

Current implementation is an internal one-shot recording adapter. It is not
registered for public API submission, automatic detection or user-facing
settings. The dependency is pinned to Streamlink 8.6.3; see
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

No new job states or database flags are written here. The worker's durable
Stop request and owner-scoped API/UI wiring still need an explicit transition
contract before activating this engine. The adapter parses the local recording
with bounded FFprobe and returns a filename matching its detected container.

## Acceptance and installation

The production Docker image installs the pinned dependency but does not expose
a new engine. In CI, `tests/e2e_streamlink_adapter.py` runs inside the actual
built image with no external network: generate a legal HLS fixture, serve it
over loopback, record a finite playlist, request graceful Stop on an ongoing
playlist, and decode a video frame from both. The unit suite checks the fixed
CLI, quality restrictions and Stop/Cancel/error semantics. This test does not
verify Twitch, YouTube Live, authorization gating, destination WebDAV, or
target egress. Those are separate release criteria under #160/#155/#156/#72.

## Operator use

There is no new UI action yet. Do not interpret the installed binary as an
available recording engine. When the feature is activated, Stop will finalize
playable bytes and proceed through verified delivery; Cancel will discard
unverified output. Public source detection and Live labels must be wired in
the native and standalone flows without duplicating the engine.
