# Administrator-provided cookie fallback

Core issue #157 implements the owner's clarified precedence: a user's explicitly selected or site-matched personal cookie wins. When none is selected, Core can use a provisioned cookie for the exact source hostname. The personal vault stays visible and unchanged; ordinary users do not receive the administrator's cookie filename or content.

## Administration

Use the trusted Core API (local Scalar API reference, or server-side tooling with X-API-Key):

- GET /api/v1/provided-cookies lists provisioned hostnames and storage state only.
- PUT /api/v1/provided-cookies/{hostname} accepts the base64 Netscape cookie export in the documented request body.
- DELETE /api/v1/provided-cookies/{hostname} removes that hostname's provision.

Enter an exact DNS hostname, not a URL, wildcard or IP address. Different subdomains need explicit provisioning. Only owner-scoped jobs using yt-dlp, gallery-dl or you-get are eligible. Explicit gallery item selection continues to require unauthenticated identity. Cookie-jar domain/path rules still apply; provisioning does not guarantee authentication.

Cookie exports contain account credentials. Use only accounts and sites you are authorized to share with entitled users, consider provider terms, rate limits and account suspension risk, and prefer dedicated least-privilege accounts. Never paste exports into issues, logs or browser code. Keep APP_ENCRYPTION_KEY stable, protect the data volume and use authenticated TLS.

The separate data/provided-cookies directory is created on first upload; no database migration is required. Storage is atomic and encrypted with Fernet. Legacy personal plaintext exports are still supported until re-uploaded; undecryptable encrypted exports fail closed. The existing job-scoped temporary plaintext copy is removed when the engine finishes.

Revocation affects subsequent engine executions. An engine already running may hold a temporary cookie copy or active session; deleting the provision does not invalidate that session at the remote provider. Cancel the active job and revoke the upstream account session if immediate revocation is required.

## Acceptance and limits

Run python -m pytest tests/test_provided_cookie_storage.py tests/test_provided_cookie_api.py -q along with the normal Core CI. These tests exercise encryption, authenticated administration, owner precedence, exact hostname selection and subsequent-execution revocation. They do not demonstrate live authenticated provider downloads, active-session revocation, native Nextcloud administration UI, authentication testing/automatic invalidation, or Telegram credentials. Those remain separate acceptance items. A stored provision is not a verified working login.
