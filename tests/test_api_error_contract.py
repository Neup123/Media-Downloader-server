"""Guard documented authentication and validation failures against API drift."""
from __future__ import annotations

from pathlib import Path

import yaml


HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}


def _contract():
    return yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8")
    )


def _error_detail(response):
    body = response.json()
    assert "detail" in body, response.text
    assert isinstance(body["detail"], (str, list, dict)), body
    return body["detail"]


def test_all_protected_operations_document_api_key_401():
    spec = _contract()
    operations = 0
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operations += 1
            response = operation["responses"]["401"]
            assert response == {"$ref": "#/components/responses/Unauthorized"}, (
                method, path, response,
            )
    assert operations >= 26
    schema = spec["components"]["responses"]["Unauthorized"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["detail"]
    assert schema["properties"]["detail"]["type"] == "string"


def test_all_json_body_operations_document_validation_errors():
    spec = _contract()
    found = 0
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS or "requestBody" not in operation:
                continue
            found += 1
            assert "422" in operation["responses"], f"{method.upper()} {path} is missing HTTP 422"
    assert found >= 10


def test_unauthorized_api_operations_return_documented_detail(client, auth_headers):
    cases = [
        client.get("/api/v1/engines"),
        client.get("/api/v1/jobs"),
        client.post("/api/v1/jobs", json={"url": "https://example.com/video"}),
        client.get("/api/v1/delivery"),
        client.delete("/api/v1/cookies/missing.txt"),
    ]
    for response in cases:
        assert response.status_code == 401, response.text
        detail = _error_detail(response)
        assert isinstance(detail, str)
        assert auth_headers["X-API-Key"] not in detail


def test_schema_and_service_validation_return_documented_422(client, auth_headers):
    wrong_engine = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/video", "engine": "unregistered-engine",
    })
    assert wrong_engine.status_code == 422
    assert isinstance(_error_detail(wrong_engine), list)

    invalid_source = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "file:///etc/passwd",
    })
    assert invalid_source.status_code == 422
    assert isinstance(_error_detail(invalid_source), str)

    invalid_preset = client.post("/api/v1/presets", headers=auth_headers, json={
        "name": "Invalid", "engine": "unregistered-engine",
    })
    assert invalid_preset.status_code == 422
    assert isinstance(_error_detail(invalid_preset), list)

    cookie_suffix = client.put("/api/v1/cookies/bad.exe", headers=auth_headers, json={
        "content_base64": "YWJj",
    })
    assert cookie_suffix.status_code == 422
    assert isinstance(_error_detail(cookie_suffix), str)


def test_not_found_and_conflict_have_stable_documented_responses(client, auth_headers):
    spec = _contract()
    assert "404" in spec["paths"]["/jobs/{job_id}"]["get"]["responses"]
    missing = client.get("/api/v1/jobs/does-not-exist", headers=auth_headers)
    assert missing.status_code == 404
    assert isinstance(_error_detail(missing), str)

    assert "409" in spec["paths"]["/jobs/{job_id}/retry"]["post"]["responses"]
    created = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.com/new", "engine": "yt-dlp",
    })
    assert created.status_code == 202
    retry_queued = client.post(
        f"/api/v1/jobs/{created.json()['id']}/retry", headers=auth_headers,
    )
    assert retry_queued.status_code == 409
    assert isinstance(_error_detail(retry_queued), str)
