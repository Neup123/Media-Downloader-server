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
  downloader namespace connections, including simulated rebinding and a controlled real HTTP 307 redirect to denied\n  private/metadata destinations. The redirect originates from the local\n  downloader health route using a test-only Host header; it needs no external\n  redirect website, credentials, or actual metadata-server access.
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
  other platform-specific DNS/redirect and engine-specific tests, and review\n  of non-IP exfiltration channels.

## Actual WSL/Docker-host acceptance

On the **same Linux/WSL host** that runs an already started guarded
downloader, execute this read-only network probe from the original core
checkout (use the same Compose files and environment file as the running
stack):

```sh
python3 scripts/guarded_host_acceptance.py --env-file .env --compose-file compose.egress.yaml
```

If the optional Nextcloud network overlay is in use, pass the **same**
overlay after the base file and make sure `NEXTCLOUD_DOCKER_NETWORK` is set
to the actual existing network:

```sh
python3 scripts/guarded_host_acceptance.py --env-file .env \
  --compose-file compose.egress.yaml \
  --compose-file /path/to/downloader-server-nextcloud/compose.nextcloud-network.yaml
```

The script does not create or delete containers, files, credentials, app
passwords, or destinations. It checks real policy chains inside the guard
container, probes the actual downloader namespace, and requires the kernel
`REJECT` counters to advance for DNS-override/metadata, loopback, RFC1918
10/8 and 192.168/16, and controlled redirect requests. A socket failure alone is not sufficient
evidence; the output is sanitized JSON without credentials or media URLs.
IPv6 probes can also fail because no route exists, so the report
distinguishes that from a verified IPv6 packet counter.

Passing on Linux CI is **not** WSL acceptance. A successful host probe
still reports `production_approved: false` because authenticated remote
WebDAV file readback, actual host DNS/TLS/hairpin reachability, and legally
authorized real-site engines must be checked separately. Capture the
sanitized JSON and deployment/health outcomes in core issues #9 and #72
after testing the actual target; avoid copying secrets or private URLs
into issue comments.

## CI acceptance

`egress-guard-e2e.yml` builds the guarded Compose stack on an ephemeral
Linux runner, waits for kernel policy installation, probes the actual
downloader namespace (not just the sidecar) for blocked private, metadata,
loopback and IPv6 destinations. It also checks that an actual HTTP redirect\nfrom the local app points at a prohibited IP and that following it fails\ninside the guarded downloader namespace, then removes containers and volumes.

This is the first concrete deployment boundary for issues #9 and #72;
keep them open until platform and production acceptance are complete.

## Production readiness decision — Core #9 and #72

The repository/CI acceptance covers kernel destination denial for private and
metadata IPv4, controlled HTTP redirects, simulated DNS rebinding, and
negative IPv6 attempts in the **disposable Linux runner**. The guard applies
to all downloader engines because they share the same network namespace; this
is not proof of individual engine behavior on arbitrary lawful public sites.
A failed IPv6 socket without a route is not a verified firewall packet hit.

The following are **owner-target gates, not CI achievements**:

1. On the actual Docker Desktop/WSL/Linux engine, start the opted-in guarded
   stack, record the guard/downloader health, and run the read-only
   `scripts/guarded_host_acceptance.py` probe with the real Compose overlay.
   Require the sanitized JSON and a clear PASS; do not include tokens or URLs.
2. From that same running stack, submit a legally authorized public media URL
   through the authenticated gateway and verify that a completed output
   arrives in the authorized user's Nextcloud Files with matching bytes.
   Confirm that the publicly routable WebDAV origin, DNS, TLS and hairpin
   routing work without granting blanket private-network exceptions.
3. Verify the actual host's reverse proxy/TLS and administrator-only access,
   plus host-level IPv6 behavior. If the host has no usable IPv6 route, mark
   IPv6 runtime enforcement *unverified*, not passed by a failed socket.
4. Verify the process/Compose config still drops downloader privileges after
   restart, and test an intentional private destination/redirect using only
   reserved targets, never live cloud metadata services.

Until those gates are signed off on the target, **Core #9 and #72 remain open;
untrusted production URL submission is not approved.**
