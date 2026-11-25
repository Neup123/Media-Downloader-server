"""Shared bounds and job-only file index selection for flat galleries.

The selection is source-order, not a stable snapshot or identity guarantee.
No UI or preview endpoint may promise an unchanged external gallery from this
primitive alone. Restrict settings that could change the extractor's ordering.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

MAX_GALLERY_OFFSET = 1000
MAX_GALLERY_PAGE = 100
MAX_GALLERY_INDEX = MAX_GALLERY_OFFSET + MAX_GALLERY_PAGE
MAX_GALLERY_IDENTITIES = 200
_SAFE_TRANSPORT_OPTIONS = frozenset({
    "extractor.sleep", "extractor.sleep-request", "extractor.sleep-retries",
    "extractor.retries", "extractor.timeout",
})


def gallery_selection(value: object) -> tuple[str, int]:
    if not isinstance(value, str) or len(value) > 5500 or not re.fullmatch(
        r"[1-9][0-9]{0,3}(?:-[1-9][0-9]{0,3})?(?:,[1-9][0-9]{0,3}(?:-[1-9][0-9]{0,3})?)*",
        value,
    ):
        raise ValueError("gallery_items must contain one-based file indexes or ranges")
    selected = set()
    for part in value.split(","):
        bounds = part.split("-")
        first, last = int(bounds[0]), int(bounds[-1])
        if not 1 <= first <= last <= MAX_GALLERY_INDEX:
            raise ValueError(f"gallery_items indexes must be between 1 and {MAX_GALLERY_INDEX}")
        indexes = set(range(first, last + 1))
        if selected & indexes:
            raise ValueError("gallery_items must not repeat or overlap")
        selected.update(indexes)
    # gallery-dl consumes ranges in source order; canonicalize them in one place.
    ordered = sorted(selected)
    compact = []
    start = end = ordered[0]
    for index in ordered[1:]:
        if index == end + 1:
            end = index
        else:
            compact.append(str(start) if start == end else f"{start}-{end}")
            start = end = index
    compact.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(compact), len(ordered)


def validate_gallery_selection_settings(settings: dict) -> tuple[str, int] | None:
    if "gallery_items" not in settings:
        return None
    result = gallery_selection(settings["gallery_items"])
    if (settings.get("cookies_file") or settings.get("proxy")
            or set(settings.get("options", {})) - _SAFE_TRANSPORT_OPTIONS):
        raise ValueError("Selected gallery files require the same unauthenticated, unfiltered source as preview")
    return result


def gallery_url_identity(url: object) -> str | None:
    """Opaque identity of the exact public extractor URL, not of remote bytes."""
    if not isinstance(url, str) or not 0 < len(url) <= 8192:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def gallery_identities(value: object) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_GALLERY_IDENTITIES
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"gallery_item_ids must contain 1–{MAX_GALLERY_IDENTITIES} unique file identities")
    return sorted(value)


def validate_gallery_identity_settings(settings: dict) -> list[str] | None:
    if "gallery_item_ids" not in settings:
        return None
    if ("gallery_items" in settings or settings.get("cookies_file") or settings.get("proxy")
            or set(settings.get("options", {})) - _SAFE_TRANSPORT_OPTIONS):
        raise ValueError("Identity-selected gallery files require the same unauthenticated, unfiltered source as preview")
    return gallery_identities(settings["gallery_item_ids"])
