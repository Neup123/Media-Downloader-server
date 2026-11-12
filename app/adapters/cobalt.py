"""Local cobalt API resolver; Core still validates and downloads its output."""
from pathlib import Path

import httpx

from app.adapters.generic import run_generic
from app.security import validate_target_url


async def run_cobalt(*, url, workdir: Path, api_url, on_progress, on_log,
                     is_cancelled, allow_private_targets=False):
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        response = await client.post(api_url.rstrip("/") + "/", json={"url": url},
                                     headers={"Accept": "application/json"})
        response.raise_for_status()
        body = response.json()
    if body.get("status") not in {"tunnel", "redirect"} or not isinstance(body.get("url"), str):
        raise RuntimeError("cobalt could not resolve a downloadable media URL")
    resolved = body["url"]
    # Only the configured daemon's own tunnel is allowed to use loopback.
    from urllib.parse import urlsplit
    target, local = urlsplit(resolved), urlsplit(api_url)
    tunnel = body["status"] == "tunnel" and (
        target.scheme, target.hostname, target.port
    ) == (local.scheme, local.hostname, local.port) and target.path == "/tunnel"
    validate_target_url(resolved, "generic", allow_private_targets or tunnel)
    return await run_generic(
        executable="curl", url=resolved, workdir=workdir,
        global_settings={}, overrides={}, on_progress=on_progress,
        on_log=on_log, is_cancelled=is_cancelled,
        allow_private_targets=allow_private_targets or tunnel,
    )
