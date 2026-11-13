from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    pass


class SourceResolutionError(UnsafeUrlError):
    """DNS lookup failed, not a malformed or policy-rejected source URL."""
    pass


def _validated_urlsplit(url: str):
    """Reject URL spellings that different HTTP clients might reinterpret.

    urllib.urlsplit strips embedded tab/newline characters; a downloader may
    interpret them differently. Backslashes and percent-encoded hostnames
    likewise create parser inconsistencies. Validate before normalization.
    """
    if any(ord(char) < 32 or ord(char) == 127 or ord(char) == 92 for char in url):
        raise UnsafeUrlError("URL must not contain control characters or backslashes")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL host, IPv6 brackets, or port") from exc
    if port == 0 or parsed.netloc.endswith(":"):
        raise UnsafeUrlError("URL contains an invalid or empty port")
    if parsed.hostname and "%" in parsed.hostname:
        raise UnsafeUrlError("Percent-encoded hostnames are not supported")
    return parsed


def _canonical_host(hostname: str) -> str:
    """Normalize IDNA for safety checks, without altering the original URL."""
    host = hostname.rstrip(".").lower()
    if ":" not in host:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UnsafeUrlError("Invalid internationalized hostname") from exc
    return host


def _reject_noncanonical_ipv4(host: str) -> None:
    """Block historical numeric aliases such as 127.1 and 0x7f000001.

    Many network clients parse those strings as an IPv4 address while Python's
    ipaddress.ip_address rejects them. Deny them instead of making claims
    about which address a particular downstream engine will actually dial.
    This check is local (inet_aton) and never performs a DNS lookup.
    """
    try:
        socket.inet_aton(host)
    except OSError:
        return
    try:
        ipaddress.ip_address(host)
    except ValueError as exc:
        raise UnsafeUrlError("Noncanonical IPv4 address syntax is blocked") from exc


def validate_target_url(url: str, engine: str, allow_private: bool = False) -> str:
    """Validate URL syntax and reject obvious local/private destinations.

    The deployment must also restrict container egress at the host/network layer.
    This check is deliberately not treated as a replacement for an egress firewall.
    """
    parsed = _validated_urlsplit(url)
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
    host = _canonical_host(parsed.hostname)
    if not allow_private:
        _reject_noncanonical_ipv4(host)
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


# A generic denylist cannot protect against positional second URLs, alternate
# output switches, external downloader activation, or new unsafe yt-dlp flags.
# Only these audited, non-routing, non-executing options may be passed through.
# Options taking values must use --option=value, never a second argv token.
SAFE_YTDLP_FLAGS = frozenset({
    "--no-warnings",
    "--ignore-errors",
    "--no-mtime",
    "--restrict-filenames",
    "--windows-filenames",
    "--write-thumbnail",
    "--write-all-thumbnails",
    "--write-auto-subs",
    "--embed-thumbnail",
    "--embed-metadata",
    "--no-embed-thumbnail",
    "--no-embed-metadata",
    "--no-part",
    "--no-check-formats",
    "--check-formats",
})

SAFE_YTDLP_INTEGER_OPTIONS: dict[str, tuple[int, int]] = {
    "--socket-timeout": (1, 300),
    "--retries": (0, 50),
    "--fragment-retries": (0, 50),
    "--extractor-retries": (0, 50),
    "--concurrent-fragments": (1, 32),
    "--playlist-start": (1, 10000),
    "--playlist-end": (1, 10000),
}

SAFE_YTDLP_ENUM_OPTIONS = {
    "--merge-output-format": {"mp4", "mkv", "webm", "mov", "m4a"},
    "--remux-video": {"mp4", "mkv", "webm", "mov", "m4a"},
    "--audio-format": {"best", "aac", "flac", "mp3", "m4a", "opus", "vorbis", "wav", "alac"},
}

SAFE_YTDLP_FORMAT_OPTIONS = {
    "--limit-rate": r"[1-9][0-9]{0,8}[KMG]?",
    "--format-sort": r"[A-Za-z0-9+.,:()_/-]{1,160}",
    "--sub-format": r"[A-Za-z0-9+._/-]{1,80}",
}


