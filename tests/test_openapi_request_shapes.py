"""Keep public request-body schemas synchronized with FastAPI/Pydantic validation.

Compare validation-related JSON Schema keywords, not generated titles or prose.
An explicit default, optional field, enum, length or numeric constraint must
mean the same thing in the checked-in OpenAPI YAML as in the live application.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.main import create_app


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}
CONSTRAINTS = {
    "type", "enum", "const", "default", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties",
}


def _dereference(value, document):
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        assert ref.startswith("#/"), f"Unsupported external schema reference: {ref}"
        resolved = document
        for part in ref[2:].split("/"):
            resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
        value = resolved
    return value


def _shape(value, document):
    value = _dereference(value, document)
    if not isinstance(value, dict):
        return value

    result = {key: value[key] for key in CONSTRAINTS if key in value}

    # OpenAPI 3.1 allows both type: [string, null] and the Pydantic
    # anyOf: [{type: string}, {type: null}] representation of nullability.
    if "anyOf" in value:
        variants = [_shape(item, document) for item in value["anyOf"]]
        if all(set(item) == {"type"} and isinstance(item["type"], str) for item in variants):
            result["type"] = sorted(item["type"] for item in variants)
        else:
            result["anyOf"] = variants
    if isinstance(result.get("type"), list):
        result["type"] = sorted(result["type"])

    if "required" in value:
        result["required"] = sorted(value["required"])
    if "properties" in value:
        result["properties"] = {
            key: _shape(schema, document)
            for key, schema in sorted(value["properties"].items())
        }
    if "items" in value:
        result["items"] = _shape(value["items"], document)
    if "additionalProperties" in value:
        extra = value["additionalProperties"]
        result["additionalProperties"] = (
            _shape(extra, document) if isinstance(extra, dict) else extra
        )
    return result


def test_checked_in_request_body_constraints_match_runtime_validation(settings):
    documented = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8")
    )
    app = create_app(settings, start_worker=False)
    try:
        actual = app.openapi()
        checked = 0
        for path, path_item in documented["paths"].items():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                actual_operation = actual["paths"][f"/api/v1{path}"][method]
                stated_body = operation.get("requestBody")
                runtime_body = actual_operation.get("requestBody")
                assert (stated_body is None) == (runtime_body is None), (
                    f"{method.upper()} {path}: request-body presence differs"
                )
                if stated_body is None:
                    continue
                checked += 1
                assert stated_body.get("required", False) == runtime_body.get("required", False), (
                    f"{method.upper()} {path}: request-body required flag differs"
                )
                stated = _shape(stated_body["content"]["application/json"]["schema"], documented)
                runtime = _shape(runtime_body["content"]["application/json"]["schema"], actual)
                assert stated == runtime, f"{method.upper()} {path}: request schema differs\nYAML: {stated}\nRuntime: {runtime}"
        assert checked >= 10, "Expected to examine all request-body endpoints"
    finally:
        app.state.engine.dispose()
