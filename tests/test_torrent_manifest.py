from __future__ import annotations

import base64
import asyncio
from pathlib import Path

import pytest

from app.adapters.aria2 import Aria2Rpc, _manifest_entries

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


@pytest.mark.parametrize("layout", ["jobs", "preview", "upload", "outside", "traversal"])
def test_inspect_torrent_followon_layout_and_upload(tmp_path, layout):
    calls = []
    roots = []
    metadata_polls = 0

    async def run():
        rpc = Aria2Rpc(tmp_path, 16895)

        async def start():
            pass

        async def call(method, params):
            nonlocal metadata_polls
            calls.append((method, params))
            if method in {"aria2.addUri", "aria2.addTorrent"}:
                options = params[-1]
                roots.append(Path(options["dir"]))
                assert options["pause-metadata"] == "true"
                if layout == "upload":
                    assert options["pause"] == "true"
                else:
                    assert options.get("bt-metadata-only", "false") == "false"
                return "initial"
            if method == "aria2.tellStatus":
                if params[0] == "initial" and layout != "upload":
                    metadata_polls += 1
                    return {
                        "status": "complete", "bittorrent": {"info": {}},
                        "followedBy": ["child"] if metadata_polls > 1 else [],
                        "files": [{"index": "1", "length": "1", "path": "[METADATA]" + "a" * 40}],
                    }
                root = roots[0] if layout in {"preview", "upload"} else tmp_path / "jobs"
                if layout == "outside":
                    root = tmp_path / "outside"
                if layout == "traversal":
                    root = root / ".." / "outside"
                return {
                    "status": "paused", "bittorrent": {"info": {"name": "bundle"}},
                    "files": [{"index": "1", "length": "7", "path": str(root / "bundle" / "x.txt")}],
                }
            return "ok"

        rpc.start = start
        rpc.call = call
        try:
            kwargs = {"torrent_base64": "ZHVtbXk="} if layout == "upload" else {"url": "magnet:?xt=urn:btih:" + "a" * 40}
            return await rpc.inspect_torrent(**kwargs)
        finally:
            await rpc.client.aclose()

    if layout in {"outside", "traversal"}:
        with pytest.raises(ValueError, match="outside|invalid relative"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == {
            "file_count": 1,
            "files": [{"index": 1, "path": "bundle/x.txt", "name": "x.txt", "size_bytes": 7}],
        }
    assert not roots[0].exists()
    gids = {"initial"} if layout == "upload" else {"initial", "child"}
    for method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
        assert {params[0] for name, params in calls if name == method} == gids


@pytest.mark.parametrize("root_name", ["preview", "jobs"])
@pytest.mark.parametrize("escape", ["absolute", "traversal", "sibling"])
def test_manifest_entries_rejects_escape_from_each_allowed_root(tmp_path, root_name, escape):
    root = tmp_path / root_name
    paths = {
        "absolute": tmp_path / "outside" / "x.txt",
        "traversal": root / ".." / "outside" / "x.txt",
        "sibling": tmp_path / (root_name + "-other") / "x.txt",
    }
    with pytest.raises(ValueError, match="outside|invalid relative"):
        _manifest_entries(
            [{"index": "1", "length": "1", "path": str(paths[escape])}],
            tmp_path / "preview", fallback_root=tmp_path / "jobs",
        )


def test_manifest_entries_upload_does_not_accept_global_jobs_dir(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        _manifest_entries(
            [{"index": "1", "length": "1", "path": str(tmp_path / "jobs" / "x.txt")}],
            tmp_path / "preview",
        )


def test_manifest_metadata_child_discovery_is_used_by_download(tmp_path, monkeypatch):
    """The two paths must request the same follow-on discovery fields."""
    import inspect

    source = inspect.getsource(Aria2Rpc.download)
    assert '"followedBy"' in source
    assert '"bittorrent"' in source
    assert '"[METADATA]"' in source
