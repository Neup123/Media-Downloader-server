"""Telegram channel/post media download through an authorized MTProto session.

Only t.me/telegram.me message links are accepted. API credentials are passed
from decrypted engine secrets, never from user-controlled job overrides.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.failure_messages import UserFacingFailure


def parse_telegram_message_link(url: str) -> tuple[str | int, int]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"https", "http"} or host not in {
        "t.me", "www.t.me", "telegram.me", "www.telegram.me",
    } or parsed.username or parsed.password:
        raise ValueError("Telegram engine supports only public t.me/telegram.me post links")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) == 2 and parts[1].isdigit() and parts[0].replace("_", "").isalnum():
        return parts[0], int(parts[1])
    if len(parts) == 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
        # t.me/c links use the channel ID with the -100 supergroup prefix.
        return int("-100" + parts[1]), int(parts[2])
    raise ValueError("Telegram URL must identify a channel message, such as https://t.me/channel/123")


async def run_telegram(
    *, url: str, workdir: Path, credentials: dict[str, Any],
    global_settings: dict[str, Any], overrides: dict[str, Any],
    on_progress: Callable, on_log: Callable, is_cancelled: Callable,
) -> list[Path]:
    if overrides:
        raise ValueError("Telegram job-level credentials and overrides are not accepted")
    entity, message_id = parse_telegram_message_link(url)
    if not credentials or not credentials.get("session_string"):
        raise ValueError("Telegram credentials and authorized session are required")
    from telethon import TelegramClient
    try:
        from telethon.errors import AuthKeyUnregisteredError, ChannelPrivateError, FloodWaitError
    except ImportError:
        # Some integrations inject a minimal TelegramClient stub without
        # telethon.errors. Distinct placeholders keep the real exception
        # handlers operative whenever Telethon is fully installed.
        class AuthKeyUnregisteredError(Exception):
            pass

        class ChannelPrivateError(Exception):
            pass

        class FloodWaitError(Exception):
            pass
    from telethon.sessions import StringSession

    workdir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        StringSession(credentials["session_string"]),
        int(credentials["api_id"]), credentials["api_hash"],
    )
    await on_log("Connecting to Telegram with encrypted session credentials")
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Telegram session is not authorized; configure a signed-in StringSession")
        message = await client.get_messages(entity, ids=message_id)
        if not message or not message.media:
            raise ValueError("Telegram post has no downloadable media")
        if await is_cancelled():
            raise __import__("asyncio").CancelledError()
        async def progress(current: int, total: int):
            if await is_cancelled():
                raise __import__("asyncio").CancelledError()
            await on_progress({
                "state": "downloading",
                "downloaded_bytes": int(current),
                "total_bytes": int(total) if total else None,
                "percent": round(current / total * 100, 2) if total else None,
            })
        received = await client.download_media(
            message, file=str(workdir.resolve()), progress_callback=progress,
        )
        if not received:
            raise RuntimeError("Telegram post did not produce a media file")
        path = Path(received).resolve()
        if not path.is_relative_to(workdir.resolve()) or not path.is_file():
            raise RuntimeError("Telegram output escaped its managed work directory")
        return [path]
    except FloodWaitError as exc:
        await on_log(f"Telegram FloodWaitError: {exc}")
        raise UserFacingFailure(
            f"Telegram is rate-limiting this account — retry in {exc.seconds} seconds.",
            "Wait and retry.",
        ) from exc
    except ChannelPrivateError as exc:
        await on_log(f"Telegram ChannelPrivateError: {exc}")
        raise UserFacingFailure(
            "This channel is private, or the account has no access to it.",
            "Use a public channel the account can see, or join it first.",
        ) from exc
    except AuthKeyUnregisteredError as exc:
        await on_log(f"Telegram AuthKeyUnregisteredError: {exc}")
        raise UserFacingFailure(
            "The Telegram session expired.",
            "Re-authorize the Telegram session in settings.",
        ) from exc
    except (ConnectionError, OSError) as exc:
        await on_log(f"Telegram network error: {exc}")
        raise UserFacingFailure(
            "Could not reach Telegram — retry.", "Retry — transient.",
        ) from exc
    finally:
        await client.disconnect()
