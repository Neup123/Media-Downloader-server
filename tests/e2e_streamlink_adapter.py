"""Real pinned Streamlink and FFprobe smoke, inside the built image only.

No external media/network; the image generates its own HLS fixture and serves
it over its isolated loopback. Also runs the explicit recording API and worker on the same local fixture.
Not an external service or native UI acceptance test.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import subprocess
import tempfile
import threading
from pathlib import Path

from app.adapters.streamlink import RECORDING_NAME, run_streamlink


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


async def exercise(root: Path, port: int):
    async def no_op(_): pass
    async def no(): return False
    async def capture(name: str, live: bool):
        work = root / name
        async def stopped():
            path = work / RECORDING_NAME
            return live and path.is_file() and path.stat().st_size > 0
        result = await asyncio.wait_for(run_streamlink(
            executable="streamlink", url=f"http://127.0.0.1:{port}/{name}.m3u8",
            quality="best", workdir=work, on_progress=no_op, on_log=no_op,
            is_cancelled=no, is_stopped=stopped,
        ), timeout=60)
        assert len(result) == 1 and result[0].suffix == ".ts"
        # Decode one frame to prove actual playable media, not just file bytes.
        subprocess.run(["ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe",
                        "-i", str(result[0]), "-frames:v", "1", "-f", "null", "-"],
                       check=True, timeout=15)
        print(f"Streamlink {name} recording parsed and decoded: PASS", flush=True)
    await capture("finite", False)
    await capture("live", True)
    await exercise_recording_api(root / "api", port)


async def exercise_recording_api(root: Path, port: int):
    from hashlib import sha256
    import httpx
    from app.config import Settings
    from app.main import create_app
    from app.worker import JobWorker

    settings = Settings(
        database_url=f"sqlite:///{root / 'jobs.db'}",
        api_token="fixture-recording-api-token",
        data_dir=root / "data",
        output_dir=root / "delivered",
        max_concurrent_downloads=1,
        aria2_rpc_port=16991,
        encryption_key=None,
        allow_private_targets=True,  # isolated loopback fixture only
        job_timeout_seconds=60,
    )
    app = create_app(settings, start_worker=False)
    factory = app.state.session_factory
    worker = JobWorker(factory, settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://local-api", headers={
                                         "X-API-Key": settings.api_token
                                     }) as client:
            policy = await client.put("/api/v1/engines/streamlink/policy", json={"enabled": True})
            assert policy.status_code == 200, policy.text
            for name in ("finite", "live"):
                response = await client.post("/api/v1/jobs", json={
                    "engine": "streamlink", "url": f"http://127.0.0.1:{port}/{name}.m3u8",
                })
                assert response.status_code == 202, response.text
                job_id = response.json()["id"]
                path = f"/api/v1/jobs/{job_id}"
                task = asyncio.create_task(worker._execute(job_id))
                try:
                    if name == "live":
                        async with asyncio.timeout(45):
                            while True:
                                job = (await client.get(path)).json()
                                media = settings.data_dir / "jobs" / job_id / RECORDING_NAME
                                if job["can_stop"] and media.is_file() and media.stat().st_size:
                                    stopped = await client.post(path + "/stop")
                                    assert stopped.status_code == 200, stopped.text
                                    assert stopped.json()["stop_requested"]
                                    break
                                assert not task.done(), job
                                await asyncio.sleep(0.1)
                    await asyncio.wait_for(task, 70)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                result = (await client.get(path)).json()
                assert result["status"] == "completed", result["error"]
                assert not result["stop_requested"] and not result["can_stop"]
                assert len(result["output_files"]) == 1
                item = result["output_files"][0]
                assert item["verified"] and item["filename"].endswith(".ts")
                target = Path(item["path"])
                source = settings.data_dir / "jobs" / job_id / item["filename"]
                assert sha256(target.read_bytes()).digest() == sha256(source.read_bytes()).digest()
                subprocess.run(["ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe",
                                "-i", str(target), "-frames:v", "1", "-f", "null", "-"],
                               check=True, timeout=15)
                print(f"Streamlink Core API {name}: verified local delivery and decoded frame PASS", flush=True)
    finally:
        app.state.engine.dispose()


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10:d=8",
            "-c:v", "libx264", "-g", "10", "-sc_threshold", "0", "-f", "hls",
            "-hls_time", "1", "-hls_list_size", "0", str(root / "finite.m3u8"),
        ], check=True, timeout=30)
        playlist = (root / "finite.m3u8").read_text()
        assert "#EXT-X-ENDLIST" in playlist
        (root / "live.m3u8").write_text(playlist.replace("#EXT-X-ENDLIST", ""))
        handler = functools.partial(QuietHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            asyncio.run(exercise(root, server.server_port))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
