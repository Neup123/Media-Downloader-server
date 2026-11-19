"""Regenerate the standalone preview TypeScript contract from Core Pydantic response models."""
from __future__ import annotations

import argparse
import types
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel

from app.schemas import (
    PreviewMetadataOut, PreviewEntryOut, PreviewFormatOut, MediaPreviewOut,
    TorrentManifestFileOut, TorrentManifestOut,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "web/src/preview-contract.generated.ts"
MODELS = (
    PreviewMetadataOut, PreviewEntryOut, PreviewFormatOut, MediaPreviewOut,
    TorrentManifestFileOut, TorrentManifestOut,
)


def ts_type(annotation: object) -> str:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        members = tuple(dict.fromkeys(ts_type(x) for x in get_args(annotation)))
        return " | ".join(members)
    if origin is Literal:
        return " | ".join(repr(x) for x in get_args(annotation))
    if origin is list:
        return f"{ts_type(get_args(annotation)[0])}[]"
    if annotation is str:
        return "string"
    if annotation in (int, float):
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is type(None):
        return "null"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    raise TypeError(f"Unsupported response annotation: {annotation!r}")


def render() -> str:
    lines = [
        "// GENERATED from app/schemas.py by scripts/generate_media_preview_ts.py.",
        "// Do not edit by hand; run python -m scripts.generate_media_preview_ts.",
        "",
    ]
    for model in MODELS:
        lines.append(f"export interface {model.__name__} {{")
        for key, field in model.model_fields.items():
            suffix = "" if field.is_required() else "?"
            lines.append(f"  {key}{suffix}: {ts_type(field.annotation)};")
        lines.extend(["}", ""])
    return "\n".join(lines)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--check", action="store_true")
    args = cli.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("Media-preview UI contract drifted; regenerate and commit")
    else:
        OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
