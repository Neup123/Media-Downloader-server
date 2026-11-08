"""Live-like destination preflight must prove readback and cleanup."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
import httpx
import pytest

from app.delivery import DeliveryError, set_delivery_settings, verify_delivery_destination
from app.schemas import DeliverySettingsIn

PAYLOAD = b"Downloader delivery preflight\n"


def test_local_destination_preflight_really_writes_and_cleans(client, auth_headers, settings):
    denied = client.post("/api/v1/delivery/test")
    assert denied.status_code == 401
    result = client.post("/api/v1/delivery/test", headers=auth_headers)
    assert result.status_code == 200
    assert result.json()["mode"] == "local"
    assert result.json()["verified"] is True
    assert result.json()["cleaned_up"] is True
    assert list(settings.output_dir.glob(".downloader-check-*")) == []


def _mock_dav(*, bad_readback=False, bad_cleanup=False):
    calls = []
    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            assert await request.aread() == PAYLOAD
            return httpx.Response(201)
        if request.method == "PROPFIND":
            href = request.url.path
            body = (
                '<d:multistatus xmlns:d="DAV:"><d:response><d:href>'
                + href + '</d:href><d:propstat><d:status>HTTP/1.1 200 OK</d:status>'
                + f'<d:prop><d:getcontentlength>{len(PAYLOAD)}</d:getcontentlength></d:prop>'
                + '</d:propstat></d:response></d:multistatus>'
            )
            return httpx.Response(207, content=body.encode())
        if request.method == "GET":
            return httpx.Response(200, content=PAYLOAD + (b"wrong" if bad_readback else b""))
        if request.method == "DELETE":
            return httpx.Response(403 if bad_cleanup else 204)
        raise AssertionError("Unexpected WebDAV method")
    return httpx.MockTransport(handle), calls


def _configure_webdav(client, settings):
    with client.app.state.session_factory() as session:
        set_delivery_settings(session, DeliverySettingsIn(
            mode="webdav", folder="Downloads",
            webdav_url="https://cloud.example.org/remote.php/dav/files/admin",
            username="admin", app_password="private-password",
        ), settings)


def test_webdav_preflight_verifies_real_put_propfind_get_delete(client, settings):
    _configure_webdav(client, settings)
    transport, calls = _mock_dav()
    result = asyncio.run(verify_delivery_destination(
        client.app.state.session_factory, settings, transport=transport))
    assert result["mode"] == "webdav" and result["cleaned_up"] and result["verified"]
    assert [method for method, _ in calls] == ["MKCOL", "MKCOL", "PUT", "PROPFIND", "GET", "DELETE", "DELETE"]
    assert not any("private-password" in str(x) for x in calls)


@pytest.mark.parametrize("bad_readback,bad_cleanup", [(True, False), (False, True)])
def test_webdav_preflight_does_not_claim_success_if_readback_or_cleanup_fails(
    client, settings, bad_readback, bad_cleanup
):
    _configure_webdav(client, settings)
    transport, calls = _mock_dav(bad_readback=bad_readback, bad_cleanup=bad_cleanup)
    with pytest.raises(DeliveryError):
        asyncio.run(verify_delivery_destination(
            client.app.state.session_factory, settings, transport=transport))
    assert [method for method, _ in calls][-2:] == ["DELETE", "DELETE"]
