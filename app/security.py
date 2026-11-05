from __future__ import annotations

import ipaddress
import socket
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


def resolve_http_target(url: str, allow_private: bool = False) -> str | None:
    """Resolve a curl HTTP(S) target and return a pinned --resolve rule.

    Literal IPs require no DNS pin. All DNS answers must be global when
    private targets are disabled; mixing a public and private answer is unsafe.
    The caller must disable proxies and redirects to any unvalidated host.
    This applies to curl only, not to the other engine subprocesses.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL host or port") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise UnsafeUrlError("Direct HTTP DNS pinning requires an HTTP(S) hostname")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Embedded URL credentials are not allowed")
    if not 1 <= port <= 65535:
        raise UnsafeUrlError("Invalid target port")
    if not allow_private and (
        host.lower() in {"localhost", "localhost.localdomain"}
        or host.lower().endswith((".localhost", ".local", ".internal"))
    ):
        raise UnsafeUrlError("Internal hostname is not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not allow_private and not address.is_global:
            raise UnsafeUrlError("Private or reserved target address is blocked")
        return None

    # The curl DNS cache must use exactly the same spelling as the URL.
    # A non-ASCII/terminal-dot spelling could otherwise bypass --resolve.
    if not host.isascii() or host.endswith(".") or not host:
        raise UnsafeUrlError("Use an ASCII hostname without a trailing dot for direct downloads")
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        raise UnsafeUrlError("Could not resolve target hostname") from exc
    addresses = set()
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafeUrlError("DNS resolution returned an invalid address") from exc
        if not allow_private and not address.is_global:
            raise UnsafeUrlError("DNS resolution includes a private or reserved address")
        addresses.add(address)
    if not addresses:
        raise UnsafeUrlError("DNS resolution returned no IP addresses")
    # curl requires IPv6 addresses inside brackets.
    encoded = ",".join(
        f"[{ip}]" if ip.version == 6 else str(ip)
        for ip in sorted(addresses, key=lambda ip: (ip.version, int(ip)))
    )
    return f"{host}:{port}:{encoded}"
