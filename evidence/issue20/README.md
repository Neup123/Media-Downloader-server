# Issue #20 demonstration evidence — 2026-11-09

These files are downloaded copies of the `engine-update-diagnostics` artifacts,
with no edits to their contents. The source SHA in each directory identifies
the tested branch code. The published candidate patch is empty because all
three covered Python pins already matched the newest stable PyPI versions.
The red patch is an intentionally invalid, disposable candidate, not a proposed
runtime requirements change. Do not apply it.

| Run | Result | Exact tested source |
| --- | --- | --- |
| [Published](https://github.com/Neup123/Media-Downloader-server/actions/runs/42636595881) | Success: policy, 496 tests, Docker, five-engine WebDAV SHA-256, restart and re-delivery | `1423f4c9d5eb3ad81222d1deb22db631fee5e791` |
| [Broken](https://github.com/Neup123/Media-Downloader-server/actions/runs/42636356980) | Expected failure: bogus yt-dlp `9999.0.0` without upstream artifact provenance; [blocked issue #150](https://github.com/Neup123/Media-Downloader-server/issues/150) created | `53fcd2435bd638907a3c8d3dcfeb84180bc5e7fe` |

The initial published attempt [42636351613](https://github.com/Neup123/Media-Downloader-server/actions/runs/42636351613)
failed before tests could run because the hosted runner could not create `/data`.
It produced [blocked record #149](https://github.com/Neup123/Media-Downloader-server/issues/149).
The corrected published run uses a runner temporary test data directory.

Official metadata checked yt-dlp `2026.10.26`, gallery-dl `1.32.19`, and you-get
`0.4.1743`; all equalled their checked-in pins. Therefore this is the task's
newest-published fallback, not a real version bump. JSON files contain the
upstream artifact digests. They are metadata provenance, not pip hash-locks.

Both modes skipped candidate PR creation, merge and release. Production
promotion remains undemonstrated and disabled. The release approval variable
was not changed, no PR was merged, and no release was created. Issue #20 remains
open; Core #9/#72 and #63 remain separate gates. Aria2's already-implemented apt
track was audited without changes; aria2's current binary passed the shared
WebDAV loop, but no new Debian candidate or Debian release was demonstrated.

The complete acceptance-bullet audit is in [PR #151](https://github.com/Neup123/Media-Downloader-server/pull/151).
