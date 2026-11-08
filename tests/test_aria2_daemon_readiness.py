"""Running aria2 process is not necessarily a functioning JSON-RPC daemon."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.adapters.aria2 import Aria2Rpc


def test_running_daemon_revalidates_rpc_before_reuse(tmp_path):
    adapter = Aria2Rpc(tmp_path, 16800)
    adapter.process = Mock()
    adapter.process.poll.return_value = None
    adapter.call = AsyncMock(return_value=["aria2.addUri"])

    asyncio.run(adapter.start())

    adapter.call.assert_awaited_once_with(
        "aria2.getGlobalOption", []
    )
    asyncio.run(adapter.client.aclose())


def test_running_but_rpc_dead_daemon_is_not_reported_ready(tmp_path):
    adapter = Aria2Rpc(tmp_path, 16800)
    adapter.process = Mock()
    adapter.process.poll.return_value = None
    adapter.call = AsyncMock(side_effect=RuntimeError("RPC unreachable"))

    with pytest.raises(RuntimeError, match="JSON-RPC is unavailable"):
        asyncio.run(adapter.start())
    asyncio.run(adapter.client.aclose())
