"""ddddocr OCR integration, opt-in and lazy resource allocation.

Not a URL download engine: provides the first captcha-solving step for engines
such as pyLoad. This adapter never stores or logs captcha image/answer data.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.captcha import solve_captcha, CaptchaWaiting


async def run_ddddocr(
    image: bytes,
    *,
    run_carefully: bool,
    user_paid_solver: Callable[[bytes], Awaitable[str | None]] | None = None,
    configured_paid_solver: Callable[[bytes], Awaitable[str | None]] | None = None,
) -> str:
    return await solve_captcha(
        image, ddddocr_enabled=run_carefully,
        user_paid_solver=user_paid_solver,
        configured_paid_solver=configured_paid_solver,
    )
