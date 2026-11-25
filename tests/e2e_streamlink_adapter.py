"""Real pinned Streamlink and FFprobe smoke, inside the built image only.

No external media/network; the image generates its own HLS fixture and serves
it over its isolated loopback. Not an API/UI or real service acceptance test.
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
