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
