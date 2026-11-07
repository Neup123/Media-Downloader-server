"""Core admin presets must be explicitly published, safe and backward-compatible."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import Preset, initialize_database, make_engine


def create(client, headers, *, shared=False, engine="yt-dlp", settings=None, name="Example"):
    return client.post("/api/v1/presets", headers=headers, json={
        "name": name, "engine": engine, "description": "an admin preset",
        "settings": settings or {"format": "best"}, "is_shared": shared,
    })


def test_private_by_default_and_shared_only_when_approved(client, auth_headers):
    private = create(client, auth_headers, name="Private")
    assert private.status_code == 201
    assert private.json()["is_shared"] is False
    assert client.get("/api/v1/presets/shared").status_code == 401
    assert client.get("/api/v1/presets/shared", headers=auth_headers).json() == []

    shared = create(client, auth_headers, shared=True, name="Published")
    assert shared.status_code == 201
    assert shared.json()["is_shared"] is True
    listing = client.get("/api/v1/presets/shared", headers=auth_headers).json()
    assert [item["id"] for item in listing] == [shared.json()["id"]]
    assert private.json()["id"] not in {item["id"] for item in listing}

    revoke = client.put("/api/v1/presets/" + shared.json()["id"],
                        headers=auth_headers, json={
        "name": "Published", "engine": "yt-dlp",
        "settings": {"format": "best"}, "is_shared": False,
    })
    assert revoke.status_code == 200
    assert client.get("/api/v1/presets/shared", headers=auth_headers).json() == []
    assert len(client.get("/api/v1/presets", headers=auth_headers).json()) == 2


def test_publishing_rejects_private_cookie_proxy_and_cli_settings(client, auth_headers):
    cases = [
        ("yt-dlp", {"cookies_file": "admin-private.txt"}),
        ("yt-dlp", {"extra_args": ["--no-warnings"]}),
        ("gallery-dl", {"proxy": "https://proxy.example.org"}),
        ("gallery-dl", {"options": {"extractor.generic.referer": "https://private.example.org"}}),
        ("you-get", {"cookies_file": "admin-private.txt"}),
    ]
    for index, (engine, settings) in enumerate(cases):
        invalid = create(client, auth_headers, shared=True, engine=engine,
                         settings=settings, name=f"Sensitive {index}")
        assert invalid.status_code == 422, (engine, settings, invalid.text)
        assert "cookie" in invalid.text.lower() or "site access" in invalid.text.lower()
    safe = create(client, auth_headers, shared=True, settings={"format": "best"})
    assert safe.status_code == 201
    assert client.post("/api/v1/presets", headers=auth_headers, json={
        "name": "Not boolean", "engine": "yt-dlp", "settings": {"format": "best"},
        "is_shared": "true",
    }).status_code == 422


def test_existing_database_presets_migrate_to_private(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path}/legacy.db")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE presets(
                id VARCHAR(36) PRIMARY KEY, name VARCHAR(120) NOT NULL,
                engine VARCHAR(40) NOT NULL, settings JSON NOT NULL,
                description TEXT NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO presets(id, name, engine, settings, description, created_at, updated_at)
            VALUES ('00000000-0000-4000-8000-000000000001',
                'Existing', 'yt-dlp', '{}', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
    initialize_database(engine)
    assert "is_shared" in {item["name"] for item in inspect(engine).get_columns("presets")}
    with Session(engine) as session:
        row = session.get(Preset, "00000000-0000-4000-8000-000000000001")
        assert row is not None and row.is_shared is False
    initialize_database(engine)  # no-op and restart-safe
    engine.dispose()
