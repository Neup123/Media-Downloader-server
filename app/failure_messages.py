"""User-facing engine failures. Preserve original engine diagnostics in job logs."""
from __future__ import annotations

import re


_ERROR_LINE = re.compile(r"(?:^|[\s\[])(?:error|fatal)(?=[:\]\s])", re.I)
_AUTH = re.compile(
    r"sign in to confirm|login required|log[ -]?in required|cookies?\b|"
    r"age[- ]gated|age[- ]restricted|sign[ -]?in required", re.I,
)


class LastErrorLine:
    """Small shared collector used by all six CLI extractors."""

    def __init__(self) -> None:
        self.value: str | None = None

    def capture(self, line: str) -> None:
        if _ERROR_LINE.search(line):
            self.value = line.strip()[:4000]


class CliFailed(RuntimeError):
    def __init__(self, engine: str, code: int, last_error: str | None) -> None:
        self.engine = engine
        self.code = code
        self.last_error = last_error
        super().__init__(last_error or f"{engine} exited with status {code}")


class UserFacingFailure(RuntimeError):
    def __init__(self, summary: str, remediation: str) -> None:
        super().__init__(summary)
        self.remediation = remediation


def _show(summary: str, remediation: str) -> str:
    return f"{summary} {remediation}"


def timeout_message(seconds: int, engine: str) -> str:
    return _show(
        f"Download timed out after {seconds}s (engine: {engine}). The source may be stalled or very slow.",
        "Retry — transient. If it repeats, the source may be too slow for this download; "
        "try a smaller file or a different source.",
    )


def blocked_engine_message(engine: str, policy: dict) -> str:
    if not policy.get("enabled", True):
        return _show("Waiting: engine disabled by owner.",
                     "Enable the engine in settings, then the job will run.")
    if engine == "telegram":
        return _show("Waiting: Telegram credentials are required.",
                     "Enter the required credentials in settings, then the job will run.")
    if engine == "pyload":
        return _show("Waiting: pyLoad service settings are required.",
                     "Configure the required service credentials in settings, then the job will run.")
    return _show(f"Waiting: {policy.get('inactive_reason') or 'engine is not permitted'}.",
                 "Enable the engine or complete its requirements in settings, then the job will run.")


# aria2 1.37 manual, EXIT STATUS. Code 31 is reserved (not documented as a failure).
# https://aria2.github.io/manual/en/html/aria2c.html#exit-status
_ARIA2_ERRORS: dict[int, tuple[str, str]] = {
    1: ("an unknown download error occurred", "Retry; if it repeats, inspect the aria2 service log."),
    2: ("the server timed out", "Retry — the server may be slow."),
    3: ("the file was not found at that address", "Check the link."),
    4: ("the file was repeatedly not found", "Check whether the link is still valid."),
    5: ("the download speed fell below the configured limit", "Retry or adjust the speed limit."),
    6: ("a network error occurred while downloading", "Retry — transient network error."),
    7: ("the download was unfinished when aria2 stopped", "Retry the download."),
    8: ("the server does not support the required resume", "Retry the download from the beginning."),
    9: ("not enough disk space", "Free disk space and retry."),
    10: ("the saved piece length differs", "Restart the download with matching settings."),
    11: ("the same file is already being downloaded", "Wait for the other download to finish."),
    12: ("the same torrent is already being downloaded", "Wait for the other torrent to finish."),
    13: ("the output file already exists", "Choose another destination or remove the conflicting file."),
    14: ("the output file could not be renamed", "Check filesystem permissions and retry."),
    15: ("an existing file could not be opened", "Check filesystem permissions."),
    16: ("the output file could not be created", "Check the output directory and its permissions."),
    17: ("a file input/output error occurred", "Check storage health and permissions."),
    18: ("the output directory could not be created", "Check directory permissions."),
    19: ("the hostname could not be resolved", "Check the address and DNS settings."),
    20: ("the Metalink document could not be parsed", "Check that the Metalink is valid."),
    21: ("an FTP command failed", "Check the server and FTP settings."),
    22: ("the server returned an unsuccessful HTTP response", "Check the link and server access."),
    23: ("too many redirects occurred", "Check the final link or sign in to the site."),
    24: ("the server refused the credentials", "Check the credentials in settings."),
    25: ("the torrent or bencoded file could not be parsed", "Get a valid torrent file or magnet link."),
    26: ("the torrent file is corrupted or incomplete", "Download the torrent metadata again."),
    27: ("the magnet URI is invalid", "Check the magnet link."),
    28: ("an aria2 option or option value is invalid", "Check the aria2 settings."),
    29: ("the server is temporarily unavailable", "Retry later."),
    30: ("the JSON-RPC request could not be parsed", "Retry; if it repeats, restart the aria2 service."),
    32: ("the downloaded file failed checksum verification", "Retry or check the expected checksum."),
}


