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


def curl_failure_kind(code: int, detail: str) -> EngineFailureKind:
    # curl's stable exit codes; parse HTTP status only from curl's own format.
    if code == 22:
        match = re.search(r"curl: \(22\) The requested URL returned error: (\d{3})\b", detail)
        if match:
            return {"401": EngineFailureKind.AUTH_REQUIRED, "403": EngineFailureKind.ACCESS_DENIED,
                    "404": EngineFailureKind.NOT_FOUND, "429": EngineFailureKind.RATE_LIMITED}.get(
                        match[1], EngineFailureKind.UNKNOWN)
    return {5: EngineFailureKind.NETWORK, 6: EngineFailureKind.NETWORK,
            7: EngineFailureKind.NETWORK, 60: EngineFailureKind.CERTIFICATE}.get(
                code, EngineFailureKind.UNKNOWN)


def aria2_failure_kind(code: object) -> EngineFailureKind:
    # aria2 exit-status vocabulary: https://aria2.github.io/manual/en/html/aria2c.html#exit-status
    return {"3": EngineFailureKind.NOT_FOUND, "6": EngineFailureKind.NETWORK,
            "9": EngineFailureKind.DISK_FULL, "24": EngineFailureKind.AUTH_REQUIRED}.get(
                str(code), EngineFailureKind.UNKNOWN)
