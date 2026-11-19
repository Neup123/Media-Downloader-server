"""Safe user-facing job failure categories; engine diagnostics belong in the technical log."""
from __future__ import annotations

import asyncio

from app.delivery import DeliveryError
from app.security import SourceResolutionError, UnsafeUrlError


def user_failure_message(error: Exception, *, during_delivery: bool = False) -> str:
    """Return a bounded user message without reflecting raw engine/stdout/URL text.

    The only inspected message text is from the service-owned DeliveryError
    vocabulary (e.g. known WebDAV 507/403/404 status). All unknown error
    classes fail closed to generic user wording.
    """
    if isinstance(error, DeliveryError):
        detail = str(error).lower()
        if "quota exceeded" in detail:
            return "Nextcloud Files quota exceeded. Free space and retry."
        if "not writable" in detail:
            return "Destination folder is not writable. Choose a writable folder."
        if "folder is missing" in detail:
            return "Destination folder is missing. Choose an existing folder."
        return "Could not verify file delivery. Check the destination and retry."
    if isinstance(error, UnsafeUrlError):
        return "The source link was blocked for safety; private or reserved addresses are not allowed."
    if isinstance(error, SourceResolutionError):
        return "The source link could not be reached. Check the address and try again."
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "The download took too long. Try again."
    if during_delivery:
        return "The download finished, but the file could not be saved. Check your destination."
    return "The download failed. Check the technical log or try again."
