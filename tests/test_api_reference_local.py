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


def test_local_js_and_css_are_served_on_the_assets_mount(settings, monkeypatch, tmp_path):
    from app import main
    from fastapi.testclient import TestClient

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "api-reference-local.js").write_text("console.log('local')", encoding="utf-8")
    (assets / "api-reference-local.css").write_text(".scalar-app{color:white}", encoding="utf-8")
    (tmp_path / "api-reference.html").write_text(
        '<div id="scalar-reference"></div>'
        '<link rel="stylesheet" href="/assets/api-reference-local.css">'
        '<script type="module" src="/assets/api-reference-local.js"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "WEB_DIST", tmp_path)
    app = main.create_app(settings, start_worker=False)
    with TestClient(app) as client:
        page = client.get("/api-reference")
        assert page.status_code == 200
        assert '/assets/api-reference-local.js' in page.text
        assert client.get("/assets/api-reference-local.js").status_code == 200
        stylesheet = client.get("/assets/api-reference-local.css")
        assert stylesheet.status_code == 200
        assert "text/css" in stylesheet.headers["content-type"]
    app.state.engine.dispose()
