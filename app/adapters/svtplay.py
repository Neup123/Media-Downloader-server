"""SVT Play / Nordic broadcaster adapter (svtplay-dl CLI).

See https://svtplay-dl.se/options/ for option spelling. The output directory is
service-managed and not user-overridable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.adapters.common_cli import run_cli


def effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = {
        "resolution": "",
        "quality": "",
        "subtitles": False,
        "all_subtitles": False,
        "all_episodes": False,
        "output_format": "mp4",
    }
    result.update(global_settings or {})
    result.update(overrides or {})
    for key in ("subtitles", "all_subtitles", "all_episodes"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be a boolean")
    resolution = result["resolution"]
    if not isinstance(resolution, str) or (resolution and (
        len(resolution) > 50 or not all(x.isdigit() and 100 <= int(x) <= 10000 for x in resolution.split(","))
    )):
        raise ValueError("resolution must be comma-separated numbers such as 480,720,1080")
    quality = result["quality"]
    if not isinstance(quality, str) or len(quality) > 80 or "\x00" in quality or quality.startswith("-"):
        raise ValueError("Invalid svtplay-dl quality")
    if not isinstance(result["output_format"], str) or result["output_format"] not in {"mp4", "mkv"}:
        raise ValueError("svtplay-dl output_format must be mp4 or mkv")
    return result


async def run_svtplay(
    *,
    executable: str, url: str, workdir: Path,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
) -> list[Path]:
    opts = effective_settings(global_settings, overrides)
    argv = [executable, "--config", "/dev/null", "--output", str(workdir), "--output-format", opts["output_format"]]
    if opts["resolution"]:
        argv.extend(["--resolution", opts["resolution"]])
    if opts["quality"]:
        argv.extend(["--quality", opts["quality"]])
    if opts["subtitles"]:
        argv.append("--subtitle")
    if opts["all_subtitles"]:
        argv.append("--all-subtitles")
    if opts["all_episodes"]:
        argv.append("--all-episodes")
    argv.append(url)
    return await run_cli(
        name="svtplay-dl", argv=argv, workdir=workdir,
        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
    )
