"""Safe user-facing job failure categories; engine diagnostics belong in the technical log."""
from __future__ import annotations

import asyncio
from typing import Literal

from app.delivery import DeliveryError
from app.engine_failures import EngineFailure, EngineFailureKind, YouGetFallbackFailure
from app.engine_registry import ENGINE_NAMES
from app.security import SourceResolutionError, UnsafeUrlError


def user_failure_message(
    error: Exception, *, during_delivery: bool = False,
    engine_deadline: tuple[str, int] | None = None,
    cookie_guidance: Literal["selected", "available"] | None = None,
) -> str:
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
        if during_delivery:
            return "File delivery timed out. Check your destination and retry."
        # Only the worker's expired asyncio deadline is evidence of the
        # configured engine budget being exhausted. A source/RPC timeout is
        # not necessarily that deadline. Never interpolate exception text.
        if engine_deadline is not None:
            engine, seconds = engine_deadline
            if engine in ENGINE_NAMES and type(seconds) is int and seconds > 0:
                return (
                    f"The download took too long. {engine} reached the configured "
                    f"{seconds}-second download limit. Try a smaller download or "
                    "ask the administrator to review the limit."
                )
        return "The download took too long. Try again."
    if during_delivery:
        return "The download finished, but the file could not be saved. Check your destination."
    if isinstance(error, YouGetFallbackFailure):
        return (
            "you-get could not handle this YouTube link; yt-dlp fallback also failed. "
            + user_failure_message(
                EngineFailure(error.kind, ""), cookie_guidance=cookie_guidance,
            )
        )
    if isinstance(error, EngineFailure):
        if error.kind == EngineFailureKind.AUTH_REQUIRED:
            if cookie_guidance == "selected":
                return (
                    "The source requires valid authentication. Check your account and "
                    "replace your selected cookie file in cookie settings before retrying."
                )
            if cookie_guidance == "available":
                return (
                    "The source requires valid authentication. Check your account, "
                    "add or update your own cookies in cookie settings, or ask the administrator."
                )
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
            EngineFailureKind.TIMEOUT: "The source did not respond in time. Try again later.",
            EngineFailureKind.SLOW_TRANSFER: "The transfer was too slow to continue. Retry or ask the administrator to check the speed limit.",
            EngineFailureKind.INCOMPLETE: "The transfer stopped before completion. Retry the download.",
            EngineFailureKind.RESUME_FAILED: "The previous transfer could not be resumed. Start a new download.",
            EngineFailureKind.DUPLICATE_TRANSFER: "The same file or torrent is already downloading. Wait for that transfer to finish.",
            EngineFailureKind.FILE_CONFLICT: "The output file already exists. Ask the administrator to check the download workspace.",
            EngineFailureKind.STORAGE_IO: "The server could not read or write the download workspace. Ask the administrator to check storage and permissions.",
            EngineFailureKind.INVALID_METADATA: "The download metadata or link is invalid. Obtain a valid source link or torrent file.",
            EngineFailureKind.SOURCE_PROTOCOL: "The source returned an unexpected response. Check the link or try again later.",
            EngineFailureKind.REDIRECT_LIMIT: "The source redirected too many times. Check the link.",
            EngineFailureKind.ENGINE_CONFIGURATION: "The download engine could not process its settings or request. Contact the administrator.",
            EngineFailureKind.SOURCE_BUSY: "The source is temporarily unavailable. Try again later.",
            EngineFailureKind.CHECKSUM_FAILED: "The downloaded data failed its integrity check. Retry the download.",
        }
        return messages.get(error.kind, "The download failed. Check the technical log or try again.")
    return "The download failed. Check the technical log or try again."
