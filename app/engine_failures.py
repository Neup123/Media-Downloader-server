"""Fixed diagnostic categories, never raw external output in public job cards."""
from enum import StrEnum
import re


class EngineFailureKind(StrEnum):
    MISSING_BINARY = "missing_binary"
    NO_OUTPUT = "no_output"
    AUTH_REQUIRED = "auth_required"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED = "unsupported"
    NETWORK = "network"
    CERTIFICATE = "certificate"
    DISK_FULL = "disk_full"
    METADATA_ONLY = "metadata_only"
    TIMEOUT = "timeout"
    SLOW_TRANSFER = "slow_transfer"
    INCOMPLETE = "incomplete"
    RESUME_FAILED = "resume_failed"
    DUPLICATE_TRANSFER = "duplicate_transfer"
    FILE_CONFLICT = "file_conflict"
    STORAGE_IO = "storage_io"
    INVALID_METADATA = "invalid_metadata"
    SOURCE_PROTOCOL = "source_protocol"
    REDIRECT_LIMIT = "redirect_limit"
    ENGINE_CONFIGURATION = "engine_configuration"
    SOURCE_BUSY = "source_busy"
    CHECKSUM_FAILED = "checksum_failed"
    UNKNOWN = "unknown"


class EngineFailure(RuntimeError):
    def __init__(self, kind: EngineFailureKind, diagnostic: str):
        if not isinstance(kind, EngineFailureKind):
            raise ValueError("Unknown engine failure category")
        self.kind = kind
        # Diagnostic stays on the technical side of user_failure_message.
        super().__init__(diagnostic)


def classify_cli_error(line: str) -> EngineFailureKind | None:
    # Only inspect bounded diagnostic lines; retain category, never message.
    text = line[:4000].lower()
    if not re.search(r"\berror\b|\bexception\b|\bfatal\b", text):
        return None
    if re.search(r"\b429\b|too many requests|rate.limit", text):
        return EngineFailureKind.RATE_LIMITED
    if re.search(r"\b401\b|login required|authentication required|sign in|cookies? (?:required|expired|invalid)", text):
        return EngineFailureKind.AUTH_REQUIRED
    if re.search(r"\b403\b|forbidden|access denied", text):
        return EngineFailureKind.ACCESS_DENIED
    if re.search(r"\b404\b|not found|video unavailable", text):
        return EngineFailureKind.NOT_FOUND
    if re.search(r"unsupported url|no suitable extractor|no matching extractor", text):
        return EngineFailureKind.UNSUPPORTED
    if re.search(r"certificate verify failed|certificate verification failed", text):
        return EngineFailureKind.CERTIFICATE
    if "no space left on device" in text:
        return EngineFailureKind.DISK_FULL
    if re.search(r"connection refused|could not resolve|network is unreachable", text):
        return EngineFailureKind.NETWORK
    return EngineFailureKind.UNKNOWN


# curl's documented exit statuses (HTTP(S) downloader scope).
# https://everything.curl.dev/cmdline/exitcode.html
# Do not infer a credential failure from a generic TLS or protocol error.
_CURL_FAILURES = {
    1: EngineFailureKind.UNSUPPORTED,
    **dict.fromkeys((2, 4, 48, 53, 54, 59, 66, 77), EngineFailureKind.ENGINE_CONFIGURATION),
    3: EngineFailureKind.INVALID_METADATA,
    **dict.fromkeys((5, 6, 7, 45, 55, 56), EngineFailureKind.NETWORK),
    **dict.fromkeys((16, 52, 61, 92, 95), EngineFailureKind.SOURCE_PROTOCOL),
    18: EngineFailureKind.INCOMPLETE,
    **dict.fromkeys((23, 26), EngineFailureKind.STORAGE_IO),
    28: EngineFailureKind.TIMEOUT,
    **dict.fromkeys((33, 36), EngineFailureKind.RESUME_FAILED),
    47: EngineFailureKind.REDIRECT_LIMIT,
    **dict.fromkeys((51, 60, 83, 90, 91), EngineFailureKind.CERTIFICATE),
    **dict.fromkeys((67, 94), EngineFailureKind.AUTH_REQUIRED),
    78: EngineFailureKind.NOT_FOUND,
}
_CURL_HTTP_FAILURES = {
    "401": EngineFailureKind.AUTH_REQUIRED,
    "403": EngineFailureKind.ACCESS_DENIED,
    "404": EngineFailureKind.NOT_FOUND,
    "408": EngineFailureKind.TIMEOUT,
    "410": EngineFailureKind.NOT_FOUND,
    "429": EngineFailureKind.RATE_LIMITED,
    "502": EngineFailureKind.SOURCE_PROTOCOL,
    "503": EngineFailureKind.SOURCE_BUSY,
    "504": EngineFailureKind.TIMEOUT,
}


def curl_failure_kind(code: int, detail: str) -> EngineFailureKind:
    if code == 22:
        # Match only curl's own HTTP error line, not arbitrary status-like text.
        match = re.search(
            r"^curl: \(22\) The requested URL returned error: (\d{3})[ \t]*$",
            detail[:4000], re.MULTILINE,
        )
        if match:
            return _CURL_HTTP_FAILURES.get(match[1], EngineFailureKind.UNKNOWN)
    return _CURL_FAILURES.get(code, EngineFailureKind.UNKNOWN)


# aria2 1.37 RPC errorCode uses the documented exit-status vocabulary.
# Codes 0 (success), 1 (unknown) and 31 (reserved) have no specific failure.
# Group only errors that have the same safe operator recovery action.
_ARIA2_FAILURES = {
    "2": EngineFailureKind.TIMEOUT,
    "3": EngineFailureKind.NOT_FOUND,
    "4": EngineFailureKind.NOT_FOUND,
    "5": EngineFailureKind.SLOW_TRANSFER,
    "6": EngineFailureKind.NETWORK,
    "7": EngineFailureKind.INCOMPLETE,
    "8": EngineFailureKind.RESUME_FAILED,
    "9": EngineFailureKind.DISK_FULL,
    "10": EngineFailureKind.RESUME_FAILED,
    "11": EngineFailureKind.DUPLICATE_TRANSFER,
    "12": EngineFailureKind.DUPLICATE_TRANSFER,
    "13": EngineFailureKind.FILE_CONFLICT,
    **dict.fromkeys(("14", "15", "16", "17", "18"), EngineFailureKind.STORAGE_IO),
    "19": EngineFailureKind.NETWORK,
    "20": EngineFailureKind.INVALID_METADATA,
    "21": EngineFailureKind.SOURCE_PROTOCOL,
    "22": EngineFailureKind.SOURCE_PROTOCOL,
    "23": EngineFailureKind.REDIRECT_LIMIT,
    "24": EngineFailureKind.AUTH_REQUIRED,
    **dict.fromkeys(("25", "26", "27"), EngineFailureKind.INVALID_METADATA),
    "28": EngineFailureKind.ENGINE_CONFIGURATION,
    "29": EngineFailureKind.SOURCE_BUSY,
    "30": EngineFailureKind.ENGINE_CONFIGURATION,
    "32": EngineFailureKind.CHECKSUM_FAILED,
}


def aria2_failure_kind(code: object) -> EngineFailureKind:
    # https://aria2.github.io/manual/en/html/aria2c.html#exit-status
    return _ARIA2_FAILURES.get(str(code), EngineFailureKind.UNKNOWN)
