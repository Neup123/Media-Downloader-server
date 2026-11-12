"""Metadata completion must never finish a follow-on payload job."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.adapters.aria2 import Aria2Rpc


def _status(state, path, *, total="20", done="20", children=None, selected="true"):
    return {
        "status": state, "totalLength": total, "completedLength": done,
        "downloadSpeed": "4", "followedBy": children or [],
        "files": [{"path": str(path), "selected": selected}],
    }


async def _run(tmp_path, monkeypatch, *, url="magnet:?xt=urn:btih:abc", delay=False,
               error=False, cancel=False, wrong_dir=False, direct=False, invisible=False, unselected=False):
    root = tmp_path / "job"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"payload")
    metadata = Path("[METADATA]" + "a" * 40)
    rpc = Aria2Rpc(tmp_path, 16888)
    rpc.start = AsyncMock()
    monkeypatch.setattr("app.adapters.aria2.asyncio.sleep", AsyncMock())
    seen = []
    progress = []
    polls = {"parent": 0, "child": 0}
    child_ready = False
    invisible_attempts = 0

    async def call(method, params):
        nonlocal child_ready, invisible_attempts
        seen.append((method, params))
        if method == "aria2.addUri":
            assert params[1]["dir"] == str(root.resolve())
            assert ("pause-metadata" in params[1]) is not direct
            return "parent"
        if method == "aria2.tellStatus":
            target = params[0]
            if target == "parent":
                polls["parent"] += 1
                if direct:
                    return _status("complete", payload)
                if delay and polls["parent"] == 1:
                    return _status("complete", metadata)
                child_ready = True
                return _status("complete", metadata, children=["child"])
            if invisible and invisible_attempts == 0:
                invisible_attempts += 1
                raise RuntimeError("GID not found yet")
            polls["child"] += 1
            if not child_ready:
                raise RuntimeError("GID not found")
            if error:
                return {**_status("error", payload), "errorCode": "3", "errorMessage": "disk"}
            if polls["child"] == 1:
                return _status("paused", payload, total="100", done="10")
            if cancel:
                return _status("active", payload, total="100", done="50")
            finished = _status("complete", payload, total="100", done="100")
            if unselected:
                other = root / "unselected.bin"
                other.write_bytes(b"old")
                finished["files"].append({"path": str(other), "selected": "false"})
            return finished
        if method == "aria2.getOption":
            return {"dir": str(tmp_path / "outside" if wrong_dir else root)}
        if method in {"aria2.changeOption", "aria2.unpause", "aria2.forceRemove",
                      "aria2.removeDownloadResult"}:
            return "OK"
        raise AssertionError(method)

    rpc.call = AsyncMock(side_effect=call)
    on_progress = AsyncMock(side_effect=lambda event: progress.append(event))
    on_log = AsyncMock()
    is_cancelled = AsyncMock(side_effect=(lambda: polls["child"] > 1) if cancel else (lambda: False))
    try:
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await rpc.download(url=url, job_id="j", workdir=root,
                                   global_settings={}, overrides={}, on_progress=on_progress,
                                   on_log=on_log, is_cancelled=is_cancelled)
        elif error:
            with pytest.raises(RuntimeError, match="aria2 download failed"):
                await rpc.download(url=url, job_id="j", workdir=root,
                                   global_settings={}, overrides={}, on_progress=on_progress,
                                   on_log=on_log, is_cancelled=is_cancelled)
        elif wrong_dir:
            with pytest.raises(RuntimeError, match="outside the job directory"):
                await rpc.download(url=url, job_id="j", workdir=root,
                                   global_settings={}, overrides={}, on_progress=on_progress,
                                   on_log=on_log, is_cancelled=is_cancelled)
        else:
            result = await rpc.download(url=url, job_id="j", workdir=root,
                                        global_settings={}, overrides={}, on_progress=on_progress,
                                        on_log=on_log, is_cancelled=is_cancelled)
            assert result == [payload]
        return seen, progress
    finally:
        await rpc.close()


@pytest.mark.parametrize("delay,invisible", [(False, False), (True, False), (False, True)])
def test_metadata_to_payload_and_late_child(tmp_path, monkeypatch, delay, invisible):
    calls, events = asyncio.run(_run(tmp_path, monkeypatch, delay=delay, invisible=invisible))
    assert any(method == "aria2.tellStatus" and params[0] == "child" for method, params in calls)
    assert events[-1]["filename"] == "payload.bin"
    assert events[-1]["percent"] == 100.0
    assert all(event["filename"] != "[METADATA]" + "a" * 40 for event in events)
    assert not any(event["percent"] == 100.0 for event in events[:-1])
    assert not any(method == "aria2.forceRemove" for method, _ in calls)


@pytest.mark.parametrize("mode", ["error", "cancel", "wrong_dir"])
def test_failure_cleans_parent_and_child(tmp_path, monkeypatch, mode):
    calls, _ = asyncio.run(_run(tmp_path, monkeypatch, **{mode: True}))
    for method in ("aria2.forceRemove", "aria2.removeDownloadResult"):
        assert {params[0] for name, params in calls if name == method} == {"parent", "child"}


@pytest.mark.parametrize("url", ["https://example.com/file.bin", "ftp://example.com/file.bin"])
def test_direct_protocol_does_not_wait_for_followon(tmp_path, monkeypatch, url):
    calls, events = asyncio.run(_run(tmp_path, monkeypatch, url=url, direct=True))
    assert events[-1]["percent"] == 100.0
    assert not any(method == "aria2.getOption" for method, _ in calls)


def test_remote_torrent_uses_metadata_gate(tmp_path, monkeypatch):
    calls, _ = asyncio.run(_run(tmp_path, monkeypatch, url="https://example.com/a.torrent"))
    add = next(params for method, params in calls if method == "aria2.addUri")
    assert add[1]["pause-metadata"] == "true"


def test_unselected_existing_files_are_not_delivered(tmp_path, monkeypatch):
    calls, events = asyncio.run(_run(tmp_path, monkeypatch, unselected=True))
    assert events[-1]["filename"] == "payload.bin"
    assert any(method == "aria2.getOption" for method, _ in calls)
