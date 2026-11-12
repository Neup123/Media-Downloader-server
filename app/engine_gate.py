"""Persistent engine enablement, resource opt-in and credential requirements."""
from __future__ import annotations

import json
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import Settings
from app.background_services import ENGINES, service_mode
from app.db import EngineCredential, EnginePolicy

RAM_NOTES = {
    "ddddocr": "Explicit opt-in required: ddddocr can consume 150–350 MB RAM while solving a captcha.",
}


def _cipher(config: Settings) -> Fernet:
    if not config.encryption_key:
        raise ValueError("APP_ENCRYPTION_KEY is required before storing Telegram credentials")
    try:
        return Fernet(config.encryption_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def save_telegram_credentials(session: Session, config: Settings, secrets: dict[str, Any]) -> None:
    # Never store the credentials in EngineSetting.settings: that JSON is
    # returned over the settings API. Only encrypted bytes go to the DB.
    token = _cipher(config).encrypt(json.dumps(secrets).encode("utf-8")).decode("ascii")
    row = session.get(EngineCredential, "telegram")
    if row is None:
        session.add(EngineCredential(engine="telegram", ciphertext=token))
    else:
        row.ciphertext = token
    session.commit()


def telegram_credentials(session: Session, config: Settings) -> dict[str, Any] | None:
    row = session.get(EngineCredential, "telegram")
    if row is None or not config.encryption_key:
        return None
    try:
        result = json.loads(_cipher(config).decrypt(row.ciphertext.encode("ascii")).decode("utf-8"))
        if (
            isinstance(result, dict) and type(result.get("api_id")) is int and result["api_id"] > 0
            and isinstance(result.get("api_hash"), str)
            and re.fullmatch(r"[0-9a-fA-F]{32}", result["api_hash"])
            and isinstance(result.get("session_string"), str) and result["session_string"]
        ):
            return result
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        pass
    return None


def engine_policy(session: Session, name: str, config: Settings) -> dict[str, Any]:
    if name in ENGINES:
        mode = service_mode(session, name)
        unmet = None
        if mode != "off" and name == "pyload" and (
            not config.pyload_api_url or not config.pyload_api_key or not config.pyload_shared_root
        ):
            unmet = "Local pyLoad API URL, API key and download root are required"
        return {
            "mode": mode, "enabled": mode != "off", "run_carefully": False,
            "requires_run_carefully": False,
            "ram_note": "On-demand waits for startup; always-on holds idle RAM (pyLoad ~50-150 MB, cobalt ~100-150 MB).",
            "requirements_met": unmet is None,
            "inactive_reason": "Engine off by owner" if mode == "off" else unmet,
            "permitted": mode != "off" and unmet is None,
        }
    row = session.get(EnginePolicy, name)
    enabled = row.enabled if row is not None else True
    opt_in = row.run_carefully if row is not None else False
    unmet = None
    if name == "telegram" and telegram_credentials(session, config) is None:
        unmet = "Telegram API ID, API hash and authorized session are required"
    elif name in RAM_NOTES and not opt_in:
        unmet = "Explicit run carefully opt-in is required"
    reason = "Engine disabled by owner" if not enabled else unmet
    return {
        "enabled": enabled,
        "run_carefully": opt_in,
        "requires_run_carefully": name in RAM_NOTES,
        "ram_note": RAM_NOTES.get(name),
        "requirements_met": unmet is None,
        "inactive_reason": reason,
        "permitted": enabled and unmet is None,
    }
