"""you-get fallback extractor: CLI options verified against upstream README."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from app.adapters.common_cli import run_cli


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
) -> list[Path]:
    options = effective_settings(global_settings, overrides)
    argv = [executable, "--output-dir", str(workdir)]
    if options["format"]:
        argv.extend(["--format", options["format"]])
    if options["no_proxy"]:
        argv.append("--no-proxy")
    if options["cookies_file"]:
        path = (cookie_dir / options["cookies_file"]).resolve()
        if path.parent != cookie_dir.resolve() or not path.is_file():
            raise ValueError("Selected you-get cookie file was not found")
        argv.extend(["--cookies", str(path)])
    argv.append(url)
    return await run_cli(
        name="you-get", argv=argv, workdir=workdir,
        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
    )
