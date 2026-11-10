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


# aria2 reports a synthetic metadata-only file for a magnet before the
# followedBy GID exposes the real torrent files.
_METADATA_PATH = "[METADATA]0123456789abcdef0123456789abcdef01234567"


def test_aria2_magnet_manifest_waits_for_follow_on_gid(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from app.adapters.aria2 import Aria2Rpc

    observed = []
    preview_root = None

    async def fake_start(self):
        pass

    async def fake_call(self, method, params, include_token=True):
        nonlocal preview_root
        if method == "aria2.addUri":
            assert params[0][0].startswith("magnet:?")
            options = params[1]
            preview_root = Path(options["dir"])
            assert options["seed-time"] == "0"
            assert options["pause-metadata"] == "true"
            assert options["bt-metadata-only"] == "true"
            assert options["bt-save-metadata"] == "false"
            return "metadata-gid"
        if method == "aria2.tellStatus":
            gid = params[0]
            observed.append(("status", gid))
            if gid == "metadata-gid":
                return {
                    "status": "complete",
                    "bittorrent": {"info": {"name": "Example torrent"}},
                    "files": [{"path": _METADATA_PATH, "index": "1", "length": "0"}],
                    "followedBy": ["files-gid"],
                }
            assert gid == "files-gid"
            return {
                "status": "paused",
                "bittorrent": {"info": {"name": "Example torrent"}},
                "files": [
                    {"path": str(preview_root / "album" / "track.flac"), "index": "1", "length": "1024"},
                    {"path": str(preview_root / "album" / "cover.jpg"), "index": "2", "length": "23"},
                ],
            }
        assert method in {"aria2.forceRemove", "aria2.removeDownloadResult"}
        observed.append((method, params[0]))

    monkeypatch.setattr(Aria2Rpc, "start", fake_start)
    monkeypatch.setattr(Aria2Rpc, "call", fake_call)

    async def inspect():
        rpc = Aria2Rpc(tmp_path, port=16891)
        try:
            return await rpc.inspect_torrent(
                url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                timeout_seconds=2,
            )
        finally:
            await rpc.close()

    result = asyncio.run(inspect())
    assert result == {
        "file_count": 2,
        "files": [
            {"index": 1, "path": "album/track.flac", "name": "track.flac", "size_bytes": 1024},
            {"index": 2, "path": "album/cover.jpg", "name": "cover.jpg", "size_bytes": 23},
        ],
    }
    assert ("status", "files-gid") in observed
    for gid in ("metadata-gid", "files-gid"):
        assert ("aria2.forceRemove", gid) in observed
        assert ("aria2.removeDownloadResult", gid) in observed


def test_aria2_mixed_metadata_and_real_files_skip_only_metadata(tmp_path):
    from app.adapters.aria2 import _manifest_entries

    root = tmp_path.resolve()
    entries = _manifest_entries([
        {"path": _METADATA_PATH, "index": "1", "length": "0"},
        {"path": str(root / "folder" / "real.bin"), "index": "3", "length": "456"},
    ], root)
    assert entries == [
        {"index": 3, "path": "folder/real.bin", "name": "real.bin", "size_bytes": 456},
    ]


def test_aria2_metadata_filter_keeps_real_path_security_guards(tmp_path):
    import pytest
    from app.adapters.aria2 import _manifest_entries

    root = tmp_path / "preview"
    root.mkdir()
    for unsafe_path in (
        str(tmp_path / "outside.bin"),
        str(root / ".." / "outside.bin"),
        "[METADATA]not-an-infohash",
        "relative.bin",
    ):
        with pytest.raises(ValueError, match="outside its preview directory|invalid relative path"):
            _manifest_entries([
                {"path": _METADATA_PATH, "index": "1", "length": "0"},
                {"path": unsafe_path, "index": "2", "length": "1"},
            ], root)
    with pytest.raises(ValueError, match="too many files"):
        _manifest_entries([{"path": _METADATA_PATH}] * 10_001, root)


def test_aria2_bytes_manifest_still_uses_paused_torrent_path(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from app.adapters.aria2 import Aria2Rpc

    observed = []
    preview_root = None

    async def fake_start(self):
        pass

    async def fake_call(self, method, params, include_token=True):
        nonlocal preview_root
        if method == "aria2.addTorrent":
            assert params[0] == "dG9ycmVudA=="
            options = params[2]
            assert options["pause"] == "true"
            assert "bt-metadata-only" not in options
            preview_root = Path(options["dir"])
            return "uploaded-gid"
        if method == "aria2.tellStatus":
            assert params[0] == "uploaded-gid"
            return {
                "status": "paused",
                "bittorrent": {"info": {"name": "Uploaded torrent"}},
                "files": [
                    {"path": str(preview_root / "uploaded.bin"), "index": "1", "length": "99"},
                ],
            }
        assert method in {"aria2.forceRemove", "aria2.removeDownloadResult"}
        observed.append((method, params[0]))

    monkeypatch.setattr(Aria2Rpc, "start", fake_start)
    monkeypatch.setattr(Aria2Rpc, "call", fake_call)

    async def inspect():
        rpc = Aria2Rpc(tmp_path, port=16891)
        try:
            return await rpc.inspect_torrent(torrent_base64="dG9ycmVudA==", timeout_seconds=2)
        finally:
            await rpc.close()

    assert asyncio.run(inspect()) == {
        "file_count": 1,
        "files": [
            {"index": 1, "path": "uploaded.bin", "name": "uploaded.bin", "size_bytes": 99},
        ],
    }
    assert ("aria2.forceRemove", "uploaded-gid") in observed
    assert ("aria2.removeDownloadResult", "uploaded-gid") in observed
