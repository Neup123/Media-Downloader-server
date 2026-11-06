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

## Promotion to the default branch

The workflows can run from `main` for candidate discovery and acceptance, but **green
update PRs are not merged and releases are not published by default**. The merge
and release steps additionally require the repository Actions variable
`DOWNLOADER_AUTOMATED_RELEASES_APPROVED` to equal the literal `true`. An absent
variable fails closed. Do not set this approval flag until the red-gate/green-gate
release scenarios, necessary real-site engine acceptance and deployment-security
review have been demonstrated. A green candidate may still be reviewed and merged
manually after independent approval. This makes source promotion to `main` a
*development milestone*, not a production-release declaration.

## Safe GitHub Actions red/green rehearsal

The weekly `pinned-engine-updates.yml` workflow also exposes a **manual-only**
`rehearsal_only` checkbox. When enabled, it reuses the complete candidate
eligibility, Python/CLI tests, production Docker build and real disposable
Nextcloud/WebDAV SHA-256 acceptance, while explicitly **skipping** candidate
PR creation, production merge, release creation and blocked-issue creation.

Its synthetic update reports represent a deliberately older placeholder
version being replaced by the **already pinned current** yt-dlp and aria2
versions. The yt-dlp artifact digest is retrieved from the exact official
PyPI release metadata, and the aria2 version comes from the checked-in
Dockerfile. There is no package bump. The green path checks that the
PyPI/Debian combined candidate passes policy; the red paths prove that
an unsupported svtplay-dl update and a duplicate candidate are rejected.

The ordinary `live-nextcloud-e2e.yml` workflow now runs this candidate
rehearsal before starting the downloader/Nextcloud containers and archives
its non-secret JSON evidence. Its subsequent real download, restart,
re-delivery and browser checks still exercise only currently pinned engines.

**Interpretation:** A green rehearsal shows the policy and existing
known-good engine binaries pass the actual GitHub Actions acceptance
pipeline; it is *not* a green real-version-upgrade candidate, an
end-to-end P2 site test or a production release demonstration. The
repository variable `DOWNLOADER_AUTOMATED_RELEASES_APPROVED` remains
untouched and the real target Nextcloud remains unverified.

## Combined candidate eligibility and exact release provenance

The pinned PyPI and Debian tracks now use
`python -m scripts.engine_candidate_gate` after detecting their changes
but **before** installing, building or launching candidate containers.
The validator fails closed on unknown/duplicated engines, missing PyPI
artifact digests, unexpected Debian package mappings, unchanged/invalid
pins, and engines without their own legal WebDAV fixture coverage.
The resulting `engine-promotion-report.json` is retained in the
candidate diagnostics artifact. This is an **eligibility record**, not a
claim that the PyPI artifact hash was enforced during installation.

The automated promotion step is still disabled unless the repository
variable `DOWNLOADER_AUTOMATED_RELEASES_APPROVED` is set to `true`
after the remaining production gates are explicitly approved. When
enabled, the workflow additionally:

1. Verifies the candidate PR head is the exact commit whose Python,
   Docker and real disposable-Nextcloud SHA-256 tests passed.
2. Squash-merges only that candidate, requiring the resulting immutable
   merge SHA to equal the current remote `main` tip.
3. Rechecks the remote `main` immediately before publication, and
   creates the patch release with an **exact commit SHA** target rather
   than the mutable name `main`. A race or incomplete merge fails
   closed without publishing a release for a different commit.

`tests/test_engine_candidate_gate.py` exercises deterministic red and
green PyPI/Debian cases, missing or malformed provenance, duplicates,
unverified P2 updates and a CLI run that produces no acceptance artifact
on a red candidate. A **real live GitHub Actions release red/green
demonstration remains open**. No automated release has been enabled by
these offline simulations, and real P2 source acceptance and host-level
network security require separate approval.

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

After remote integrity is proved, the manual acceptance now restarts the
actual downloader Docker Compose service without deleting its volume.
It verifies that the same P2 job, local SHA-256 manifest, execution record
and encrypted WebDAV connection remain available, then re-delivers the job
from its existing workspace and performs full remote streaming SHA-256
readback again. Source media is not re-downloaded during the re-delivery.

Only when **all** checks pass, the manual job publishes a 30-day
`legal-p2-site-acceptance` evidence artifact. Its JSON includes the exact
core commit SHA, selected P2 engine, number of verified files, total byte
count and a deterministic SHA-256 digest of the completed file manifest,
plus restart/re-delivery pass flags. It intentionally excludes the source
URL, downloaded filenames, credentials and extractor logs. The artifact
does not certify a production Nextcloud target, approve P2 auto-upgrades or
make the source content itself publicly available.

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
