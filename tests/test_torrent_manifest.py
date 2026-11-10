from __future__ import annotations

import base64

from app.schemas import TorrentManifestIn


def test_torrent_manifest_input_requires_exactly_one_source():
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        TorrentManifestIn()
    with pytest.raises(ValidationError):
        TorrentManifestIn(url="magnet:?xt=urn:btih:abcdef12345678", torrent_base64="YWJjZA==")


def test_torrent_manifest_api_is_authenticated_and_does_not_queue_jobs(client, auth_headers, monkeypatch):
    observed = []

    class FakeRpc:
        def __init__(self, data_dir, port):
            observed.append(("init", str(data_dir), port))

        async def inspect_torrent(self, *, url=None, torrent_base64=None):
            observed.append(("inspect", url, torrent_base64))
            return {"file_count": 1, "files": [
                {"index": 1, "path": "album/cover.jpg", "name": "cover.jpg", "size_bytes": 123},
            ]}

        async def close(self):
            observed.append(("close",))

    monkeypatch.setattr("app.main.Aria2Rpc", FakeRpc)
    magnet = "magnet:?xt=urn:btih:0123456789012345678901234567890123456789"
    assert client.post("/api/v1/torrent/manifest", json={"url": magnet}).status_code == 401
    response = client.post("/api/v1/torrent/manifest", headers=auth_headers, json={"url": magnet})
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["size_bytes"] == 123
    assert ("inspect", magnet, None) in observed
    assert ("close",) in observed
    jobs = client.get("/api/v1/jobs", headers=auth_headers)
    assert jobs.status_code == 200 and jobs.json() == []

    upload = base64.b64encode(b"d4:infod4:name4:test6:lengthi1eee").decode()
    uploaded = client.post("/api/v1/torrent/manifest", headers=auth_headers, json={
        "torrent_base64": upload,
    })
    assert uploaded.status_code == 200, uploaded.text
    assert ("inspect", None, upload) in observed


def test_torrent_manifest_rejects_nonmagnet_urls_and_bad_uploads(client, auth_headers, monkeypatch):
    class ShouldNotRun:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid manifest must not start aria2")

    monkeypatch.setattr("app.main.Aria2Rpc", ShouldNotRun)
    for payload in (
        {"url": "https://example.com/archive.zip"},
        {"url": "http://127.0.0.1/archive.torrent"},
        {"torrent_base64": "%%%not_base64"},
        {"torrent_base64": "YQ=="},
        {"torrent_base64": base64.b64encode(b"x" * (2_000_001)).decode()},
    ):
        response = client.post("/api/v1/torrent/manifest", headers=auth_headers, json=payload)
        assert response.status_code == 422, (payload.keys(), response.text)
