"""Release promotion must publish a pullable, SHA-identifiable GHCR image."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_automatic_engine_release_publishes_image_before_annotated_release():
    text = (ROOT / ".github/workflows/pinned-engine-updates.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"]["packages"] == "write"
    assert workflow["permissions"]["contents"] == "write"

    steps = workflow["jobs"]["candidate-gate"]["steps"]
    promote = next(s for s in steps if s.get("id") == "promote")
    release = next(s for s in steps if s.get("name") == "Cut a patch release from the tested merged main")
    assert "vars.DOWNLOADER_AUTOMATED_RELEASES_APPROVED == 'true'" in promote["if"]
    assert release["if"] == promote["if"]

    script = release["run"]
    required = (
        'git switch --detach "$merged_sha"',
        'test "$(git rev-parse HEAD)" = "$merged_sha"',
        'docker manifest inspect "$IMAGE:$version"',
        'docker build --pull',
        'org.opencontainers.image.revision=$merged_sha',
        'docker login ghcr.io',
        'docker push "$IMAGE:$version"',
        'docker pull "$IMAGE:$version"',
        'docker pull "$digest_ref"',
        'git ls-remote origin refs/heads/main',
        'gh release create "$tag" --target "$merged_sha"',
    )
    for text_snippet in required:
        assert text_snippet in script
    assert script.index('docker pull "$digest_ref"') < script.index('gh release create "$tag"')
    assert script.index('docker manifest inspect "$IMAGE:$version"') < script.index('docker push "$IMAGE:$version"')
    assert "engine-update-release.md" in script
    assert 'Verified OCI image digest:' in script


def test_rehearsal_cannot_publish_engine_image_or_release():
    workflow = yaml.safe_load((ROOT / ".github/workflows/pinned-engine-updates.yml").read_text())
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    release = next(s for s in steps if s.get("name") == "Cut a patch release from the tested merged main")
    assert "inputs.rehearsal_only != true" in release["if"]
    assert "steps.detect.outputs.changed == 'true'" in release["if"]


def test_engine_candidate_acceptance_precedes_permission_gated_pr_creation():
    """A blocked Actions createPullRequest permission must not skip acceptance."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/pinned-engine-updates.yml").read_text())
    steps = workflow["jobs"]["candidate-gate"]["steps"]
    names = [step.get("name") for step in steps]
    patch = names.index("Preserve exact proposed pin patch before acceptance")
    policy = names.index("Require a real smoke test and reviewed source provenance for every updated engine")
    python = names.index("Install pinned dependencies and test requirements")
    image = names.index("Compile the production image with candidate engine versions")
    webdav = names.index("Verify real downloads and delivered SHA-256 hashes in Nextcloud")
    pr = names.index("Open a candidate PR after full acceptance (not a prerequisite to tests)")
    diagnostics = names.index("Collect sanitized diagnostic logs and release manifest")
    assert patch < policy < python < image < webdav < pr < diagnostics
    patch_step = steps[patch]
    assert "inputs.rehearsal_only != true" in patch_step["if"]
    assert "git diff -- requirements.txt Dockerfile > engine-candidate.patch" in patch_step["run"]
    assert "test -s engine-candidate.patch" in patch_step["run"]

    candidate = steps[pr]
    assert "steps.detect.outputs.changed == 'true'" in candidate["if"]
    assert "inputs.rehearsal_only != true" in candidate["if"]
    assert "gh pr create" in candidate["run"]
    assert 'git diff --quiet HEAD -- requirements.txt Dockerfile' in candidate["run"]
    assert "engine-update-pr.log" in candidate["run"]
    # A denied PR still fails the job, retains the tested patch and diagnostics,
    # and must never be eligible for a release through the success() gate.
    artifacts = steps[names.index("Collect sanitized diagnostic logs and release manifest")]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact"))
    promotion = next(step for step in steps if step.get("id") == "promote")
    assert artifacts["if"].startswith("always()")
    assert upload["if"].startswith("always()")
    assert "engine-candidate.patch" in upload["with"]["path"]
    assert "engine-update-diagnostics.txt" in upload["with"]["path"]
    assert "success()" in promotion["if"]
    assert "steps.open_pr.outputs.number" in promotion["run"]
    assert "vars.DOWNLOADER_AUTOMATED_RELEASES_APPROVED == 'true'" in promotion["if"]
