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

    # Pydantic expresses nullable constrained strings as anyOf with a
    # constraint on the string branch; OpenAPI 3.1 can instead put that
    # constraint on a union of types. They validate the same inputs.
    if "anyOf" in value:
        variants = [_shape(item, document) for item in value["anyOf"]]
        nullable = next((item for item in variants if item == {"type": "null"}), None)
        others = [item for item in variants if item != {"type": "null"}]
        if nullable is not None and len(others) == 1 and isinstance(others[0].get("type"), str):
            result.update(others[0])
            result["type"] = sorted([others[0]["type"], "null"])
        elif all(set(item) == {"type"} and isinstance(item["type"], str) for item in variants):
            result["type"] = sorted(item["type"] for item in variants)
        else:
            result["anyOf"] = variants
    if isinstance(result.get("type"), list):
        result["type"] = sorted(result["type"])
    # An explicitly null informational default and an omitted default
    # are equivalent for these nullable optional request fields.
    if result.get("default", object()) is None:
        del result["default"]

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


JOB_RESPONSE_OPERATIONS = [
    ("get", "/jobs", "200"),
    ("post", "/jobs", "202"),
    ("post", "/jobs/batch", "202"),
    ("get", "/jobs/{job_id}", "200"),
    ("post", "/jobs/{job_id}/cancel", "200"),
    ("post", "/jobs/{job_id}/retry", "202"),
    ("post", "/jobs/{job_id}/redeliver", "202"),
]


def test_job_response_contracts_match_fastapi_output_models(settings):
    """Catch missing fields and response model changes before Scalar drifts."""
    stated = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8")
    )
    app = create_app(settings, start_worker=False)
    try:
        runtime = app.openapi()
        for method, path, status in JOB_RESPONSE_OPERATIONS:
            spec = stated["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
            actual = runtime["paths"][f"/api/v1{path}"][method]["responses"][status]["content"]["application/json"]["schema"]
            assert _shape(spec, stated) == _shape(actual, runtime), (
                f"{method.upper()} {path}: documented response differs from enforced FastAPI response model"
            )
    finally:
        app.state.engine.dispose()


def test_job_response_models_preserve_log_and_state(client, auth_headers):
    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/video",
        "engine": "yt-dlp",
    })
    assert job.status_code == 202
    expected = {
        "id", "url", "owner_id", "engine", "status", "overrides", "progress",
        "output_files", "log_text", "error", "cancel_requested",
        "created_at", "updated_at", "finished_at",
    }
    assert set(job.json()) == expected
    identifier = job.json()["id"]

    details = client.get(f"/api/v1/jobs/{identifier}", headers=auth_headers)
    assert details.status_code == 200
    assert set(details.json()) == expected
    assert isinstance(details.json()["log_text"], str)

    listing = client.get("/api/v1/jobs", headers=auth_headers)
    assert listing.status_code == 200
    assert any(item["id"] == identifier and set(item) == expected for item in listing.json())

    cancelled = client.post(f"/api/v1/jobs/{identifier}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert set(cancelled.json()) == expected
    assert cancelled.json()["status"] == "cancelled"
    retried = client.post(f"/api/v1/jobs/{identifier}/retry", headers=auth_headers)
    assert retried.status_code == 202
    assert set(retried.json()) == expected

    batch = client.post("/api/v1/jobs/batch", headers=auth_headers, json={
        "items": [{"url": "https://example.com/other", "engine": "yt-dlp"}],
    })
    assert batch.status_code == 202
    assert batch.json()["created"] == 1
    assert set(batch.json()["jobs"][0]) == expected
