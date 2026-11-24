"""Bounded, header-only direct-file metadata using curl's pinned public DNS path.

The probe performs one HEAD request, never follows redirects or reads a
response body, and never forwards cookies. Inbound URL syntax and DNS must
pass the same policy as Core's generic direct download engine. This is an
information aid, NOT a substitute for target-host egress isolation.
"""
from __future__ import annotations

import asyncio
import re

from app.adapters.process_env import downloader_environment
from app.security import resolve_http_target, validate_target_url

MAX_HEADERS = 16_384
SAFE_BYTES_MAX = 9_007_199_254_740_991


def parse_head_response(raw: bytes) -> dict[str, object]:
    """Return only explicitly observed safe fields from one successful HEAD."""
    text = raw.decode("iso-8859-1")
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    blocks = [block for block in blocks if block.startswith("HTTP/")]
    if len(blocks) != 1:
        raise ValueError("Unexpected or redirected HEAD response")
    lines = blocks[0].splitlines()
    status = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})(?:\s|$)", lines[0])
    if not status or not 200 <= int(status.group(1)) < 300:
        raise ValueError("HEAD did not return a successful direct-file response")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in {"content-type", "content-length", "content-disposition"}:
            # Repeated conflicting headers must not be interpreted arbitrarily.
            if key in headers:
                raise ValueError("Ambiguous file metadata headers")
            headers[key] = value.strip()
    mime = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime and (len(mime) > 160 or re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime) is None):
        mime = ""
    size: int | None = None
    length = headers.get("content-length", "")
    if length.isascii() and length.isdecimal():
        parsed = int(length)
        if parsed <= SAFE_BYTES_MAX:
            size = parsed
    filename: str | None = None
    disposition = headers.get("content-disposition", "")
    # Plain quoted filename only. RFC 5987 filename* and parameterized escapes
    # are intentionally not interpreted without a full trusted parser.
    match = re.search(r'(?:^|;)\s*filename="([^"]{1,200})"\s*(?:;|$)', disposition, re.I)
    if match:
        candidate = match.group(1)
        if candidate not in {".", ".."} and not candidate.startswith(".") and \
                "/" not in candidate and "\\" not in candidate and \
                all(32 <= ord(char) < 127 for char in candidate):
            filename = candidate
    return {"filename": filename, "mime": mime or None, "size": size}


async def _read_bounded_headers(stream: asyncio.StreamReader) -> bytes:
    data = bytearray()
    while True:
        chunk = await stream.read(MAX_HEADERS + 1 - len(data))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > MAX_HEADERS:
            raise ValueError("Direct-file HEAD headers exceed the size limit")


async def inspect_direct_head(url: str, allow_private_targets: bool = False) -> dict[str, object]:
    """Inspect only headers; fail if redirects, DNS policy or sizes are unsafe."""
    clean_url = validate_target_url(url, "generic", allow_private_targets)
    pin = await asyncio.to_thread(resolve_http_target, clean_url, allow_private_targets)
    argv = [
        "curl", "--disable", "--head", "--silent", "--show-error", "--globoff",
        "--proto", "=http,https", "--proto-redir", "=http,https",
        "--connect-timeout", "5", "--max-time", "12",
        "--noproxy", "*", "--proxy", "",
    ]
    if pin:
        argv.extend(["--resolve", pin])
    argv.append(clean_url)
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=downloader_environment(),
    )
    try:
        assert proc.stdout is not None
        # A malicious origin cannot force unbounded header buffering.
        # StreamReader.read(n) may return fewer than n bytes before EOF.
        # Accumulate bounded chunks under ONE deadline, rather than treating
        # a second chunk as an oversized response.
        raw = await asyncio.wait_for(_read_bounded_headers(proc.stdout), timeout=15)
        result = await asyncio.wait_for(proc.wait(), timeout=3)
        if result != 0:
            raise ValueError("Could not check direct-file headers")
        return parse_head_response(raw)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
