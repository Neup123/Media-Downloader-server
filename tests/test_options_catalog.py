from app.options_catalog import get_catalog


def test_option_catalog_exposes_typed_fields_for_all_p0_engines(client, auth_headers):
    for engine in ("yt-dlp", "aria2c", "gallery-dl"):
        response = client.get(f"/api/v1/engines/{engine}/options", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["engine"] == engine
        fields = response.json()["fields"]
        assert fields
        assert all(field["type"] in {"text", "boolean", "integer"} for field in fields)
        assert len({field["key"] for field in fields}) == len(fields)

    aria_keys = {field["key"] for field in get_catalog("aria2c")}
    assert {"max-connection-per-server", "split", "retry-wait"}.issubset(aria_keys)
    gallery_keys = {field["key"] for field in get_catalog("gallery-dl")}
    assert {"extractor.sleep", "extractor.sleep-request", "extractor.skip"}.issubset(gallery_keys)


def test_gallery_output_templates_cannot_traverse_directory(client, auth_headers):
    for name in ("extractor.filename", "extractor.directory", "extractor.example.filename"):
        result = client.put("/api/v1/engines/gallery-dl/settings", headers=auth_headers, json={
            "settings": {"options": {name: "../escape.jpg"}},
        })
        assert result.status_code == 422, (name, result.text)


def test_list_engines_reports_installation_status(client, auth_headers):
    result = client.get("/api/v1/engines", headers=auth_headers)
    assert result.status_code == 200
    assert len(result.json()) == 5
    for item in result.json():
        assert isinstance(item["installed"], bool)
        assert "version" in item
