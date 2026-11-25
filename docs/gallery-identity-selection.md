# Flat gallery identity selection

Explicit gallery-dl metadata preview returns an optional SHA-256 selection_id
derived from the actual extractor URL, without returning that URL. A job can
submit gallery_item_ids (1–200 unique IDs) instead of legacy gallery_items
indices. The installed gallery-dl CLI runs in an isolated child with a narrow
DownloadJob subclass: it matches actual emitted URLs, not position or mutable
metadata, disables fallback, refuses nested queues, scans at most 1100 files,
and fails if a selected URL is missing. Completion still requires the expected
number of output files and ordinary verified delivery. No credentials, proxies,
filters or other ordering-changing options are accepted in this mode.

This is identity of an extractor URL, not an immutable byte snapshot or an
authorization token. Expiring/signed URLs can legitimately change and fail;
preview again rather than silently downloading an unrelated item. Nested
chapters, CBZ, and native gallery integration remain separate. Tests use the
pinned gallery-dl with a loopback HTML gallery and changed source order, never
a public account or media service. No new configuration or dependencies.
