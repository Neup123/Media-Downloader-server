"""aria2 pause/resume is a live state, not a terminal failure."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.adapters.aria2 import Aria2Rpc


def test_paused_aria2_is_reported_and_can_complete_after_resume(tmp_path, monkeypatch):
    asyncio.run(_paused_then_completed(tmp_path, monkeypatch))


async def _paused_then_completed(tmp_path, monkeypatch):
    workdir = tmp_path / "media"
    workdir.mkdir()
    output = workdir / "complete.bin"
    output.write_bytes(b"verified-fixture")
    adapter = Aria2Rpc(tmp_path, 16800)
    adapter.start = AsyncMock()
    paused = {
        "status": "paused", "totalLength": "16", "completedLength": "5",
        "downloadSpeed": "0", "files": [{"path": str(output)}],
    }
    complete = {
        "status": "complete", "totalLength": "16", "completedLength": "16",
        "downloadSpeed": "0", "files": [{"path": str(output)}],
    }
    adapter.call = AsyncMock(side_effect=["gid", paused, complete])
    on_progress, on_log, is_cancelled = AsyncMock(), AsyncMock(), AsyncMock(return_value=False)
    monkeypatch.setattr("app.adapters.aria2.asyncio.sleep", AsyncMock())

    result = await adapter.download(
        url="https://example.com/file", job_id="test", workdir=workdir,
        global_settings={}, overrides={}, on_progress=on_progress,
        on_log=on_log, is_cancelled=is_cancelled,
    )
    assert result == [output]
    assert on_progress.await_args_list[0].args[0]["engine_status"] == "paused"
    assert on_progress.await_args_list[1].args[0]["engine_status"] == "complete"
    assert [call.args[0]["state"] for call in on_progress.await_args_list] == ["paused", "downloading"]
    assert adapter.can_control("test") is False
    await adapter.close()


def test_pause_and_resume_control_same_gid_without_adding_a_new_uri(tmp_path):
    async def exercise():
        adapter = Aria2Rpc(tmp_path, 16801)
        adapter._job_gids["job"] = "gid-123"
        adapter.call = AsyncMock(return_value="OK")
        assert adapter.can_control("job")
        await adapter.pause("job")
        await adapter.resume("job")
        assert [(call.args[0], call.args[1]) for call in adapter.call.await_args_list] == [
            ("aria2.pause", ["gid-123"]), ("aria2.unpause", ["gid-123"]),
        ]
        with pytest.raises(RuntimeError, match="no active aria2 transfer"):
            await adapter.pause("different-job")
        await adapter.close()
    asyncio.run(exercise())


def test_pause_resume_targets_payload_after_followon_transition(tmp_path, monkeypatch):
    async def exercise():
        workdir = tmp_path / "job"
        workdir.mkdir()
        output = workdir / "payload.bin"
        output.write_bytes(b"done")
        rpc = Aria2Rpc(tmp_path, 16802)
        rpc.start = AsyncMock()
        observed = []
        parent = {
            "status": "complete", "followedBy": ["payload"],
            "files": [{"path": "[METADATA]abc"}],
        }
        paused = {
            "status": "paused", "totalLength": "4", "completedLength": "0",
            "files": [{"path": str(output), "selected": "true"}],
        }
        complete = {**paused, "status": "complete", "completedLength": "4"}
        polls = 0

        async def call(method, params):
            nonlocal polls
            observed.append((method, params))
            if method == "aria2.addUri":
                return "metadata"
            if method == "aria2.tellStatus":
                if params[0] == "metadata":
                    return parent
                polls += 1
                return paused if polls == 1 else complete
            if method == "aria2.getOption":
                return {"dir": str(workdir.resolve())}
            if method in {"aria2.unpause", "aria2.pause"}:
                return "OK"
            raise AssertionError(method)

        rpc.call = AsyncMock(side_effect=call)
        async def progress(event):
            if event["engine_status"] == "complete" and rpc.can_control("j"):
                assert rpc._job_gids["j"] == "payload"
                await rpc.pause("j")
                await rpc.resume("j")

        monkeypatch.setattr("app.adapters.aria2.asyncio.sleep", AsyncMock())
        try:
            assert await rpc.download(
                url="magnet:?xt=urn:btih:abc", job_id="j", workdir=workdir,
                global_settings={}, overrides={}, on_progress=progress,
                on_log=AsyncMock(), is_cancelled=AsyncMock(return_value=False),
            ) == [output]
            assert ("aria2.pause", ["payload"]) in observed
            assert ("aria2.unpause", ["payload"]) in observed
        finally:
            await rpc.close()
    asyncio.run(exercise())
