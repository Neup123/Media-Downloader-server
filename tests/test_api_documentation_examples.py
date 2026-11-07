"""Developer API reference stays local, grouped, example-backed and authenticated."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_scalar_reference_has_product_intro_and_local_pinned_config():
    html = (ROOT / "web/api-reference.html").read_text()
    script = (ROOT / "web/src/api-reference.ts").read_text()
    package = (ROOT / "web/package.json").read_text()
    assert "Downloader Server — API reference" in html
    assert "X-API-Key" in html
    assert 'url: \'/openapi.yaml\'' in script
    assert "theme: 'kepler'" in script
    assert '"@scalar/api-reference": "1.62.4"' in package
    for remote in ("https://cdn.", "latest", "unpkg.com"):
        assert remote not in script


def test_openapi_has_grouped_descriptions_and_realistic_examples():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text())
    assert "X-API-Key" in spec["info"]["description"]
    assert [tag["name"] for tag in spec["tags"]] == [
        "jobs", "engines", "settings", "delivery"
    ]
    assert all(tag["description"] for tag in spec["tags"])
    paths = spec["paths"]
    examples = [
        ("/jobs", "post"), ("/jobs/batch", "post"),
        ("/presets", "post"), ("/engine-rules", "post"),
        ("/engines/{engine_name}/settings", "put"), ("/delivery", "put"),
        ("/preview", "post"),
    ]
    for path, method in examples:
        operation = paths[path][method]
        body = operation["requestBody"]["content"]["application/json"]
        assert "example" in body or "examples" in body, (path, method)
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    assert spec["security"] == [{"ApiKeyAuth": []}]
