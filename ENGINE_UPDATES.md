# Pinned engine update automation

The weekly GitHub Actions workflow `.github/workflows/pinned-engine-updates.yml`
checks the public PyPI JSON API for bundled Python engines and queries the
Debian trixie package index for aria2 and curl inside the matching runtime
base-image family. It never runs in the downloader service at runtime.

## Tracked engines

| CLI engine | Release source | Update mode |
| --- | --- | --- |
| yt-dlp | yt-dlp | Candidate PR + automated gate |
| gallery-dl | gallery-dl | Candidate PR + automated gate |
| you-get | you-get | Candidate PR + automated gate |
| svtplay-dl | svtplay-dl | Candidate PR is blocked until a real site-specific E2E smoke is added |
| aria2c | Debian trixie package | Exact apt pin + candidate PR, Docker/Nextcloud WebDAV gate |
| generic / curl | Debian trixie package | Initialize an exact curl apt pin, then gated candidate updates |
| get-sauce | Pinned Go source tag | Weekly release detection and smoke-blocked candidate PR; no unattended merge until live site-specific acceptance |

`get-sauce` is compiled from the pinned Go module tag in the multi-stage Docker
build. `.github/workflows/get-sauce-release-watch.yml` checks GitHub's
published stable releases weekly (and manually after it reaches `main`).
`scripts/get_sauce_updates.py` refuses malformed or prerelease versions,
requires exactly one pinned module tag and the upstream release checksum
manifest, and prepares a Dockerfile-only candidate when a newer version
exists. It opens a candidate PR and an `engine-update-blocked` issue.

**This is discovery and candidate preparation only.** Release asset checksums
are not Go module installation hashes, and their presence is not verification
of the compiled binary. The normal Go module checksum database supplies Go
module integrity checks when the toolchain builds, but there is no currently
accepted live get-sauce site-specific smoke fixture. The watcher must never
merge or publish a candidate based solely on the other engines' tests.
Likewise, a GitHub-token-created PR does not reliably trigger separate PR CI.
Full automated get-sauce upgrades remain blocked until that smoke and the
complete candidate gate have been implemented and demonstrated.

Debian candidates use `apt-cache policy` inside the exact trixie runtime
distribution rather than the GitHub runner's Ubuntu apt index. Version
comparisons use `dpkg --compare-versions`, including Debian revisions and
security updates. System candidates receive the same Python, Docker and
real disposable-Nextcloud WebDAV gate as Python candidates. A failed gate
does not merge or release any pin changes. This is not a Debian snapshot
lock or multi-architecture validation.

The detector ignores prerelease and yanked releases and requires an upstream
artifact SHA-256 digest. Python dependency versions are updated as exact
`package==version` pins. The digest is recorded in the report for traceability;
**pip is not yet configured with a complete hash-locked dependency graph**.
Do not describe this as checksum-enforced installation.

## Workflow behavior

1. Runs weekly and can be dispatched manually after the workflow reaches the default branch.
2. Checks PyPI with a bounded network timeout, writes `engine-update-report.json`,
   then queries Debian's aria2/curl candidates in `python:3.13-slim-trixie` and
   records `debian-engine-update-report.json`. The new candidate changes exact
   `requirements.txt` and/or `Dockerfile` pins only if an engine changed.
3. Fails closed if a changed engine lacks a real smoke fixture in the
   disposable Nextcloud test gate. Currently yt-dlp, gallery-dl and you-get
   are covered by the disposable Nextcloud E2E loop with remote SHA-256 verification;
   updates involving svtplay-dl remain blocked until that engine has equivalent
   live acceptance. The allowlist is kept in `scripts/engine_smoke_coverage.py`
   and checked against the E2E test by CI.
4. On the candidate commit, runs Python compilation and unit/fixture tests,
   builds the production Docker image, then uses the production Compose
   downloader alongside disposable Nextcloud and a generated legal media fixture.
5. The E2E test verifies yt-dlp, aria2c, gallery-dl, generic/curl, and
   you-get downloads and fetches WebDAV files back to compare SHA-256 and size.
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

## Manual P2 site acceptance (not automatic upgrade approval)

`.github/workflows/manual-p2-site-webdav.yml` is an opt-in workflow with a
choice of `svtplay-dl` or `get-sauce` and one **public, authorized, non-secret**
source URL. It rejects URLs with queries, fragments or credentials, and runs
only when manually dispatched on the default branch. GitHub workflow inputs
are visible in the run configuration; do not supply session tokens, cookies,
private downloads or copyrighted material without permission.

The workflow starts the production downloader Compose image beside a
disposable Nextcloud instance. After the engine finishes, it loads the
durable completed-download manifest inside the downloader container,
checks each local output SHA-256, validates recognized media signatures/
archive contents or uses ffprobe for common audio/video formats, and reads
each delivered file back from Nextcloud in bounded chunks. Every remote
file must have the same relative path, size and SHA-256 as the corresponding
completed local file. A partial or missing download is a failure. Neither
the production Nextcloud instance nor its credentials are used.

A green manual test is evidence only for the chosen engine, source, pinned
version and CI environment. **Neither P2 engine is automatically approved**
for unattended version updates until stable, legal, repeatable site-specific
fixtures have been selected and incorporated into a required candidate gate.
Creating this workflow does not by itself close those acceptance requirements.

## Testing and current limitations

- `python -m pytest -q tests/test_engine_updates.py` validates offline
  version selection, yanked-release filtering, pin updates, and workflow structure.
- Both scheduled engine-update workflows require installation on the default branch before they can run. The get-sauce watcher has only offline parser and workflow tests while in the core review branch.
- The workflow's live red and green branches still require a real dispatch
  after its installation on the default branch. A YAML parse test is not a
  substitute for that acceptance.
- The Debian candidate track is wired into the existing test/merge gate but its
  scheduled red-issue/green-release paths have not been demonstrated on main.
  The base image tag, OS dependencies and transitive Python dependencies are not
  fully digest/hash-locked. The trixie apt policy is checked on CI's architecture;
  this does not validate every architecture or guarantee that a package remains
  available forever. `ffmpeg` is still not pinned and is outside the engine list.
- A release is **not** ready until Go smoke/automation, full hash locking,
  and the red-issue/green-merge demonstrations are completed.
- The disposable WebDAV fixture is not a guarantee that every upstream
  extractor website still works; site-specific smoke targets must be added
  for full engine parity.

## Documentation

- PyPI JSON API: https://docs.pypi.org/api/json/
- GitHub Actions token behavior: https://docs.github.com/en/actions/concepts/security/github_token
- GitHub CLI pull-request merge: https://cli.github.com/manual/gh_pr_merge
