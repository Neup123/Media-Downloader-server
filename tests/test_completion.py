from pathlib import Path

import pytest

from app.completion import load_completed_files, save_completed_files


def test_incomplete_file_does_not_count_as_a_finished_download(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"incomplete but normal filename")
    assert load_completed_files(tmp_path) == []


def test_valid_manifest_reuses_files_after_success(tmp_path: Path):
    output = tmp_path / "album" / "video.mp4"
    output.parent.mkdir()
    output.write_bytes(b"finished file")
    save_completed_files(tmp_path, [output])
    assert load_completed_files(tmp_path) == [output]


def test_modified_output_is_not_reused(tmp_path: Path):
    output = tmp_path / "image.jpg"
    output.write_bytes(b"complete")
    save_completed_files(tmp_path, [output])
    output.write_bytes(b"changed")
    assert load_completed_files(tmp_path) == []


def test_manifest_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "secret.dat"
    outside.write_bytes(b"not a job output")
    link = tmp_path / "escape.dat"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="inside its job directory"):
        save_completed_files(tmp_path, [link])


def test_corrupt_manifest_is_not_trusted(tmp_path: Path):
    (tmp_path / ".download-complete.json").write_text("[broken", encoding="utf-8")
    assert load_completed_files(tmp_path) == []


def test_new_manifests_include_sha256_digest(tmp_path: Path):
    import hashlib
    import json

    output = tmp_path / "movie.mp4"
    payload = b"the original downloaded bytes"
    output.write_bytes(payload)
    save_completed_files(tmp_path, [output])
    manifest = json.loads((tmp_path / ".download-complete.json").read_text())
    assert manifest[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert load_completed_files(tmp_path) == [output]


def test_same_size_modified_file_cannot_be_reused_even_with_original_mtime(tmp_path: Path):
    import os

    output = tmp_path / "video.mp4"
    output.write_bytes(b"legitimate")
    save_completed_files(tmp_path, [output])
    original = output.stat()
    output.write_bytes(b"counterft")
    assert output.stat().st_size == original.st_size
    os.utime(output, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert output.stat().st_mtime_ns == original.st_mtime_ns
    assert load_completed_files(tmp_path) == []


def test_malformed_or_wrong_digest_rejects_manifest(tmp_path: Path):
    import json

    output = tmp_path / "archive.bin"
    output.write_bytes(b"complete data")
    save_completed_files(tmp_path, [output])
    manifest = tmp_path / ".download-complete.json"
    entries = json.loads(manifest.read_text())
    for bad_digest in ("not-a-sha256", None, 123, "0" * 64):
        entries[0]["sha256"] = bad_digest
        manifest.write_text(json.dumps(entries))
        # null behaves like the old manifest; the legacy compatible format
        # is allowed, but malformed non-null values and bad hashes fail.
        if bad_digest is None:
            assert load_completed_files(tmp_path) == [output]
        else:
            assert load_completed_files(tmp_path) == []


def test_legacy_size_and_timestamp_manifest_remains_usable(tmp_path: Path):
    import json

    output = tmp_path / "old-video.mp4"
    output.write_bytes(b"old persisted download")
    source = output.stat()
    (tmp_path / ".download-complete.json").write_text(json.dumps([{
        "path": output.name, "size_bytes": source.st_size, "mtime_ns": source.st_mtime_ns
    }]))
    assert load_completed_files(tmp_path) == [output]
