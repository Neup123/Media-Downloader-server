# Guarded downloader network namespace (experimental deployment)

This is a **separate** Compose deployment, not an override of `compose.yaml`.
The standard stack is intentionally unchanged so its disposable Nextcloud
fixtures continue to work. The guarded stack is intended for a downloader
whose media sources and WebDAV destination are **publicly routable**.

## Launch

1. Put a long random `APP_API_TOKEN` and a persistent `APP_ENCRYPTION_KEY`
   in a private `.env` (see `.env.example`); keep the API bound to
   `127.0.0.1` unless protected by a trusted reverse proxy and access control.
2. Run `docker compose --env-file .env -f compose.egress.yaml up -d --build --wait`.
3. Inspect the two health checks. The downloader only starts after the guard
   has installed IPv4 and IPv6 kernel firewall chains. A guard unable to
   install netfilter rules must fail its health check, blocking startup.
4. Verify with `docker compose --env-file .env -f compose.egress.yaml ps`.
   Use `down` without `--volumes` when retaining downloads.

## Boundary and tradeoffs

- The `egress-guard` sidecar has `NET_ADMIN` to install firewall rules.
  The actual downloader shares its network namespace but **does not** have
  `NET_ADMIN`; it drops all capabilities and runs as an unprivileged user.
  Its yt-dlp, aria2c, gallery-dl, curl, you-get, svtplay-dl and get-sauce
  subprocesses therefore inherit the same kernel egress rules.
- IPv4 loopback is rejected except TCP to `127.0.0.1:8080` (service health)
  and `127.0.0.1:16800` (aria2 RPC). Docker's embedded DNS resolver
  `127.0.0.11` is permitted only on TCP/UDP port 53. Private, shared,
  metadata/link-local, documentation, multicast and reserved IPv4 ranges
  are rejected. IPv6 is restricted to global unicast with documented
  transition/documentation ranges blocked.
- Because the rules match **destination IPs in the kernel**, a public-looking
  hostname resolving to a blocked IP, a redirect to that IP, or a late DNS
  rebinding to that IP is rejected independently of engine behavior. The
  [kernel egress CI](.github/workflows/egress-guard-e2e.yml) checks actual
  downloader namespace connections, including simulated rebinding.
- The guard blocks internal Nextcloud WebDAV addresses (Docker bridge, LAN,
  Tailscale private addresses) by design. Use a public HTTPS endpoint or a
  separately designed, tightly scoped authenticated delivery path; **do not
  add broad private-network exceptions to make delivery work**.
- Docker Desktop, rootless Docker, non-Linux hosts, Kubernetes, IPv6-disabled
  kernels and alternate firewall backends require independent verification.
  An external host firewall and cloud network ACLs remain advisable.
  The sidecar requires Linux kernel netfilter and a runtime that permits
  `NET_ADMIN`. It does not secure a compromised Docker daemon or guard
  sidecar, and is not a general-purpose network security appliance.
- No production release approval is implied. Host-level enforcement and
  application-level URL checks are complementary. Remaining work includes
  platform-specific acceptance, end-to-end guarded public downloads,
  controlled DNS/redirect tests, and review of non-IP exfiltration channels.

## CI acceptance

`egress-guard-e2e.yml` builds the guarded Compose stack on an ephemeral
Linux runner, waits for kernel policy installation, probes the actual
downloader namespace (not just the sidecar) for blocked private, metadata,
loopback and IPv6 destinations, then removes containers and volumes.

This is the first concrete deployment boundary for issues #9 and #72;
keep them open until platform and production acceptance are complete.
