"""Typed API contracts must preserve auth, payloads and secret redaction."""
from __future__ import annotations

import base64
from pathlib import Path

import yaml

from app.schemas import (
    CookieDeletedOut, CookieFileOut, CookieUploadedOut, DeliverySettingsOut,
    EngineOptionsOut, EngineSettingsOut, EngineStatusOut,
)


def test_engine_responses_are_typed_without_changing_existing_json(client, auth_headers):
    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert rows
    assert all(EngineStatusOut.model_validate(row).model_dump(mode="json") == row for row in rows)
    response = client.get("/api/v1/engines/generic/options", headers=auth_headers)
    assert response.status_code == 200, response.text
    options = response.json()
    assert EngineOptionsOut.model_validate(options).engine == "generic"
    assert all("minimum" not in row or row["minimum"] is not None for row in options["fields"])
    response = client.get("/api/v1/engines/generic/settings", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert EngineSettingsOut.model_validate(response.json()).engine == "generic"
    response = client.put(
        "/api/v1/engines/generic/settings", headers=auth_headers,
        json={"settings": {"retry_count": 3}},
    )
    assert response.status_code == 200, response.text
    assert EngineSettingsOut.model_validate(response.json()).settings["retry_count"] == 3
    assert client.get("/api/v1/engines").status_code == 401


def test_cookie_response_is_typed_and_never_exposes_contents(client, auth_headers):
    content = "# Netscape HTTP Cookie File\nexample.org\tFALSE\t/\tFALSE\t2147483647\tssid\tsecret"
    name = "typed-response-test.cookies"
    payload = {"content_base64": base64.b64encode(content.encode()).decode()}
    uploaded = client.put("/api/v1/cookies/" + name, headers=auth_headers, json=payload)
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert CookieUploadedOut.model_validate(body).stored is True
    assert "secret" not in str(body)
    fetched = client.get("/api/v1/cookies", headers=auth_headers)
    assert fetched.status_code == 200, fetched.text
    assert any(CookieFileOut.model_validate(row).filename == name for row in fetched.json())
    assert "secret" not in str(fetched.json())
    removed = client.delete("/api/v1/cookies/" + name, headers=auth_headers)
    assert removed.status_code == 200, removed.text
    assert CookieDeletedOut.model_validate(removed.json()).deleted is True
    assert client.get("/api/v1/cookies").status_code == 401


def test_delivery_response_masks_credentials_and_preserves_optional_fields(client, auth_headers):
    result = client.get("/api/v1/delivery", headers=auth_headers)
    assert result.status_code == 200
    assert result.json() == {
        "mode": "local", "folder": "Downloader", "password_configured": False,
    }
    assert DeliverySettingsOut.model_validate(result.json()).mode == "local"
    updated = client.put(
        "/api/v1/delivery", headers=auth_headers,
        json={"mode": "local", "folder": "Validated"},
    )
    assert updated.status_code == 200, updated.text
    assert DeliverySettingsOut.model_validate(updated.json()).folder == "Validated"
    assert "app_password" not in updated.json()
    assert client.put("/api/v1/delivery", json={"mode": "local"}).status_code == 401


def test_all_documented_response_models_match_runtime_openapi(client):
    runtime = client.app.openapi()
    documented = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "openapi.yaml").read_text()
    )
    models = {
        ("/engines", "get"): "EngineStatusOut",
        ("/engines/{engine_name}/options", "get"): "EngineOptionsOut",
        ("/engines/{engine_name}/settings", "get"): "EngineSettingsOut",
        ("/engines/{engine_name}/settings", "put"): "EngineSettingsOut",
        ("/cookies", "get"): "CookieFileOut",
        ("/cookies/{filename}", "put"): "CookieUploadedOut",
        ("/cookies/{filename}", "delete"): "CookieDeletedOut",
        ("/delivery", "get"): "DeliverySettingsOut",
        ("/delivery", "put"): "DeliverySettingsOut",
    }
    for (path, method), model in models.items():
        relative = documented["paths"][path][method]
        target = relative["responses"]["200"]["content"]["application/json"]["schema"]
        assert target.get("$ref", target.get("items", {}).get("$ref")) == f"#/components/schemas/{model}"
        assert "401" in relative["responses"], (path, method)
        actual = runtime["paths"]["/api/v1" + path][method]["responses"]["200"]
        actual_ref = actual["content"]["application/json"]["schema"]
        assert actual_ref.get("$ref", actual_ref.get("items", {}).get("$ref")) == f"#/components/schemas/{model}"
    assert {"EngineStatusOut", "CookieDeletedOut", "DeliverySettingsOut"} <= set(
        documented["components"]["schemas"]
    )
