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



def test_aria2_select_file_reaches_rpc_options_and_rejects_invalid_values():
    import pytest

    for selection in ("3", "4,6", "1,2,3,4,5,6", "003"):
        assert _effective_options({}, {"options": {"select-file": selection}})["select-file"] == selection

    for invalid in ("", "0", "1,0", "-1", "1,,2", "1-3", " 1", 3, None):
        with pytest.raises(ValueError, match="aria2c select-file"):
            _effective_options({}, {"options": {"select-file": invalid}})


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



def test_aria2_daemon_and_overrides_force_zero_seed_time(tmp_path: Path, monkeypatch):
    import subprocess
    from app.adapters import aria2 as module

    launched = []

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        launched.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    async def run():
        rpc = Aria2Rpc(tmp_path, 16894)

        async def call(method, params):
            assert method == "aria2.getGlobalOption"
            return {"seed-time": "0"}

        rpc.call = call
        await rpc.start()
        await rpc.client.aclose()

    asyncio.run(run())
    assert len(launched) == 1
    assert "--seed-time=0" in launched[0][0]
    assert "--rpc-save-upload-metadata=false" in launched[0][0]
    assert launched[0][1]["env"] is not None
    assert _effective_options({"options": {"seed-time": "120"}}, {"options": {"seed-time": "999"}})["seed-time"] == "0"


def test_torrent_magnet_manifest_only_reads_metadata_and_cleans_up(tmp_path: Path):
    calls = []
    workdirs = []

    async def run():
        rpc = Aria2Rpc(tmp_path, 16895)

        async def start():
            return None

        async def call(method, params):
            calls.append((method, params))
            if method == "aria2.addUri":
                options = params[1]
                workdirs.append(Path(options["dir"]))
                assert options["seed-time"] == "0"
                assert "bt-metadata-only" not in options
                assert options["pause-metadata"] == "true"
                assert options["bt-save-metadata"] == "false"
                return "magnet-gid"
            if method == "aria2.tellStatus":
                assert params[0] == "magnet-gid"
                return {
                    "status": "complete", "bittorrent": {"info": {"name": "bundle"}},
                    "files": [
                        {"index": "1", "length": "7", "path": str(workdirs[0] / "bundle" / "x.txt")},
                        {"index": "2", "length": "0", "path": str(workdirs[0] / "bundle" / "sub" / "empty.txt")},
                    ],
                }
            return "ok"

        rpc.start = start
        rpc.call = call
        result = await rpc.inspect_torrent(url="magnet:?xt=urn:btih:0123456789abcdef")
        await rpc.client.aclose()
        return result

    result = asyncio.run(run())
    assert result["file_count"] == 2
    assert result["files"][0] == {
        "index": 1, "path": "bundle/x.txt", "name": "x.txt", "size_bytes": 7,
    }
    assert not workdirs[0].exists()
    assert [method for method, _ in calls] == [
        "aria2.addUri", "aria2.tellStatus", "aria2.forceRemove", "aria2.removeDownloadResult",
    ]


def test_uploaded_torrent_manifest_is_paused_and_removed_on_failure(tmp_path: Path):
    import pytest

    calls = []

    async def run():
        rpc = Aria2Rpc(tmp_path, 16896)

        async def start():
            return None

        async def call(method, params):
            calls.append((method, params))
            if method == "aria2.addTorrent":
                assert params[0] == "ZHVtbXk="
                assert params[2]["pause"] == "true"
                assert params[2]["pause-metadata"] == "true"
                return "torrent-gid"
            if method == "aria2.tellStatus":
                return {"status": "error", "errorCode": "1", "files": []}
            return "ok"

        rpc.start = start
        rpc.call = call
        with pytest.raises(RuntimeError, match="could not retrieve"):
            await rpc.inspect_torrent(torrent_base64="ZHVtbXk=")
        await rpc.client.aclose()

    asyncio.run(run())
    assert [method for method, _ in calls] == [
        "aria2.addTorrent", "aria2.tellStatus", "aria2.forceRemove", "aria2.removeDownloadResult",
    ]


def test_torrent_manifest_rejects_paths_outside_ephemeral_workspace(tmp_path: Path):
    import pytest
    from app.adapters.aria2 import _manifest_entries

    with pytest.raises(ValueError, match="outside"):
        _manifest_entries([{"index": "1", "length": "1", "path": "/etc/passwd"}], tmp_path)
