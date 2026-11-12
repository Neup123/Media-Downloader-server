"""musicdl 2.14 playlist link adapter.

Upstream implements URL playlist parsing with '-p', and source-specific '-m'.
Only documented noninteractive clients are selected; arbitrary CLI args or
credential literals are not accepted in job overrides.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.adapters.common_cli import run_cli


SOURCES = {
    "open.spotify.com": "SpotifyMusicClient",
    "soundcloud.com": "SoundCloudMusicClient",
    "www.soundcloud.com": "SoundCloudMusicClient",
    "audius.co": "AudiusMusicClient",
    "ccmixter.org": "CCMixterMusicClient",
    "www.jamendo.com": "JamendoMusicClient",
    "www.kuwo.cn": "KuwoMusicClient",
    "music.163.com": "NeteaseMusicClient",
    "y.qq.com": "QQMusicClient",
    "www.deezer.com": "DeezerMusicClient",
}


def music_client(url: str, override: str = "") -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host not in SOURCES:
        raise ValueError("musicdl link host is not supported by this adapter")
    derived = SOURCES[host]
    if override and override != derived:
        raise ValueError("musicdl source must match the link's supported client")
    return derived


async def run_musicdl(
    *, executable: str, url: str, workdir: Path,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
) -> list[Path]:
    values = {**(global_settings or {}), **(overrides or {})}
    if set(values) - {"source"}:
        raise ValueError("Unknown musicdl option")
    source = values.get("source", "")
    if not isinstance(source, str):
        raise ValueError("musicdl source must be a client name")
    source = music_client(url, source)
    # Upstream musicdl CLI documents -p for playlist links and -m for client.
    # No shell is used and the output root is subprocess cwd.
    return await run_cli(
        name="musicdl",
        argv=[executable, "-p", url, "-m", source],
        workdir=workdir, on_progress=on_progress,
        on_log=on_log, is_cancelled=is_cancelled,
    )
