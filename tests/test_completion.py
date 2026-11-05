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
