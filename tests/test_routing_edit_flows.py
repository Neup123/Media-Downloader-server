"""Exercise the PUT flows now available in the graphical preset and routing editors."""

from __future__ import annotations


def test_preset_can_be_edited_without_changing_its_identity(client, auth_headers):
    created = client.post("/api/v1/presets", headers=auth_headers, json={
        "name": "Initial", "engine": "yt-dlp", "description": "Old preset",
        "settings": {"format": "best"},
    })
    assert created.status_code == 201
    identifier = created.json()["id"]

    updated = client.put(f"/api/v1/presets/{identifier}", headers=auth_headers, json={
        "name": "Edited preset", "engine": "yt-dlp", "description": "New settings",
        "settings": {"format": "bv*+ba/b", "write_subtitles": True},
    })
    assert updated.status_code == 200
    assert updated.json()["id"] == identifier
    assert updated.json()["description"] == "New settings"
    listed = client.get("/api/v1/presets", headers=auth_headers).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Edited preset"
    assert listed[0]["settings"]["write_subtitles"] is True

    job = client.post("/api/v1/jobs", headers=auth_headers, json={
        "url": "https://example.org/watch", "engine": "yt-dlp",
        "preset_id": identifier,
    })
    assert job.status_code == 202
    assert job.json()["overrides"]["write_subtitles"] is True


def test_url_rule_edit_updates_priority_engine_and_enabled_state(client, auth_headers):
    created = client.post("/api/v1/engine-rules", headers=auth_headers, json={
        "host_suffix": "routed.example", "path_prefix": "/media",
        "engine": "gallery-dl", "priority": 150,
        "enabled": True, "description": "Initial",
    })
    assert created.status_code == 201
    identifier = created.json()["id"]

    def auto_engine():
        response = client.post("/api/v1/jobs", headers=auth_headers, json={
            "url": "https://routed.example/media/item", "engine": "auto",
        })
        assert response.status_code == 202, response.text
        return response.json()["engine"]

    assert auto_engine() == "gallery-dl"

    disabled = client.put(f"/api/v1/engine-rules/{identifier}", headers=auth_headers, json={
        "host_suffix": "routed.example", "path_prefix": "/media",
        "engine": "aria2c", "priority": 220,
        "enabled": False, "description": "Pause routing",
    })
    assert disabled.status_code == 200
    assert disabled.json()["id"] == identifier
    assert disabled.json()["enabled"] is False
    assert auto_engine() == "yt-dlp"

    enabled = client.put(f"/api/v1/engine-rules/{identifier}", headers=auth_headers, json={
        "host_suffix": "routed.example", "path_prefix": "/media",
        "engine": "aria2c", "priority": 220,
        "enabled": True, "description": "Active",
    })
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["description"] == "Active"
    assert auto_engine() == "aria2c"
    fetched = client.get("/api/v1/engine-rules", headers=auth_headers).json()
    assert len(fetched) == 1 and fetched[0]["id"] == identifier
    assert fetched[0]["priority"] == 220
