"""Isolated yt-dlp metadata probe with typed, machine-readable failure origins.

Only a single reason_code is emitted to stderr on failure. The public API keeps
its existing human detail string and status; raw yt-dlp diagnostics are not sent
to callers. This process is bounded and terminated by app.discovery.
"""
from __future__ import annotations

import json
import sys
from urllib.parse import urldefrag
from urllib.error import HTTPError as UrllibHTTPError

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import HTTPError as YtdlpHTTPError
from yt_dlp.utils import UnsupportedError


class _SilentLogger:
    def debug(self, _message): pass
    def info(self, _message): pass
    def warning(self, _message): pass
    def error(self, _message): pass


class SourceAwareYoutubeDL(YoutubeDL):
    """Record whether the requested source page actually returned HTTP 2xx."""

    def __init__(self, source_url: str, params: dict):
        self.source_url = source_url
        self.source_fetched = False
        super().__init__(params)

    def urlopen(self, req):
        response = super().urlopen(req)
        requested_url = getattr(req, "url", None)
        method = getattr(req, "method", "GET")
        if (
            isinstance(requested_url, str)
            and urldefrag(requested_url).url == urldefrag(self.source_url).url
            and method == "GET"
            and 200 <= getattr(response, "status", 0) < 300
        ):
            # Followed redirects are still a successfully retrieved source
            # page; response.url can differ from the original requested URL.
            self.source_fetched = True
        return response


def _causes(error: BaseException):
    """Walk typed yt-dlp exception links, never human-readable messages."""
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for child in (getattr(current, "cause", None), current.__cause__, current.__context__):
            if isinstance(child, BaseException):
                pending.append(child)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1 and isinstance(exc_info[1], BaseException):
            pending.append(exc_info[1])


def classify_extraction_failure(
    error: BaseException, *, source_url: str, source_fetched: bool, dedicated_claim: bool
) -> str:
    """Prefer source HTTP evidence over broad or opaque extractor exceptions."""
    causes = list(_causes(error))
    for cause in causes:
        if isinstance(cause, YtdlpHTTPError):
            status = cause.status
            failed_url = getattr(cause.response, "url", None)
        elif isinstance(cause, UrllibHTTPError):
            status = cause.code
            failed_url = cause.url
        else:
            continue
        # A 404 from a thumbnail/API/secondary request does not by itself
        # prove the item URL is gone.
        if status in (404, 410) and isinstance(failed_url, str) and (
            urldefrag(failed_url).url == urldefrag(source_url).url
        ):
            return "item_not_found"

    if any(isinstance(cause, UnsupportedError) for cause in causes):
        if source_fetched:
            # GenericIE raises UnsupportedError after fetching a page and
            # exhausting its direct-media and embedded-media detectors.
            return "not_media"
        if not dedicated_claim:
            # No dedicated host claimant and no successfully fetched page.
            # UnsupportedError is yt-dlp's typed no-handler signal.
            return "unsupported_domain"

    # "private", "deleted" and "no formats" often arrive as a generic
    # ExtractorError(expected=True) with just prose. That is NOT evidence.
    return "could_not_check"


def inspect(url: str, max_entries: int, offset: int) -> dict:
    params = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playliststart": offset + 1,
        "playlistend": offset + max_entries + 1,
        "logger": _SilentLogger(),
    }
    with SourceAwareYoutubeDL(url, params) as ydl:
        dedicated_claim = any(
            key != "Generic" and extractor.suitable(url)
            for key, extractor in ydl._ies.items()
        )
        try:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict):
                raise RuntimeError("Extractor returned no metadata")
            return ydl.sanitize_info(info)
        except Exception as exc:
            reason = classify_extraction_failure(
                exc, source_url=url, source_fetched=ydl.source_fetched,
                dedicated_claim=dedicated_claim,
            )
            # Preserve the typed reason across process isolation, not the
            # potentially sensitive engine/URL/error detail.
            print(json.dumps({"reason_code": reason}), file=sys.stderr)
            raise SystemExit(1) from None


def main() -> int:
    if len(sys.argv) != 4:
        print('{"reason_code":"could_not_check"}', file=sys.stderr)
        return 1
    try:
        max_entries, offset, url = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
        metadata = inspect(url, max_entries, offset)
        print(json.dumps(metadata))
        return 0
    except SystemExit:
        raise
    except Exception:
        print('{"reason_code":"could_not_check"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
