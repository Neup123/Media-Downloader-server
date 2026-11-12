"""Captcha handling: local ddddocr, owner's paid solver, then manual.

Never embed provider keys or captcha images in job logs or public status.
Paid solvers are injected interfaces: no provider or key is invented.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

PaidSolver = Callable[[bytes], Awaitable[str | None]]


class CaptchaWaiting(RuntimeError):
    """No solver completed; preserve the pyLoad challenge ID for manual handoff."""

    def __init__(self, message: str, *, task_id: int | None = None,
                 package_id: int | None = None):
        super().__init__(message)
        self.task_id = task_id
        self.package_id = package_id


def _ocr(image: bytes) -> str | None:
    import ddddocr  # Lazy: model and RAM allocated only when solving.
    text = ddddocr.DdddOcr(show_ad=False).classification(image)
    return text.strip() or None if isinstance(text, str) else None


async def solve_captcha(
    image: bytes,
    *,
    ddddocr_enabled: bool,
    user_paid_solver: PaidSolver | None = None,
    configured_paid_solver: PaidSolver | None = None,
) -> str:
    if not image or len(image) > 5_000_000:
        raise ValueError("Captcha image must be between 1 and 5000000 bytes")
    if ddddocr_enabled:
        try:
            answer = await asyncio.to_thread(_ocr, image)
            if answer:
                return answer
        except Exception:
            # Local OCR can fail on unsupported inputs. Only continue with
            # configured providers; never mark the job failed for that alone.
            pass
    for resolver in (user_paid_solver, configured_paid_solver):
        if resolver is None:
            continue
        try:
            answer = await resolver(image)
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        except Exception:
            pass
    raise CaptchaWaiting("Captcha requires manual action; no automatic solver completed")
