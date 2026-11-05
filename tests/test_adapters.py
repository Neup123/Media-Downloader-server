from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.adapters.aria2 import Aria2Rpc, _effective_options
from app.adapters.gallery_dl import run_gallery_dl
from app.adapters.yt_dlp import PROGRESS_TEMPLATE, run_ytdlp


def test_yt_dlp_adapter_uses_argument_array_and_emits_progress(tmp_path: Path):
    executable = tmp_path / "fake-yt-dlp"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "print('__DL_PROGRESS__1024\\t2048\\tNA\\t512\\t2\\tclip.mp4', flush=True)\n"
        "output = pathlib.Path.cwd() / 'clip.mp4'\n"
        "output.write_bytes(b'content')\n"
        "print('__DL_FILE__' + str(output), flush=True)\n"
    )
    executable.chmod(0o755)
    events = []
    logs = []

    async def on_progress(event): events.append(event)
    async def on_log(message): logs.append(message)
    async def not_cancelled(): return False

    files = asyncio.run(run_ytdlp(
        executable=str(executable),
        url="https://example.com/video",
        workdir=tmp_path / "work",
        global_settings={"format": "best"},
        overrides={},
        cookie_dir=tmp_path / "cookies",
        on_progress=on_progress,
        on_log=on_log,
        is_cancelled=not_cancelled,
    ))
    assert files == [tmp_path / "work" / "clip.mp4"]
    assert events[0]["percent"] == 50.0
    assert "Starting yt-dlp" in logs[0]
    assert "https://example.com/video" not in " ".join(logs)
    assert "%(progress.downloaded_bytes)s\t" in PROGRESS_TEMPLATE


def test_aria2_rpc_secret_is_sent_only_in_authorization_parameter(tmp_path: Path):
    seen = []

    def handler(request: httpx.Request):
        payload = __import__("json").loads(request.content)
        seen.append(payload)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": "ok"})

    async def run():
        rpc = Aria2Rpc(tmp_path, 16892)
        rpc.secret = "generated-secret"
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await rpc.call("aria2.addUri", [["https://example.com/file"], {"dir": str(tmp_path)}])
        await rpc.client.aclose()
        return result

    assert asyncio.run(run()) == "ok"
    assert seen[0]["params"][0] == "token:generated-secret"
    assert seen[0]["method"] == "aria2.addUri"


def test_aria2_settings_allowlist_rejects_rpc_exposure():
    assert _effective_options({}, {})["continue"] == "true"
    try:
        _effective_options({}, {"options": {"rpc-listen-all": "true"}})
    except ValueError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("RPC exposure option must not be user-overridable")


def test_gallery_dl_adapter_uses_managed_output_and_discovers_downloads(tmp_path: Path):
    executable = tmp_path / "fake-gallery-dl"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "print('16%   1.01MB 337.19kB/s', flush=True)\n"
        "output = pathlib.Path.cwd() / 'sample.jpg'\n"
        "output.write_bytes(b'image-bytes')\n"
        "print(str(output), flush=True)\n"
    )
    executable.chmod(0o755)
    events = []
    logs = []

    async def on_progress(event): events.append(event)
    async def on_log(message): logs.append(message)
    async def not_cancelled(): return False

    files = asyncio.run(run_gallery_dl(
        executable=str(executable),
        url="https://example.com/gallery",
        workdir=tmp_path / "work",
        global_settings={"options": {"extractor.generic.referer": "https://example.com/"}},
        overrides={},
        cookie_dir=tmp_path / "cookies",
        on_progress=on_progress,
        on_log=on_log,
        is_cancelled=not_cancelled,
    ))
    assert files == [tmp_path / "work" / "sample.jpg"]
    assert events[0]["percent"] == 16.0
    assert events[0]["total_bytes"] is None
    assert "Starting gallery-dl" in logs[0]


def test_gallery_dl_rejects_service_managed_output_options():
    from app.adapters.gallery_dl import _effective_settings

    try:
        _effective_settings({}, {"options": {"output.directory": "/tmp/escape"}})
    except ValueError as exc:
        assert "managed by the service" in str(exc)
    else:
        raise AssertionError("gallery-dl output directory must remain service-managed")
