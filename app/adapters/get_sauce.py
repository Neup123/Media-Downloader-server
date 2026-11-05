"""Validated get-sauce P2 CLI wrapper with a service-managed output root."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from app.adapters.common_cli import run_cli

DEFAULTS = {
    "stream_index": 0, "amount": 0, "workers": 4, "merge": "default",
    "pages": "", "subdirectory": False, "timeout_minutes": 15,
}


def effective_settings(global_settings: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    values = {**DEFAULTS, **(global_settings or {}), **(overrides or {})}
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown get-sauce setting(s): {', '.join(sorted(unknown))}")
    for field, lower, upper in (
        ("stream_index", 0, 10000), ("amount", 0, 100000),
        ("workers", 1, 32), ("timeout_minutes", 1, 120),
    ):
        number = values[field]
        if type(number) is not int or not lower <= number <= upper:
            raise ValueError(f"get-sauce {field} must be an integer between {lower} and {upper}")
    if not isinstance(values["merge"], str) or values["merge"] not in {"default", "none", "cbz"}:
        raise ValueError("get-sauce merge must be default, none or cbz")
    if not isinstance(values["subdirectory"], bool):
        raise ValueError("get-sauce subdirectory must be a boolean")
    pages = values["pages"]
    if not isinstance(pages, str) or (pages and not re.fullmatch(r"[1-9]\\d{0,5}(?:-[1-9]\\d{0,5})?(?:,[1-9]\\d{0,5}(?:-[1-9]\\d{0,5})?)*", pages)):
        raise ValueError("get-sauce pages must be comma-separated positive indices and ranges")
    return values


async def run_get_sauce(
    *,
    executable: str, url: str, workdir: Path,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
) -> list[Path]:
    values = effective_settings(global_settings, overrides)
    argv = [
        executable, "-O", str(workdir), "-s", str(values["stream_index"]),
        "-w", str(values["workers"]), "-T", str(values["timeout_minutes"]),
    ]
    if values["amount"]:
        argv.extend(["-a", str(values["amount"])])
    if values["merge"] != "default":
        argv.extend(["-m", values["merge"]])
    if values["pages"]:
        argv.extend(["-p", values["pages"]])
    if values["subdirectory"]:
        argv.append("-S")
    argv.append(url)
    return await run_cli(
        name="get-sauce", argv=argv, workdir=workdir,
        on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
    )
