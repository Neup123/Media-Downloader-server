"""Regression: a magnet's synthetic metadata is not completed output."""
from __future__ import annotations

import asyncio

import pytest

from app.adapters.aria2 import Aria2Rpc, Aria2MetadataPending, _is_aria2_metadata_path

METADATA = "[METADATA]0123456789abcdef0123456789abcdef01234567"


def test_btih_metadata_marker_is_strict():
    assert _is_aria2_metadata_path(METADATA)
    assert not _is_aria2_metadata_path("[METADATA]not-a-hash")
    assert not _is_aria2_metadata_path("file.bin")
    assert not _is_aria2_metadata_path(None)


def test_metadata_completion_follows_payload_gid(tmp_path, monkeypatch):
    target = tmp_path / "album.bin"
    target.write_bytes(b"payload")
    statuses = []

    async def start(self):
        pass

    async def call(self, method, params, include_token=True):
        if method == "aria2.addUri":
            return "metadata-gid"
        assert method == "aria2.tellStatus"
        gid = params[0]
        statuses.append(gid)
        if gid == "metadata-gid":
            return {
                "status": "complete", "totalLength": "19961",
                "completedLength": "19961", "downloadSpeed": "0",
                "files": [{"path": METADATA, "length": "19961"}],
                "followedBy": ["payload-gid"],
            }
        assert gid == "payload-gid"
        return {
            "status": "complete", "totalLength": "7",
            "completedLength": "7", "downloadSpeed": "0",
            "files": [{"path": str(target), "length": "7"}],
        }

    monkeypatch.setattr(Aria2Rpc, "start", start)
    monkeypatch.setattr(Aria2Rpc, "call", call)
    updates, logs = [], []

    async def on_progress(value):
        updates.append(value)

    async def on_log(value):
        logs.append(value)

    async def not_cancelled():
        return False

    async def exercise():
        rpc = Aria2Rpc(tmp_path, 16891)
        try:
            return await rpc.download(
                url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                job_id="job", workdir=tmp_path, global_settings={}, overrides={},
                on_progress=on_progress, on_log=on_log, is_cancelled=not_cancelled,
            )
        finally:
            await rpc.client.aclose()

    assert asyncio.run(exercise()) == [target.resolve()]
    assert statuses == ["metadata-gid", "payload-gid"]
    assert updates[0]["state"] == "extracting"
    assert updates[0]["percent"] is None
    assert "following aria2 payload" in " ".join(logs)


def test_metadata_only_without_follow_on_is_retryable(tmp_path, monkeypatch):
    polls = []

    async def start(self):
        pass

    async def call(self, method, params, include_token=True):
        if method == "aria2.addUri":
            return "metadata-gid"
        polls.append(method)
        return {
            "status": "complete", "totalLength": "19961",
            "completedLength": "19961", "downloadSpeed": "0",
            "files": [{"path": METADATA, "length": "19961"}],
            "followedBy": [],
        }

    async def no_wait(_):
        pass

    monkeypatch.setattr(Aria2Rpc, "start", start)
    monkeypatch.setattr(Aria2Rpc, "call", call)
    monkeypatch.setattr("app.adapters.aria2.asyncio.sleep", no_wait)

    async def on_progress(_):
        pass

    async def on_log(_):
        pass

    async def not_cancelled():
        return False

    async def exercise():
        rpc = Aria2Rpc(tmp_path, 16891)
        try:
            with pytest.raises(Aria2MetadataPending, match="retryable"):
                await rpc.download(
                    url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
                    job_id="job", workdir=tmp_path, global_settings={}, overrides={},
                    on_progress=on_progress, on_log=on_log, is_cancelled=not_cancelled,
                )
        finally:
            await rpc.client.aclose()

    asyncio.run(exercise())
    assert polls == ["aria2.tellStatus"] * 5
