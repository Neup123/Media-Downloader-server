"""Safe user-facing job failure categories; engine diagnostics belong in the technical log."""
from __future__ import annotations

import asyncio

from app.delivery import DeliveryError
from app.engine_failures import EngineFailure, EngineFailureKind
from app.security import SourceResolutionError, UnsafeUrlError


def user_failure_message(error: Exception, *, during_delivery: bool = False) -> str:
    """Return a bounded user message without reflecting raw engine/stdout/URL text.

    Typed engine failures contribute only a fixed category. The only inspected
    message text here is from the service-owned DeliveryError
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
    if isinstance(error, EngineFailure):
        messages = {
            EngineFailureKind.MISSING_BINARY: "The download engine is not installed. Contact the administrator.",
            EngineFailureKind.NO_OUTPUT: "The download engine returned no file. Check the source link.",
            EngineFailureKind.AUTH_REQUIRED: "The source requires valid authentication. Check your account or site connection.",
            EngineFailureKind.ACCESS_DENIED: "The source denied access. Check your permissions.",
            EngineFailureKind.NOT_FOUND: "The source media could not be found. Check that the link still works.",
            EngineFailureKind.RATE_LIMITED: "The source is limiting requests. Wait before trying again.",
            EngineFailureKind.UNSUPPORTED: "The engine does not support this source link. Try a supported link.",
            EngineFailureKind.NETWORK: "The engine could not reach the source. Check the connection and try again.",
            EngineFailureKind.CERTIFICATE: "The source certificate could not be verified. Contact the administrator.",
            EngineFailureKind.DISK_FULL: "The download server has insufficient disk space. Contact the administrator.",
            EngineFailureKind.METADATA_ONLY: "Torrent metadata was retrieved, but no file transfer started. Try again later.",
        }
        return messages.get(error.kind, "The download failed. Check the technical log or try again.")
    return "The download failed. Check the technical log or try again."
