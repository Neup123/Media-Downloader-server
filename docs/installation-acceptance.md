# Standalone installation, upgrade and acceptance

Use this guide for the **Core** service. Installing the Nextcloud plugin is a separate process covered by the [Nextcloud per-user guide](https://github.com/Neup123/downloader-server-nextcloud/blob/main/NEXTCLOUD_PER_USER_INSTALL.md).

## 1. Select the environment and safety boundary

The standard Compose file is a trusted local installation and binds HTTP to `127.0.0.1:8080`. It is not a public download endpoint. Confirm host operating system, Docker/Compose support, durable storage, authenticated TLS reverse proxy (if remote access is intended), and isolation of the aria2 RPC interface.

For untrusted submissions, **stop before deployment** until the [guarded Linux egress deployment](../EGRESS_GUARD.md) has been proved on that exact host. The Linux network guard and negative egress tests are not equivalent to accepting Docker Desktop or a generic reverse proxy.

## 2. Prepare credentials and persistent storage

1. Copy `.env.example` to `.env` outside source control.
2. Generate a long random `APP_API_TOKEN` and keep the value only in the authorized secret store. Do not put it in browser bundles, documentation or issues.
3. For WebDAV use, generate `APP_ENCRYPTION_KEY` once. Preserve the identical key through restarts, data migrations, backups and rollbacks; losing it makes encrypted per-user delivery snapshots unusable.
4. Configure local data and expected output/delivery host settings from `.env.example`. Private WebDAV and insecure HTTP WebDAV allowances are independent opt-ins, not default internet-wide permissions.

Do not upload `.env` or copied secrets to GitHub.

## 3. Start the trusted standalone service

```sh
docker compose up --build -d
docker compose ps
```

Open `http://127.0.0.1:8080` on the trusted host. Authenticate as administrator and perform a disposable **authorized** test download. The local API reference is `/api-reference`; `X-API-Key` is the expected header for protected API endpoints.

Run local source tests when changing code:

```sh
python -m compileall -q app tests
python -m pytest -q
```

Tests are necessary but not sufficient. Verify the actual output file and destination readback.

## 4. Functional acceptance checklist

- [ ] A valid public fixture link is accepted; an invalid or private link is rejected according to the configured safety boundary
- [ ] Metadata preview (single item and playlist) displays only real extractor metadata; missing values remain unknown
- [ ] Submission reports **queued**, not delivered; active progress and cancellation are reflected in the Library
- [ ] Successful local delivery leaves readable file bytes on persistent storage after a service restart
- [ ] A WebDAV test destination creates, writes, reads back, compares content and cleans up its temporary object
- [ ] A deliberately failed destination leaves a failed job with actionable non-secret diagnostic detail; a retry or verified re-delivery behaves as documented
- [ ] A reboot during an active job does not turn incomplete files into falsely completed files
- [ ] The real host rejects private/metadata IP egress by **every** engine, including re-resolved and redirected destinations
- [ ] Legal, stable P2-site smoke URLs and actual readback evidence exist for any engines being approved for production
- [ ] Default-branch required checks and the target deployment's Nextcloud/AppAPI connectivity have been verified independently

Only mark an item passed with an identifiable screenshot, log artifact, API response, actual file-hash comparison or test report. The current [acceptance ledger](../ACCEPTANCE_STATUS.md) tracks outstanding gates; this checklist is **not** a declaration that they have passed.

## 5. Upgrade and rollback

1. Identify the exact image tag/source commit and review [engine update acceptance](../ENGINE_UPDATES.md) plus all schema migrations.
2. Create a verified backup of the persistent `/data` volume and preserve the stable encryption key and operating secrets through an approved secret-management procedure.
3. Exercise the candidate image against disposable destination readback and the host-specific egress negative test. Confirm no owner-scoped permission regressions before an integrated release.
4. Update the image only after acceptance. Observe queue recovery, file verification, engine discovery and WebDAV delivery in the running environment.
5. If rollback is required, restore a compatible image **and** database/volume state under the documented migration procedure. Never blindly downgrade a migrated database without a compatible rollback plan.

Pinned candidate PRs, GHCR image builds and disposable CI can demonstrate development-level acceptance. They are not evidence of readiness on the real host, nor permission to auto-merge an unverified engine update. See the [release workflow guide](../ENGINE_UPDATES.md).

## 6. Nextcloud integration is separate

The Core UI and queue stay in this repository. The optional Nextcloud plugin has its own native Files, Dashboard, Library and settings surfaces under [downloader-server-nextcloud](https://github.com/Neup123/downloader-server-nextcloud). Its privileged AppAPI/ExApp gateway calls the Core API server-side and owns per-user entitlement and encrypted WebDAV identity. Never embed the standalone Core UI in that plugin, or add a Core token field to a regular Nextcloud user's UI.
