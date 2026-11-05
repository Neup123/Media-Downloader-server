"""WebDAV credentials require a separate, explicit destination network policy."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database, make_engine, make_session_factory
from app.delivery import DeliveryError, deliver_files, set_delivery_settings
from app.main import create_app
from app.schemas import DeliverySettingsIn
from app.security import UnsafeUrlError


def _payload(url: str) -> dict:
    return {
        "mode": "webdav",
        "webdav_url": url,
        "username": "media-user",
        "app_password": "test-only-password",
        "folder": "Downloader",
    }


@pytest.mark.parametrize("url,detail", [
    ("http://cloud.example.com/remote.php/dav/files/user", "Plain HTTP"),
    ("http://127.0.0.1/remote.php/dav/files/user", "not allowed"),
    ("https://169.254.169.254/remote.php/dav/files/user", "not allowed"),
    ("https://[::1]/remote.php/dav/files/user", "not allowed"),
])
def test_default_delivery_settings_reject_unsafe_destinations(client, auth_headers, url, detail):
    response = client.put("/api/v1/delivery", headers=auth_headers, json=_payload(url))
    assert response.status_code == 422, response.text
    assert detail in response.text


def test_public_https_webdav_remains_supported(client, auth_headers):
    response = client.put("/api/v1/delivery", headers=auth_headers, json=_payload(
        "https://cloud.example.com/remote.php/dav/files/user"
    ))
    assert response.status_code == 200
    assert response.json()["password_configured"]
    assert "test-only-password" not in response.text


def test_media_private_target_flag_does_not_allow_private_webdav(settings):
    # ALLOW_PRIVATE_TARGETS exists only for user-submitted media URLs and
    # must never implicitly permit authenticated requests to internal hosts.
    media_permissive = replace(settings, allow_private_targets=True)
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    payload = DeliverySettingsIn(**_payload("https://10.30.0.12/remote.php/dav/files/user"))
    try:
        with sessions() as session:
            with pytest.raises(DeliveryError, match="not allowed"):
                set_delivery_settings(session, payload, media_permissive)
    finally:
        engine.dispose()


def test_trusted_private_http_webdav_requires_both_explicit_opt_ins(settings, auth_headers):
    url = "http://172.21.0.7/remote.php/dav/files/user"
    for kwargs, expected in [
        ({"allow_private_webdav_targets": True}, "Plain HTTP"),
        ({"allow_insecure_webdav": True}, "not allowed"),
    ]:
        app = create_app(replace(settings, **kwargs), start_worker=False)
        with TestClient(app) as client:
            rejected = client.put("/api/v1/delivery", headers=auth_headers, json=_payload(url))
            assert rejected.status_code == 422
            assert expected in rejected.text

    both = replace(
        settings, allow_private_webdav_targets=True, allow_insecure_webdav=True,
        allow_private_targets=False,
    )
    app = create_app(both, start_worker=False)
    with TestClient(app) as client:
        allowed = client.put("/api/v1/delivery", headers=auth_headers, json=_payload(url))
        assert allowed.status_code == 200
        assert allowed.json()["password_configured"]
        assert both.allow_private_targets is False


def test_delivery_fails_closed_on_private_dns_answer(settings, monkeypatch):
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    public_url = "https://cloud.example.test/remote.php/dav/files/user"
    source = settings.data_dir / "jobs" / "dns-test" / "clip.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fixture")
    with sessions() as session:
        set_delivery_settings(session, DeliverySettingsIn(**_payload(public_url)), settings)

    observed = []

    def untrusted_dns(url, allow_private):
        observed.append((url, allow_private))
        raise UnsafeUrlError("DNS resolution includes a private or reserved address")

    monkeypatch.setattr("app.delivery.resolve_http_target", untrusted_dns)
    try:
        with pytest.raises(DeliveryError, match="DNS check failed"):
            asyncio.run(deliver_files([source], "dns-test", sessions, settings))
        assert observed == [(public_url, False)]
    finally:
        engine.dispose()
