from __future__ import annotations


def test_reference_fallback_never_calls_an_external_cdn(client):
    response = client.get("/api-reference")
    assert response.status_code == 200
    assert "cdn.jsdelivr.net" not in response.text
    assert "/openapi.yaml" in response.text


def test_reference_is_served_from_bundled_static_artifact(client, monkeypatch, tmp_path):
    reference = tmp_path / "api-reference.html"
    reference.write_text("<html><body>Local Scalar build marker</body></html>", encoding="utf-8")
    monkeypatch.setattr("app.main.WEB_DIST", tmp_path)
    response = client.get("/api-reference")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Local Scalar build marker" in response.text
    assert "cdn.jsdelivr.net" not in response.text
