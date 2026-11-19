"""Preview and torrent success contracts across Python, checked-in OpenAPI and React."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.exceptions import ResponseValidationError
from pydantic import ValidationError

from app.discovery import summarize_metadata
from app.schemas import MediaPreviewOut, TorrentManifestOut
from scripts.generate_media_preview_ts import OUTPUT, render


ROOT = Path(__file__).resolve().parents[1]


def test_generated_ui_type_matches_pydantic_models_and_replaces_parallel_hand_type():
    assert OUTPUT.read_text(encoding="utf-8") == render()
    component = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    assert "type MediaPreview = MediaPreviewOut" in component
    assert "type MediaPreview = {" not in component


def test_pydantic_preview_contract_accepts_nulls_and_nested_entries():
    sample = summarize_metadata({
        "title": "Album", "playlist_count": 2,
        "entries": [
            {"title": "First", "id": "1", "duration": None, "thumbnail": None},
            {"title": "Second", "id": "2", "is_live": False},
        ],
    }, 5)
    valid = MediaPreviewOut.model_validate(sample)
    assert valid.kind == "playlist" and valid.entry_count == 2
    assert valid.entries[0].thumbnail is None and valid.entries[0].duration is None
    assert valid.entries[1].is_live is False
    assert valid.next_offset is None
    assert valid.model_dump(exclude_unset=True) == sample


@pytest.mark.parametrize("patch", [
    {"kind": "unknown"},
    {"entries": [{"index": 1, "title": "missing id"}]},
    {"entries": [{"index": -1, "id": "x", "title": "bad", "duration": None}]},
    {"entries": "not an array"},
    {"has_more": "not true or false"},
    {"not_a_documented_field": "should fail closed"},
    {"thumbnail": 7},
])
def test_pydantic_preview_success_contract_rejects_incompatible_drift(patch):
    sample = summarize_metadata({"title": "A", "entries": [{"id":"a","title":"one"}]}, 5)
    sample.update(patch)
    with pytest.raises(ValidationError):
        MediaPreviewOut.model_validate(sample)


def test_torrent_success_contract_rejects_wrong_count_and_entry_shape():
    accepted = {
        "file_count": 2, "files": [
            {"index": 1, "path": "A/first.mp4", "name": "first.mp4", "size_bytes": 4},
            {"index": 2, "path": "B/second.mkv", "name": "second.mkv", "size_bytes": 0},
        ]
    }
    assert TorrentManifestOut.model_validate(accepted).model_dump() == accepted
    for invalid in (
        {"file_count": 1, "files": accepted["files"]},
        {"file_count": 2, "files": accepted["files"][:-1]},
        {"file_count": 2, "files": [{**accepted["files"][0], "size_bytes": -1}, accepted["files"][1]]},
        {"file_count": 2, "files": [dict(accepted["files"][0], url="token-carrying URL"), accepted["files"][1]]},
    ):
        with pytest.raises(ValidationError):
            TorrentManifestOut.model_validate(invalid)


def test_preview_and_torrent_success_openapi_schemas_are_explicit():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    pairs = (
        ("/preview", MediaPreviewOut, "MediaPreviewOut"),
        ("/torrent/manifest", TorrentManifestOut, "TorrentManifestOut"),
    )
    for route, model, name in pairs:
        response = spec["paths"][route]["post"]["responses"]["200"]["content"]["application/json"]
        assert response["schema"] == {"$ref": f"#/components/schemas/{name}"}
        documented = spec["components"]["schemas"][name]
        assert set(documented["properties"]) == set(model.model_fields)
        assert set(documented["required"]) == {
            key for key, field in model.model_fields.items() if field.is_required()
        }
        assert documented.get("additionalProperties") is False
    preview = spec["components"]["schemas"]["MediaPreviewOut"]
    assert preview["properties"]["entries"]["items"]["$ref"].endswith("/PreviewEntryOut")
    assert preview["properties"]["formats"]["items"]["$ref"].endswith("/PreviewFormatOut")
    manifest = spec["components"]["schemas"]["TorrentManifestOut"]
    assert manifest["properties"]["files"]["items"]["$ref"].endswith("/TorrentManifestFileOut")
    assert spec["paths"]["/preview"]["post"]["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PreviewFailure"
    }


def test_invalid_previews_fail_api_response_validation_not_silent_passthrough(client, auth_headers, monkeypatch):
    async def invalid_preview(url, *, max_entries, offset=0):
        return {"title": "wrong", "kind": "video", "entries": [], "formats": []}
    monkeypatch.setattr("app.main.inspect_ytdlp", invalid_preview)
    monkeypatch.setattr("app.main.preflight_source_dns", lambda _: None)
    with pytest.raises(ResponseValidationError):
        client.post("/api/v1/preview", headers=auth_headers, json={"url":"https://example.org/video"})


def test_optional_byte_extension_does_not_invent_unknown_size():
    sample = summarize_metadata({"title":"A","entries":[{"id":"a","title":"one"}]}, 10)
    validated = MediaPreviewOut.model_validate(sample).model_dump(exclude_unset=True)
    # The extractor may explicitly report unknown sizes as None; never fabricate bytes.
    assert validated["entries"][0].get("size_bytes") is None
    assert validated["entries"][0].get("size_estimate_bytes") is None
