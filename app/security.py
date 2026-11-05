from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    pass


def validate_target_url(url: str, engine: str, allow_private: bool = False) -> str:
    """Validate URL syntax and reject obvious local/private destinations.

    The deployment must also restrict container egress at the host/network layer.
    This check is deliberately not treated as a replacement for an egress firewall.
    """
    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL") from exc
    allowed = {"http", "https"}
    if engine == "aria2c":
        allowed |= {"ftp", "ftps", "magnet"}
    if parsed.scheme.lower() not in allowed:
        raise UnsafeUrlError(f"{engine} does not accept the {parsed.scheme or 'unknown'} URL scheme")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials embedded in URLs are not allowed; configure authentication in engine settings")
    if parsed.scheme.lower() == "magnet":
        if engine != "aria2c" or not parsed.query:
            raise UnsafeUrlError("Invalid magnet URI")
        return url.strip()
    if not parsed.hostname:
        raise UnsafeUrlError("URL must contain a hostname")
    host = parsed.hostname.rstrip(".").lower()
    if not allow_private:
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeUrlError("Local and internal destinations are blocked")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (not address.is_global):
            raise UnsafeUrlError("Private, local, and reserved IP addresses are blocked")
    return url.strip()


SENSITIVE_OPTION_NAMES = {
    "--exec", "--exec-before-download", "--exec-after-download", "--external-downloader",
    "--output", "-o", "--paths", "-P", "--batch-file", "-a", "--progress-template",
    "--cookies", "--cookies-from-browser", "--download-archive", "--config-location",
    "--load-info-json", "--write-info-json", "--write-annotations", "--write-pages",
    "--print-to-file", "-O", "--config-locations", "--config-locations=",
}


def validate_extra_args(args: list[str]) -> list[str]:
    if not isinstance(args, list):
        raise ValueError("extra_args must be a list of strings")
    if len(args) > 40:
        raise ValueError("At most 40 extra arguments are allowed")
    cleaned: list[str] = []
    for raw in args:
        if not isinstance(raw, str) or len(raw) > 512 or "\x00" in raw:
            raise ValueError("Each extra argument must be a string of at most 512 characters")
        option = raw.split("=", 1)[0]
        if option in SENSITIVE_OPTION_NAMES:
            raise ValueError(f"{option} is managed by the service and cannot be overridden")
        cleaned.append(raw)
    return cleaned
