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
from app.failure_messages import UserFacingFailure


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
    existing_package_id: int | None = None,
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
            try:
                response = await client.request(method, base_url + "/api/" + route, **kwargs)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                await on_log(f"pyLoad request failed: {exc}")
                raise UserFacingFailure(
                    "The pyLoad service is unreachable at the configured address.",
                    "Check that the pyLoad daemon is running and the address in settings is correct.",
                ) from exc
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                detail = body["error"]
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("detail") or detail.get("error") or detail
                text = str(detail)[:500]
                await on_log(f"pyLoad API reported an error: {text}")
                raise RuntimeError(f"pyLoad reported an error: {text}")
            return body

        package_id = existing_package_id
        if package_id is None:
            try:
                package_id = await call("POST", "add_package", json={
                    "name": "Core job", "links": [url], "dest": 1,
                })
            except UserFacingFailure:
                raise
            except RuntimeError as exc:
                raise UserFacingFailure(
                    f"pyLoad could not start this download (enqueue failed): {exc}.",
                    "Retry; if it repeats, check the pyLoad daemon.",
                ) from exc
        if type(package_id) is not int or package_id <= 0:
            raise UserFacingFailure("pyLoad could not start this download (enqueue failed).",
                                    "Retry; if it repeats, check the pyLoad daemon.")
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
                                on_log=on_log,
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
                    raise CaptchaWaiting("pyLoad captcha awaiting owner action",
                                         task_id=task_id if isinstance(task_id, int) else None,
                                         package_id=package_id)
                package = await call("GET", "get_package_data", params={
                    "package_id": package_id,
                })
                if not isinstance(package, dict):
                    raise UserFacingFailure("pyLoad returned an unexpected status for this package.",
                                            "Retry; if it repeats, check the pyLoad daemon.")
                files = package.get("links") or []
                if not isinstance(files, list) or any(
                    not isinstance(x, dict) or type(x.get("status")) is not int
                    or x["status"] not in range(15) for x in files
                ):
                    raise UserFacingFailure("pyLoad returned an unexpected status for this package.",
                                            "Retry; if it repeats, check the pyLoad daemon.")
                finished = [x for x in files if x.get("status") == 0]
                failures = [x for x in files if x.get("status") in {8, 9, 14}]
                if failures:
                    reasons = []
                    for link in failures:
                        reason = next((link[key] for key in (
                            "errorMessage", "error", "reason", "statusmsg", "statusMessage"
                        ) if isinstance(link.get(key), str) and link[key].strip()), None)
                        if reason:
                            name = str(link.get("name") or "file")[:100]
                            reasons.append(f"{name}: {reason[:200]}")
                    details = " Reasons: " + "; ".join(reasons[:3]) + "." if reasons else ""
                    raise UserFacingFailure(
                        f"pyLoad could not download {len(failures)} of {len(files)} files in this package."
                        + details,
                        "Retry the failed files; if a hoster limit is reported, wait before retrying.",
                    )
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


async def submit_pyload_captcha(*, api_url: str, api_key: str, task_id: int,
                                answer: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Submit manual captcha privately as a POST form, never in a URL query."""
    base = validate_pyload_endpoint(api_url)
    if not api_key.startswith("pl_") or task_id <= 0 or not answer:
        raise ValueError("Invalid manual pyLoad captcha submission")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                                  transport=transport) as client:
        response = await client.post(
            base + "/api/set_captcha_result",
            headers={"X-API-Key": api_key},
            data={"tid": str(task_id), "result": answer},
        )
        response.raise_for_status()
        # pyLoad returns no payload on a successful manual captcha POST in
        # some supported versions. An empty 2xx body is a valid success.
        if response.content:
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                raise RuntimeError("pyLoad refused the manual captcha answer")
