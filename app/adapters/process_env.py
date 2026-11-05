"""Build subprocess environments without ambient network proxy settings.

The service does not trust the host's proxy variables as a network policy.
Deliberately configured per-engine proxy settings are passed as CLI flags
where supported. This is defense in depth, not an outbound firewall.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


_PROXY_VARIABLES = frozenset({
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy",
})


def downloader_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Preserve unrelated configuration while discarding inherited proxy routes."""
    original = os.environ if source is None else source
    return {key: value for key, value in original.items() if key.lower() not in _PROXY_VARIABLES}
