"""Explicit restart decisions for complete, interrupted and empty workspaces."""
from __future__ import annotations

import json

from app.completion import (
    WorkspaceRecoveryState, inspect_workspace_recovery, save_completed_files,
)


def test_empty_workspace_does_not_request_cleanup(tmp_path):
    workdir = tmp_path / "empty"
    workdir.mkdir()
    result = inspect_workspace_recovery(workdir)
    assert result.state == WorkspaceRecoveryState.EMPTY
    assert result.files == ()
    assert list(workdir.iterdir()) == []


def test_verified_completion_reuses_all_exact_manifest_outputs(tmp_path):
    workdir = tmp_path / "verified"
    workdir.mkdir()
    first = workdir / "one.bin"
    second = workdir / "two.bin"
    first.write_bytes(b"good-one")
    second.write_bytes(b"good-two")
    save_completed_files(workdir, [first, second])
    result = inspect_workspace_recovery(workdir)
    assert result.state == WorkspaceRecoveryState.VERIFIED_COMPLETION
    assert set(result.files) == {first, second}
    assert first.read_bytes() == b"good-one"


def test_missing_invalid_or_tampered_manifest_is_never_reused(tmp_path):
    for scenario in ("missing-manifest", "broken-json", "changed-bytes", "missing-file"):
        workdir = tmp_path / scenario
        workdir.mkdir()
        path = workdir / "sample.bin"
        path.write_bytes(b"example-media")
        if scenario in {"changed-bytes", "missing-file"}:
            save_completed_files(workdir, [path])
            if scenario == "changed-bytes":
                # Preserve file length and mtime to prove SHA-256 is checked.
                stat = path.stat()
                path.write_bytes(b"replaced-medi")
                import os
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            else:
                path.unlink()
        elif scenario == "broken-json":
            (workdir / ".download-complete.json").write_text("{bad")
        result = inspect_workspace_recovery(workdir)
        assert result.state == WorkspaceRecoveryState.DISCARD_UNVERIFIED, scenario
        assert result.files == ()
        # Inspection is read-only: worker owns cleanup *after* DB reconciliation.
        assert workdir.exists()


def test_recovery_decision_does_not_promote_bare_completed_looking_media(tmp_path):
    workdir = tmp_path / "unverified"
    workdir.mkdir()
    (workdir / "completed.mp4").write_bytes(b"looks-valid-but-truncated")
    result = inspect_workspace_recovery(workdir)
    assert result.state == WorkspaceRecoveryState.DISCARD_UNVERIFIED
    assert not result.files
