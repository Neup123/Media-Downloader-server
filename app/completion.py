"""Durable proof that an engine finished before a job can reuse its output."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


MANIFEST_FILENAME = ".download-complete.json"


def load_completed_files(workdir: Path) -> list[Path]:
    """Return verified completed outputs, or an empty list for incomplete state.

    The presence of arbitrary files is never evidence that an engine finished:
    an interrupted writer may have left a normal-looking but truncated file.
    """
    workdir = workdir.resolve()
    manifest = workdir / MANIFEST_FILENAME
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(entries, list) or not entries:
            return []
        files = []
        seen = set()
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return []
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.name == MANIFEST_FILENAME:
                return []
            candidate = workdir / relative
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(workdir) or not resolved.is_file() or candidate.is_symlink():
                return []
            if resolved in seen:
                return []
            stat = resolved.stat()
            if stat.st_size != item.get("size_bytes") or stat.st_mtime_ns != item.get("mtime_ns"):
                return []
            seen.add(resolved)
            files.append(resolved)
        return files
    except (OSError, ValueError, TypeError, RuntimeError):
        return []


def save_completed_files(workdir: Path, files: list[Path]) -> None:
    """Publish a completion manifest atomically, only after engine success."""
    workdir = workdir.resolve()
    if not files:
        raise ValueError("An engine cannot complete a job without output files")
    entries = []
    seen = set()
    for path in files:
        source = path.resolve(strict=True)
        if not source.is_relative_to(workdir) or not source.is_file() or path.is_symlink():
            raise ValueError("Engine output must be a regular file inside its job directory")
        relative = source.relative_to(workdir)
        if relative.name == MANIFEST_FILENAME or source in seen:
            raise ValueError("Invalid or duplicated engine output file")
        seen.add(source)
        stat = source.stat()
        entries.append({"path": relative.as_posix(), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    temp = workdir / f".download-complete-{uuid4().hex}.tmp"
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(entries, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, workdir / MANIFEST_FILENAME)
    finally:
        temp.unlink(missing_ok=True)
