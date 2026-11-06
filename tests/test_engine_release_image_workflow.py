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
