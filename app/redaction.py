"""Central credential, URL-query and secret redaction for all downloader logs."""
from __future__ import annotations

import re

_LOG_URL = re.compile(r"""(?i)\b(?:https?|ftps?)://[^\s<>"'\x60]+""")


def _redact(message: str) -> str:
    """Remove arbitrary signed URL parameters before persisting engine logs.

    A query key allowlist cannot cover vendor-specific signed URL schemes.
    Keep the origin/path for diagnostics, but never preserve query/fragment
    content, including uncommon credential names and URL-embedded passwords.
    """
    message = re.sub(
        r"(?i)(authorization|cookie|set-cookie|app_password|rpc-secret|token|password|secret|api_key)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]", message,
    )

    def clean_url(match: re.Match[str]) -> str:
        url = match.group(0)
        # Credential-bearing authorities must never be echoed; this applies
        # to FTP(S) and HTTP(S) links that extractors may discover internally.
        scheme, rest = url.split("://", 1)
        authority, slash, path = rest.partition("/")
        if "@" in authority:
            authority = "[REDACTED]@" + authority.rsplit("@", 1)[-1]
        result = scheme + "://" + authority + (slash + path if slash else "")
        cut = min((i for i in (result.find("?"), result.find("#")) if i >= 0), default=-1)
        if cut >= 0:
            result = result[:cut] + "?[REDACTED]"
        return result

    message = _LOG_URL.sub(clean_url, message)
    # Preserve existing protection for bare key/value secrets not attached to
    # an HTTP URL, such as a credential printed by a CLI diagnostic.
    message = re.sub(
        r"([?&](?:token|access_token|api_key|key|secret|auth|password|signature|sig|session|cookie)=)[^&#\s]+",
        r"\1[REDACTED]", message, flags=re.IGNORECASE,
    )
    return message[:4000]


