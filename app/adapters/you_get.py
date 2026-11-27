"""you-get fallback extractor: CLI options verified against upstream README."""

from __future__ import annotations

from contextlib import nullcontext
import re
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.adapters.common_cli import run_cli
from app.cookies import materialize_cookie_file
from app.engine_failures import EngineFailure, EngineFailureKind


def effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    values = {"format": "", "cookies_file": "", "no_proxy": False}
    values.update(global_settings or {})
    values.update(overrides or {})
    for field in ("format", "cookies_file"):
        text = values[field]
        if not isinstance(text, str) or len(text) > 256 or "\x00" in text:
            raise ValueError(f"Invalid you-get {field}")
    if not isinstance(values["no_proxy"], bool):
        raise ValueError("you-get no_proxy must be a boolean")
    cookie = values["cookies_file"]
    if cookie and (Path(cookie).name != cookie or cookie in {".", ".."} or "\\" in cookie):
        raise ValueError("Cookie filename must be inside the service cookies directory")
    return values


async def run_you_get(
    *,
    executable: str, url: str, workdir: Path,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    cookie_dir: Path,
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
    encryption_key: str | None = None,
) -> list[Path]:
    options = effective_settings(global_settings, overrides)
    workdir.mkdir(parents=True, exist_ok=True)
    argv = [executable, "--output-dir", str(workdir)]
    if options["format"]:
        argv.extend(["--format", options["format"]])
    if options["no_proxy"]:
        argv.append("--no-proxy")
    cookie_source: Path | None = None
    if options["cookies_file"]:
        path = (cookie_dir / options["cookies_file"]).resolve()
        if path.parent != cookie_dir.resolve() or not path.is_file():
            raise ValueError("Selected you-get cookie file was not found")
        cookie_source = path
    context = materialize_cookie_file(cookie_source, workdir, encryption_key) if cookie_source else nullcontext(None)
    with context as decrypted:
        if decrypted is not None:
            argv.extend(["--cookies", str(decrypted)])
        argv.append(url)
        return await run_cli(
            name="you-get", argv=argv, workdir=workdir,
            on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
        )


def permits_youtube_fallback(
    url: str, global_settings: dict[str, Any], overrides: dict[str, Any],
    failure: EngineFailure,
) -> bool:
    """Only explicit unsupported-extractor failures for single YouTube videos.

    No generic exceptions, auth/access/network errors, custom format/proxy
    settings, playlist links or lookalike domains can trigger another engine.
    The worker separately enforces the target engine's policy and capacity.
    """
    if failure.kind != EngineFailureKind.UNSUPPORTED:
        return False
    options = effective_settings(global_settings, overrides)
    if options["format"] or options["no_proxy"]:
        return False
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or parts.username or parts.password:
        return False
    host = parts.hostname
    if parts.port not in {None, 443 if parts.scheme == "https" else 80}:
        return False
    query = parse_qs(parts.query, keep_blank_values=True)
    if "list" in query or "p" in query:
        return False
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parts.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parts.path == "/watch":
            values = query.get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        elif parts.path.startswith(("/shorts/", "/live/")):
            video_id = parts.path.split("/")[2]
            if parts.path.rstrip("/") != "/" + parts.path.split("/")[1] + "/" + video_id:
                return False
        else:
            return False
    else:
        return False
    return re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is not None
