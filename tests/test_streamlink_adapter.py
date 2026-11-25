import asyncio
import sys
from pathlib import Path

import pytest

from app.adapters.common_cli import run_cli
from app.adapters.streamlink import streamlink_command


def test_streamlink_cli_uses_only_reviewed_options():
    argv = streamlink_command("streamlink", "https://example.com/live", "720p,best", Path("/data/recording.media"))
    assert argv[-3:] == ["--", "https://example.com/live", "720p,best"]
    assert "--no-config" in argv and "--no-plugin-cache" in argv
    assert "--no-plugin-sideloading" in argv and "--http-ignore-env" in argv
    assert argv[argv.index("--webbrowser")+1] == "no"
    assert argv[argv.index("--output")+1] == "/data/recording.media"
    for quality in ("--force", "best;bad", "", "720p, best", "best,best,best,best,best,best"):
        with pytest.raises(ValueError):
            streamlink_command("streamlink", "https://example.com/live", quality, Path("recording.media"))


@pytest.mark.parametrize("exit_code, expected", [(130, True), (7, False)])
def test_requested_stop_is_distinct_from_failure_and_cancel(tmp_path, exit_code, expected):
    script = tmp_path / "record.py"
    script.write_text(
        "import pathlib,signal,sys,time\n"
        "signal.signal(signal.SIGINT, lambda *_: sys.exit(int(sys.argv[1])))\n"
        "pathlib.Path('recording.media').write_bytes(b'media')\n"
        "while True: time.sleep(0.05)\n"
    )
    work = tmp_path / "work"
    logs = []
    async def progress(_): pass
    async def log(message): logs.append(message)
    async def stopped(): return (work / "recording.media").exists()
    async def cancelled(): return False
    async def exercise():
        return await asyncio.wait_for(run_cli(
            name="test recording", argv=[sys.executable, str(script), str(exit_code)],
            workdir=work, on_progress=progress, on_log=log, is_cancelled=cancelled,
            is_stopped=stopped,
        ), timeout=15)
    if expected:
        assert asyncio.run(exercise()) == [work / "recording.media"]
    else:
        with pytest.raises(RuntimeError, match="exited with status 7"):
            asyncio.run(exercise())
    assert logs.count("Stopping recording and finalizing media") == 1


def test_cancel_wins_even_if_stop_also_requested(tmp_path):
    async def no_op(_): pass
    async def yes(): return True
    async def exercise():
        await run_cli(name="test", argv=[sys.executable, "-c", "import time;time.sleep(60)"],
                      workdir=tmp_path, on_progress=no_op, on_log=no_op,
                      is_cancelled=yes, is_stopped=yes)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(exercise())
