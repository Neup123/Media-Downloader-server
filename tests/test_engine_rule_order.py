"""Atomic routing order is the priority used by actual automatic downloads."""
from __future__ import annotations

from app.db import EngineRule


def create_rule(client, headers, engine, *, host="media.example.org",
                path="/", priority=100, enabled=True):
    response = client.post("/api/v1/engine-rules", headers=headers, json={
        "host_suffix": host, "path_prefix": path, "engine": engine,
        "priority": priority, "enabled": enabled,
        "description": "routing acceptance",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def resolved_engine(client, headers):
    response = client.post("/api/v1/jobs", headers=headers, json={
        "url": "https://media.example.org/watch/123", "engine": "auto",
    })
    assert response.status_code == 202, response.text
    return response.json()["engine"]


def test_reorder_requires_auth_and_complete_unique_ids(client, auth_headers):
    first = create_rule(client, auth_headers, "yt-dlp")
    second = create_rule(client, auth_headers, "aria2c", priority=50)
    third = create_rule(client, auth_headers, "gallery-dl", enabled=False)
    target = "/api/v1/engine-rules/order"
    before = client.get("/api/v1/engine-rules", headers=auth_headers).json()

    assert client.put(target, json={"ids": [third, second, first]}).status_code == 401
    assert client.put(target, headers=auth_headers,
                      json={"ids": [first, second, second]}).status_code == 422
    assert client.put(target, headers=auth_headers,
                      json={"ids": [first, second]}).status_code == 409
    assert client.put(target, headers=auth_headers,
                      json={"ids": [first, second, "stale-id"]}).status_code == 409
    assert client.put(target, headers=auth_headers, json={"ids": []}).status_code == 422

    after = client.get("/api/v1/engine-rules", headers=auth_headers).json()
    assert after == before, "An invalid reorder must not change any priorities"
    assert len(after) == 3


def test_reorder_updates_real_auto_engine_selection_and_is_atomic(client, auth_headers):
    video = create_rule(client, auth_headers, "yt-dlp", priority=100)
    direct = create_rule(client, auth_headers, "aria2c", priority=50)
    inactive = create_rule(client, auth_headers, "gallery-dl", priority=20, enabled=False)

    assert resolved_engine(client, auth_headers) == "yt-dlp"
    response = client.put("/api/v1/engine-rules/order", headers=auth_headers,
                          json={"ids": [direct, inactive, video]})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [direct, inactive, video]
    assert [row["priority"] for row in response.json()] == [1000, 999, 998]
    assert response.json()[1]["enabled"] is False
    assert resolved_engine(client, auth_headers) == "aria2c"

    with client.app.state.session_factory() as session:
        assert session.get(EngineRule, direct).priority == 1000
        assert session.get(EngineRule, video).priority == 998
        assert session.get(EngineRule, inactive).enabled is False

    # Deleting or adding a rule between UI read and PUT must reject stale order
    # instead of silently dropping a rule or changing only some priorities.
    extra = create_rule(client, auth_headers, "you-get", host="extra.example.org")
    stale = client.put("/api/v1/engine-rules/order", headers=auth_headers,
                       json={"ids": [video, direct, inactive]})
    assert stale.status_code == 409
    assert extra in {row["id"] for row in client.get(
        "/api/v1/engine-rules", headers=auth_headers).json()}
    assert resolved_engine(client, auth_headers) == "aria2c"


def test_existing_individual_rule_edits_still_work_after_reorder(client, auth_headers):
    first = create_rule(client, auth_headers, "yt-dlp")
    second = create_rule(client, auth_headers, "aria2c")
    assert client.put("/api/v1/engine-rules/order", headers=auth_headers,
                      json={"ids": [first, second]}).status_code == 200

    edit = client.put("/api/v1/engine-rules/" + second, headers=auth_headers,
                      json={"host_suffix": "media.example.org", "path_prefix": "/",
                            "engine": "aria2c", "priority": 1000,
                            "enabled": True, "description": "admin advanced edit"})
    assert edit.status_code == 200
    assert edit.json()["priority"] == 1000
    assert edit.json()["description"] == "admin advanced edit"
