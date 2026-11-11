"""Check the checked-in Scalar contract against Pydantic request models.

OpenAPI 3.1 uses JSON Schema types; this test compares the documented
field inventory, required fields, primitive types and objective constraints.
It intentionally does not claim complete JSON Schema equivalence.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.schemas import (
    CookieUploadIn, DeliverySettingsIn, EngineRuleInput, EngineRuleOrderInput,
    EngineSettingsIn, EnginePolicyIn, TelegramCredentialsIn, CaptchaAnswerIn,
    JobBatchCreate, JobCreate, MediaPreviewIn, TorrentManifestIn, PresetInput,
)


OPERATIONS = {
    ("post", "/preview"): MediaPreviewIn,
    ("post", "/torrent/manifest"): TorrentManifestIn,
    ("put", "/engines/{engine_name}/settings"): EngineSettingsIn,
    ("put", "/engines/{engine_name}/policy"): EnginePolicyIn,
    ("put", "/engines/telegram/credentials"): TelegramCredentialsIn,
    ("post", "/jobs/{job_id}/captcha"): CaptchaAnswerIn,
    ("put", "/cookies/{filename}"): CookieUploadIn,
    ("post", "/presets"): PresetInput,
    ("put", "/presets/{preset_id}"): PresetInput,
    ("post", "/engine-rules"): EngineRuleInput,
    ("put", "/engine-rules/order"): EngineRuleOrderInput,
    ("put", "/engine-rules/{rule_id}"): EngineRuleInput,
    ("post", "/jobs"): JobCreate,
    ("post", "/jobs/batch"): JobBatchCreate,
    ("put", "/delivery"): DeliverySettingsIn,
}
CONSTRAINTS = ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern")


def _schema_types(schema: dict) -> set[str]:
    if "type" in schema:
        t = schema["type"]
        return set(t if isinstance(t, list) else [t])
    if "const" in schema:
        value = schema["const"]
        return {"string" if isinstance(value, str) else "integer"}
    if "anyOf" in schema:
        types = set()
        for option in schema["anyOf"]:
            types |= _schema_types(option)
        return types
    if "$ref" in schema:
        return {"ref"}
    return set()


def _constraint_value(schema: dict, name: str):
    """OpenAPI 3.1 union type arrays and Pydantic anyOf express the same bounds."""
    if name in schema:
        return schema[name]
    if "anyOf" in schema:
        candidates = [
            item[name] for item in schema["anyOf"]
            if isinstance(item, dict) and name in item and item.get("type") != "null"
        ]
        if len(candidates) == 1:
            return candidates[0]
    return None


@pytest.mark.parametrize("method,path", OPERATIONS)
def test_request_schema_matches_actual_pydantic_model(method, path):
    contract = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    model = OPERATIONS[(method, path)]
    operation = contract["paths"][path][method]
    body = operation["requestBody"]
    documented = body["content"]["application/json"]["schema"]
    if "$ref" in documented:
        ref = documented["$ref"]
        assert ref.startswith("#/components/schemas/")
        documented = contract["components"]["schemas"][ref.split("/")[-1]]
    assert body["required"] is True

    model_schema = model.model_json_schema()
    documented_fields = documented["properties"]
    model_fields = model_schema["properties"]
    assert set(documented_fields) == set(model_fields), (
        f"{method.upper()} {path}: fields drift from {model.__name__}"
    )
    expected_required = {name for name, field in model.model_fields.items() if field.is_required()}
    assert set(documented.get("required", [])) == expected_required, (
        f"{method.upper()} {path}: required fields differ from {model.__name__}"
    )
    for name, actual in model_fields.items():
        spec = documented_fields[name]
        assert _schema_types(spec) == _schema_types(actual), (
            f"{method.upper()} {path} {name}: JSON types differ: "
            f"documented {_schema_types(spec)} vs model {_schema_types(actual)}"
        )
        for constraint in CONSTRAINTS:
            assert _constraint_value(spec, constraint) == _constraint_value(actual, constraint), (
                f"{method.upper()} {path} {name}: {constraint} differs "
                f"({_constraint_value(spec, constraint)!r} vs {_constraint_value(actual, constraint)!r})"
            )
        actual_enum = actual.get("enum")
        if actual_enum is None and "const" in actual:
            actual_enum = [actual["const"]]
        assert spec.get("enum") == actual_enum, (
            f"{method.upper()} {path} {name}: enum differs"
        )


def test_every_json_body_endpoint_is_covered():
    contract = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    actual = {
        (method.lower(), path)
        for path, item in contract["paths"].items()
        for method, value in item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
        and isinstance(value, dict)
        and "requestBody" in value
        and "application/json" in value["requestBody"].get("content", {})
    }
    assert actual == set(OPERATIONS), (
        f"Missing schema model test for operations: {actual - set(OPERATIONS)}"
    )
