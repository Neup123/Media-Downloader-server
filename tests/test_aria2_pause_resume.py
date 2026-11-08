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
    assert all(call.args[0]["state"] == "downloading" for call in on_progress.await_args_list)
    await adapter.close()
