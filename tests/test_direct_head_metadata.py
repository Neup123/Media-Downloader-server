"""Regression contracts for bounded, no-body direct-file HEAD metadata."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from app.direct_metadata import MAX_HEADERS, _read_bounded_headers, parse_head_response, inspect_direct_head


def test_explicit_head_headers_are_sanitized_without_derived_values():
    result = parse_head_response(
        b'HTTP/1.1 200 OK\r\nContent-Type: application/pdf; charset=UTF-8\r\n'
        b'Content-Length: 120\r\nContent-Disposition: attachment; filename="report.pdf"\r\n\r\n'
    )
    assert result == {"filename": "report.pdf", "mime": "application/pdf", "size": 120}
    assert parse_head_response(b"HTTP/2 204 No Content\r\n\r\n") == {
        "filename": None, "mime": None, "size": None
    }


@pytest.mark.parametrize("headers", [
    b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/admin\r\n\r\n",
    b"HTTP/1.1 401 Unauthorized\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
    b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\n\r\n",
    b"garbage",
])
def test_failed_redirected_or_ambiguous_headers_never_become_a_file(headers):
    with pytest.raises(ValueError):
        parse_head_response(headers)


@pytest.mark.parametrize("raw,expected", [
    (b'HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n', None),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 9007199254740992\r\n\r\n', None),
    (b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n', 0),
])
def test_invalid_or_imprecise_bytes_are_not_exposed(raw, expected):
    assert parse_head_response(raw)["size"] == expected


def test_filename_disposition_is_not_allowed_to_escape_or_represent_paths():
    valid = b'HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename="safe.zip"\r\n\r\n'
    unsafe = b'HTTP/1.1 200 OK\r\nContent-Disposition: attachment; filename="../unsafe.zip"\r\n\r\n'
    assert parse_head_response(valid)["filename"] == "safe.zip"
    assert parse_head_response(unsafe)["filename"] is None


def test_metadata_endpoint_is_signed_and_does_not_leak_upstream_details(client, auth_headers, monkeypatch):
    assert client.post("/api/v1/direct-metadata", json={"url": "https://example.org/file.pdf"}).status_code == 401

    async def accepted(url, allow_private_targets):
        assert url == "https://example.org/file.pdf"
        assert allow_private_targets is False
        return {"filename": "approved.pdf", "mime": "application/pdf", "size": 42}
    monkeypatch.setattr("app.main.inspect_direct_head", accepted)
    response = client.post("/api/v1/direct-metadata", headers=auth_headers, json={"url":"https://example.org/file.pdf"})
    assert response.status_code == 200
    assert response.json() == {"filename":"approved.pdf", "mime":"application/pdf", "size":42}

    async def denied(_url, _allow_private):
        raise ValueError("http://private.example/secret?token=must-not-leak")
    monkeypatch.setattr("app.main.inspect_direct_head", denied)
    response = client.post("/api/v1/direct-metadata", headers=auth_headers, json={"url":"https://example.org/file.pdf"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Could not verify direct-file metadata"}


def test_actual_curl_probe_has_dns_pin_without_redirect_or_proxies(monkeypatch):
    class Pipe:
        values = [b"HTTP/1.1 200 OK\r\n", b"Content-Type: application/pdf\r\n", b"\r\n", b""]
        async def read(self, _size):
            return self.values.pop(0)
    class FakeProc:
        returncode = 0
        stdout = Pipe()
        async def wait(self): return 0
        def kill(self): raise AssertionError("Successful HEAD probe must not be killed")
    async def fake_subprocess(*argv, **kw):
        assert "--head" in argv
        assert "--location" not in argv and "-L" not in argv
        assert "--noproxy" in argv and "--proxy" in argv
        assert "--resolve" in argv and "example.org:443:93.184.216.34" in argv
        assert kw["stderr"] == asyncio.subprocess.DEVNULL
        return FakeProc()
    monkeypatch.setattr("app.direct_metadata.resolve_http_target", lambda *_: "example.org:443:93.184.216.34")
    monkeypatch.setattr("app.direct_metadata.asyncio.create_subprocess_exec", fake_subprocess)
    result = asyncio.run(inspect_direct_head("https://example.org/file.pdf"))
    assert result == {"filename":None,"mime":"application/pdf","size":None}


def test_openapi_describes_exact_direct_head_route():
    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    route = spec["paths"]["/direct-metadata"]["post"]
    assert "200" in route["responses"] and "422" in route["responses"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"]["required"] == [
        "filename", "mime", "size"
    ]


def test_actual_local_http_head_never_reads_payload_or_follows_redirects():
    """Real curl+HTTP localhost integration; no public site, cookies or downloads."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    counters = {"head": 0, "get": 0}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            counters["head"] += 1
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/internal")
                self.send_header("Content-Length", "0")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", "42")
                self.send_header("Content-Disposition", 'attachment; filename="sample.pdf"')
            self.end_headers()

        def do_GET(self):
            counters["get"] += 1
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        result = asyncio.run(inspect_direct_head(base + "/sample.pdf", True))
        assert result == {"filename":"sample.pdf", "mime":"application/pdf", "size":42}
        with pytest.raises(ValueError, match="successful direct-file response"):
            asyncio.run(inspect_direct_head(base + "/redirect", True))
        assert counters == {"head":2,"get":0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_bounded_header_reader_accepts_exact_limit_and_rejects_oversize():
    async def verify():
        exact = asyncio.StreamReader()
        exact.feed_data(b"x" * MAX_HEADERS)
        exact.feed_eof()
        assert await _read_bounded_headers(exact) == b"x" * MAX_HEADERS

        oversized = asyncio.StreamReader()
        oversized.feed_data(b"x" * (MAX_HEADERS + 1))
        oversized.feed_eof()
        with pytest.raises(ValueError, match="size limit"):
            await _read_bounded_headers(oversized)

    asyncio.run(verify())