def _validate_ytdlp_option_value(option: str, value: str) -> None:
    """Reject unreviewed CLI behavior and unsafe or excessive argument values."""
    if not value or "\r" in value or "\n" in value or "://" in value:
        raise ValueError(f"{option} requires a nonempty single-line value")
    if option in SAFE_YTDLP_INTEGER_OPTIONS:
        lower, upper = SAFE_YTDLP_INTEGER_OPTIONS[option]
        if not value.isascii() or not value.isdecimal() or not lower <= int(value) <= upper:
            raise ValueError(f"{option} must be an integer between {lower} and {upper}")
    elif option in SAFE_YTDLP_ENUM_OPTIONS:
        if value not in SAFE_YTDLP_ENUM_OPTIONS[option]:
            raise ValueError(f"Invalid value for {option}")
    elif option in SAFE_YTDLP_FORMAT_OPTIONS:
        if re.fullmatch(SAFE_YTDLP_FORMAT_OPTIONS[option], value) is None:
            raise ValueError(f"Invalid value for {option}")
    else:
        raise ValueError(f"Unsupported yt-dlp advanced option: {option}")


def validate_extra_args(args: list[str]) -> list[str]:
    """Return reviewed yt-dlp options, never positional sources or executables.

    Each argument must be a standalone safe flag or --name=value. Separate
    value tokens would otherwise be indistinguishable from extra download
    targets, including internal HTTP URLs bypassing normal job validation.
    """
    if not isinstance(args, list):
        raise ValueError("extra_args must be a list of strings")
    if len(args) > 40:
        raise ValueError("At most 40 extra arguments are allowed")
    cleaned: list[str] = []
    for raw in args:
        if not isinstance(raw, str) or len(raw) > 512 or "\x00" in raw:
            raise ValueError("Each extra argument must be a string of at most 512 characters")
        option, separator, value = raw.partition("=")
        if option in SENSITIVE_OPTION_NAMES:
            raise ValueError(f"{option} is managed by the service and cannot be overridden")
        if not option.startswith("--") or option == "--":
            raise ValueError("Positional yt-dlp arguments are prohibited; use --option=value")
        if option in SAFE_YTDLP_FLAGS:
            if separator:
                raise ValueError(f"{option} is a flag and must not have a value")
        elif separator:
            _validate_ytdlp_option_value(option, value)
        else:
            raise ValueError(f"Unsupported yt-dlp advanced option: {option}; use --option=value for supported value options")
        cleaned.append(raw)
    return cleaned


def resolve_http_target(url: str, allow_private: bool = False) -> str | None:
    """Resolve a curl HTTP(S) target and return a pinned --resolve rule.

    Literal IPs require no DNS pin. All DNS answers must be global when
    private targets are disabled; mixing a public and private answer is unsafe.
    The caller must disable proxies and redirects to any unvalidated host.
    This applies to curl only, not to the other engine subprocesses.
    """
    parsed = _validated_urlsplit(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
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
    if not allow_private:
        _reject_noncanonical_ipv4(_canonical_host(host))
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
        raise SourceResolutionError("Could not resolve target hostname") from exc
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
        raise SourceResolutionError("DNS resolution returned no IP addresses")
    # curl requires IPv6 addresses inside brackets.
    encoded = ",".join(
        f"[{ip}]" if ip.version == 6 else str(ip)
        for ip in sorted(addresses, key=lambda ip: (ip.version, int(ip)))
    )
    return f"{host}:{port}:{encoded}"


def preflight_source_dns(url: str) -> None:
    """Reject private DNS answers immediately before an unpinned engine starts.

    For HTTP(S), reuse the curl first-hop DNS policy. Aria2's FTP/FTPS
    sources receive equivalent A/AAAA checks. The subprocess still resolves
    addresses independently, so redirects, DNS changes, playlists, magnets,
    and extracted media URLs REQUIRE deployment-level egress controls.
    """
    parsed = _validated_urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        resolve_http_target(url, False)
        return
    if scheme not in {"ftp", "ftps"}:
        return
    validate_target_url(url, "aria2c", False)
    assert parsed.hostname is not None
    host = _canonical_host(parsed.hostname)
    port = parsed.port or (990 if scheme == "ftps" else 21)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise UnsafeUrlError("FTP source uses a private or reserved address")
        return
    try:
        records = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, OSError) as exc:
        raise UnsafeUrlError("Could not resolve FTP source hostname") from exc
    if not records:
        raise UnsafeUrlError("FTP source DNS returned no IP addresses")
    for record in records:
        try:
            address = ipaddress.ip_address(record[4][0])
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafeUrlError("FTP source DNS returned an invalid address") from exc
        if not address.is_global:
            raise UnsafeUrlError("FTP source DNS includes a private or reserved address")
