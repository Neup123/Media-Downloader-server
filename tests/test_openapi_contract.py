"""Fail CI when checked-in Scalar/OpenAPI endpoints drift from FastAPI routes."""

from pathlib import Path

import yaml
from fastapi.routing import APIRoute

from app.main import create_app


API_PREFIX = "/api/v1"
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options"}


def test_checked_in_openapi_methods_match_live_routes(settings):
    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["servers"][0]["url"] == API_PREFIX

    documented = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method.lower() in HTTP_METHODS:
                documented[(path, method.lower())] = operation

    app = create_app(settings, start_worker=False)
    try:
        live = {}
        for route in app.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            if not route.path.startswith(API_PREFIX + "/"):
                continue
            relative_path = route.path[len(API_PREFIX):]
            for method in route.methods:
                live[(relative_path, method.lower())] = route

        assert set(documented) == set(live), (
            f"Undocumented live operations: {sorted(set(live) - set(documented))}. "
            f"Documented but missing operations: {sorted(set(documented) - set(live))}"
        )

        for endpoint, route in live.items():
            operation = documented[endpoint]
            status = str(route.status_code or 200)
            assert status in operation.get("responses", {}), (
                f"{endpoint} returns HTTP {status} but openapi.yaml does not document it"
            )
            # Route-level authentication must match the global OpenAPI security
            # declaration. Authentication failures must not be undocumented.
            dependencies = {dep.call.__name__ for dep in route.dependant.dependencies}
            assert "authenticate" in dependencies, f"{endpoint} lost X-API-Key enforcement"
            assert operation.get("security", spec["security"]) == [{"ApiKeyAuth": []}]
    finally:
        app.state.engine.dispose()


def test_api_key_contract_names_the_actual_header():
    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key"
    }
    assert spec["security"] == [{"ApiKeyAuth": []}]
