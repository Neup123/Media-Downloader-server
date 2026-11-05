# Pinned engine update automation

The weekly GitHub Actions workflow `.github/workflows/pinned-engine-updates.yml`
checks the public PyPI JSON API for newer stable releases of bundled Python
download engines. It never runs in the downloader service at runtime.

## Tracked engines

| CLI engine | Python distribution | Update mode |
| --- | --- | --- |
| yt-dlp | yt-dlp | Candidate PR + automated gate |
| gallery-dl | gallery-dl | Candidate PR + automated gate |
| you-get | you-get | Candidate PR + automated gate |
| svtplay-dl (once bundled) | svtplay-dl | Candidate PR + automated gate |
| aria2c | Debian system package | Not yet automated; separate update track |
| generic / curl | Debian system package | Not yet automated; separate update track |

`get-sauce` is not bundled yet; its version source will require a separate
Go release build and checksum-verification track.

The detector ignores prerelease and yanked releases and requires an upstream
artifact SHA-256 digest. Python dependency versions are updated as exact
`package==version` pins. The digest is recorded in the report for traceability;
**pip is not yet configured with a complete hash-locked dependency graph**.
Do not describe this as checksum-enforced installation.

## Workflow behavior

1. Runs weekly and can be dispatched manually after the workflow reaches the default branch.
2. Checks PyPI with a bounded network timeout, writes `engine-update-report.json`,
   and creates a unique candidate branch and PR only if a bundled engine changed.
3. Fails closed if a changed engine lacks a real smoke fixture in the
   disposable Nextcloud test gate. Currently only yt-dlp and gallery-dl
   are covered; updates involving you-get or svtplay-dl remain blocked until
   those engines have equivalent live acceptance.
4. On the candidate commit, runs Python compilation and unit/fixture tests,
   builds the production Docker image, then uses the production Compose
   downloader alongside disposable Nextcloud and a generated legal media fixture.
5. The E2E test verifies downloads from all P0 engines and fetches WebDAV files
   back to compare their SHA-256 hashes and sizes.
6. On a green gate, attempts a normal squash merge and patch release. The normal
   GitHub branch protection, permissions, and merge rules still apply.
7. On any failed gate, leaves the candidate PR unmerged and opens/updates a
   labelled `engine-update-blocked` issue with versions, workflow link, and
   a diagnostic excerpt. Artifacts are retained for 30 days.

All work in a run is serialized by a workflow-level concurrency group. Candidate
PRs created with GitHub's built-in `GITHUB_TOKEN` do not provide a reliable
automatic secondary CI trigger; acceptance runs **inside the same workflow**
against the candidate checkout before merging.

The workflow needs repository Actions permission to write contents, PRs, and
issues. Where an organization restricts these permissions or requires manual
PR approval, GitHub is allowed to block the merge; the candidate remains
unmerged and an issue records the blocker.

## Testing and current limitations

- `python -m pytest -q tests/test_engine_updates.py` validates offline
  version selection, yanked-release filtering, pin updates, and workflow structure.
- The workflow's live red and green branches still require a real dispatch
  after its installation on the default branch. A YAML parse test is not a
  substitute for that acceptance.
- A release is **not** ready until the apt/Go engine update tracks, hash locking,
  and the red-issue/green-merge demonstrations are completed.
- The disposable WebDAV fixture is not a guarantee that every upstream
  extractor website still works; site-specific smoke targets must be added
  for full engine parity.

## Documentation

- PyPI JSON API: https://docs.pypi.org/api/json/
- GitHub Actions token behavior: https://docs.github.com/en/actions/concepts/security/github_token
- GitHub CLI pull-request merge: https://cli.github.com/manual/gh_pr_merge