def format_failure(engine: str, exc: Exception, *, cookies_selected: bool = False) -> str:
    """Return a public message; unexpected errors retain their existing wording."""
    if isinstance(exc, UserFacingFailure):
        return _show(str(exc), exc.remediation)

    raw = str(exc)
    if isinstance(exc, CliFailed) and not exc.last_error:
        return raw  # No diagnostic line: preserve the promised exit-status fallback.

    if engine in {"yt-dlp", "gallery-dl", "you-get"} and _AUTH.search(raw):
        if engine == "yt-dlp":
            if cookies_selected:
                return _show(
                    "This video needs a signed-in account; the saved cookies may have expired.",
                    "Refresh the site's cookies in the vault (re-export cookies.txt) and retry.",
                )
            return _show(
                "This video needs a signed-in account. yt-dlp reports: "
                "Sign in to confirm you're not a bot.",
                "Add the site's cookies to the vault (Advanced settings) and retry.",
            )
        label = "gallery" if engine == "gallery-dl" else "site"
        return _show(
            f"This {label} needs a login; the saved cookies may have expired.",
            "Refresh the site's cookies in the vault and retry.",
        )

    if engine == "yt-dlp":
        if re.search(r"unsupported url|no suitable extractor|no matching extractor", raw, re.I):
            return _show("yt-dlp cannot handle this link (no matching extractor).",
                         "Try the generic (curl) engine for a direct file, or another engine.")
        if re.search(r"video unavailable|private video|video is private|removed|not available in your country|"
                     r"geo.?restricted|region.?blocked|not available in your region", raw, re.I):
            return _show("Video unavailable — the video may have been removed, made private, or region-blocked.",
                         "Check the link in a browser; if it plays there, retry — otherwise the source is gone.")

    if engine == "gallery-dl":
        match = re.search(r"(?<!\d)(404|401|403)(?!\d)", raw)
        if match:
            status = int(match.group(1))
            if status == 404:
                return _show("The gallery was not found (404) — it may have been removed or made private.",
                             "Check the link in a browser.")
            return _show(f"The gallery cannot be accessed ({status}) — login or permission may be required.",
                         "Check the link in a browser.")

    if engine == "svtplay-dl" and re.search(r"geo|region|country|unavailable|not available|removed", raw, re.I):
        return _show("This stream is not available in your region or was removed.",
                     "Check the link in a browser.")

    if engine == "aria2c":
        rpc = re.fullmatch(r"aria2 RPC ([\w.]+) failed \(([^)]*)\): (.*)", raw, re.S)
        if rpc:
            return _show(f"aria2 could not check the download status ({rpc.group(1)}): {rpc.group(3)}.",
                         "Retry — transient. If it repeats, restart the service.")
        failure = re.fullmatch(r"aria2 download failed \(([^)]*)\): (.*)", raw, re.S)
        if failure:
            try:
                code = int(failure.group(1))
            except ValueError:
                code = None
            if code in _ARIA2_ERRORS:
                summary, remedy = _ARIA2_ERRORS[code]
                return _show(f"aria2 could not download the file: {summary} ({code}).", remedy)
            return _show(f"aria2 download failed ({failure.group(1)}): {failure.group(2)}.",
                         "Retry; if it repeats, check the aria2 service log.")
        if "Torrent metadata contains a path outside its preview directory" in raw:
            return ("The torrent preview failed because of a server-side path issue "
                    "(known problem, being fixed). Try downloading it directly.")
        if any(part in raw for part in (
            "too many files for manifest preview", "invalid path", "invalid relative path",
            "invalid file size or index",
        )):
            return _show(f"{raw} — the torrent's file list cannot be previewed.",
                         "Try downloading it directly instead of previewing.")

    if engine == "generic":
        code_match = re.search(r"curl failed with status (\d+)", raw)
        if code_match:
            code = int(code_match.group(1))
            if code == 22:
                http_status = re.search(r"\b(?:returned error:|error:)\s*(\d{3})\b", raw, re.I)
                if http_status:
                    status = int(http_status.group(1))
                    descriptions = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
                                    404: "Not Found", 410: "Gone", 429: "Too Many Requests",
                                    500: "Internal Server Error", 503: "Service Unavailable"}
                    suffix = descriptions.get(status, "HTTP Error")
                    return _show(f"The server returned {status} {suffix} for this file.",
                                 "Check the link; if it moved, the file is gone.")
            errors = {
                6: ("Could not resolve the hostname.", "Check the address for a typo."),
                7: ("Could not connect to the server.", "Retry — the server may be down."),
                28: ("The server took too long to respond.", "Retry — transient."),
                47: ("Too many redirects.", "Check the link in a browser."),
                60: ("The server's certificate could not be verified.",
                     "The site's certificate is invalid or expired; contact the site owner."),
            }
            if code in errors:
                return _show(*errors[code])

    if engine == "pyload":
        if "pyLoad returned no completed package links" in raw:
            return _show("pyLoad returned no completed package links — the package finished with nothing downloadable.",
                         "Check the link in pyLoad directly.")
        if raw.startswith("pyLoad reported an error:"):
            return _show(raw if raw.endswith(".") else raw + ".",
                         "Retry; if it repeats, check the pyLoad daemon.")

    if engine in {"yt-dlp", "gallery-dl", "you-get", "svtplay-dl", "get-sauce", "musicdl", "generic"} and re.search(
        r"completed without any output file|exited successfully but no output file was found|"
        r"completed without producing a file", raw, re.I,
    ):
        return _show(
            f"{engine} reported success but produced no file. "
            "The source may have returned an empty page or a format this engine cannot save.",
            "Retry; if it repeats, the link may point at a page rather than media — try yt-dlp.",
        )
    return raw
