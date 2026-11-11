"""pyLoad-ng REST bridge for a separately started, authenticated pyLoad service.

Upstream pyLoad is a persistent daemon, NOT a CLI downloader. To avoid
starting an unauthenticated web server inside the Core worker, the owner must
configure a reachable pyLoad API URL, API key and shared file directory.
pyLoad's storage directory must be mounted at the configured shared root.
"""
from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.captcha import CaptchaWaiting
from app.adapters.ddddocr import run_ddddocr


def validate_pyload_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("pyLoad API endpoint must be an HTTP(S) base URL without credentials or paths")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1", "localhost", "pyload",
    }:
        raise ValueError("Unencrypted pyLoad API is allowed only on the local private service")
    return value.rstrip("/")


def _finished_output_files(package: dict[str, Any], workdir: Path, shared_root: Path) -> list[Path]:
    links = package.get("links")
    if not isinstance(links, list) or not links:
        raise RuntimeError("pyLoad returned no completed package links")
    source_root = shared_root.resolve()
    package_folder = package.get("folder", "")
    if not isinstance(package_folder, str) or ".." in Path(package_folder).parts:
        raise RuntimeError("pyLoad returned unsafe package folder")
    results = []
    for link in links:
        name = link.get("name") if isinstance(link, dict) else None
        if not isinstance(name, str) or Path(name).name != name or not name:
            raise RuntimeError("pyLoad returned unsafe downloaded filename")
        source = (source_root / package_folder / name).resolve()
        if not source.is_relative_to(source_root) or not source.is_file() or source.is_symlink():
            raise RuntimeError("pyLoad output is missing or outside configured shared storage")
        target = workdir / name
        if target.exists() or target.is_symlink():
            raise RuntimeError("Refusing to overwrite existing pyLoad destination file")
        shutil.copy2(source, target)
        results.append(target.resolve())
    return results


async def run_pyload(
    *,
    url: str, workdir: Path, api_url: str, api_key: str, shared_root: Path,
    run_ddddocr_opt_in: bool,
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Path]:
    if global_settings or overrides:
        raise ValueError("pyLoad job settings are not supported; configure the daemon separately")
    base_url = validate_pyload_endpoint(api_url)
    if not api_key.startswith("pl_"):
        raise ValueError("pyLoad REST API key must be an owner-generated pl_ key")
    workdir.mkdir(parents=True, exist_ok=True)
    headers = {"X-API-Key": api_key}
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, trust_env=False,
        transport=transport, headers=headers,
    ) as client:
        async def call(method: str, route: str, **kwargs):
            response = await client.request(method, base_url + "/api/" + route, **kwargs)
            response.raise_for_status()
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                raise RuntimeError("pyLoad API reported an error")
            return body

        package_id = await call("POST", "add_package", json={
            "name": "Core job", "links": [url], "dest": 1,
        })
        if type(package_id) is not int or package_id <= 0:
            raise RuntimeError("pyLoad failed to enqueue a package")
        await on_log("pyLoad package accepted by authenticated API")
        try:
            while True:
                if await is_cancelled():
                    raise asyncio.CancelledError
                if await call("GET", "is_captcha_waiting"):
                    task = await call("GET", "get_captcha_task")
                    data = task.get("data") if isinstance(task, dict) else None
                    task_id = task.get("tid") if isinstance(task, dict) else None
                    image_data = data.get("image") if isinstance(data, dict) else None
                    if isinstance(image_data, str) and isinstance(task_id, int):
                        try:
                            image_bytes = base64.b64decode(image_data, validate=True)
                            result = await run_ddddocr(
                                image_bytes, run_carefully=run_ddddocr_opt_in,
                            )
                        except (ValueError, CaptchaWaiting):
                            pass
                        else:
                            await call("POST", "set_captcha_result", params={
                                "tid": task_id, "result": result,
                            })
                            await on_log("pyLoad captcha handled by local solver")
                            continue
                    # Manual review remains a nonterminal state signalled in SSE.
                    raise CaptchaWaiting("pyLoad captcha awaiting owner action")
                package = await call("GET", "get_package_data", params={
                    "package_id": package_id,
                })
                if not isinstance(package, dict):
                    raise RuntimeError("pyLoad package status response is invalid")
                files = package.get("links") or []
                finished = [x for x in files if x.get("status") == 0]
                failures = [x for x in files if x.get("status") in {8, 9, 14}]
                if failures:
                    raise RuntimeError("pyLoad package contains a failed or aborted transfer")
                if files and len(finished) == len(files):
                    return _finished_output_files(package, workdir, shared_root)
                await on_progress({
                    "state": "downloading",
                    "downloaded_bytes": package.get("sizedone"),
                    "total_bytes": package.get("sizetotal"),
                    "percent": None,
                })
                await asyncio.sleep(2)
        finally:
            # Do not delete potentially unfinished packages on captcha wait;
            # operator action may be required in the pyLoad interface.
            pass
