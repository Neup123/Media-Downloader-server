from __future__ import annotations

import asyncio
import socket

import pytest

from app.adapters.generic import run_generic
from app.security import UnsafeUrlError, resolve_http_target


def fake_dns(ip_list):
    def resolve(host, port, **kwargs):
        return [
            (socket.AF_INET6 if ":" in addr else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, port))
            for addr in ip_list
        ]
    return resolve


def test_public_dns_is_pinned_with_deterministic_address_order(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(["2606:4700:4700::1111", "1.1.1.1", "1.1.1.1"]))
    result = resolve_http_target("https://example.com/media")
    assert result == "example.com:443:1.1.1.1,[2606:4700:4700::1111]"


@pytest.mark.parametrize("private", [
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.2.4",
    "::1", "fc00::1", "fe80::1", "2001:db8::1",
])
def test_private_or_reserved_dns_replies_are_rejected(monkeypatch, private):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(["1.1.1.1", private]))
    with pytest.raises(UnsafeUrlError, match="private or reserved"):
        resolve_http_target("https://safe-looking.example/file")


def test_literal_ip_cannot_bypass_private_protection(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("Unexpected DNS lookup"))
    with pytest.raises(UnsafeUrlError):
        resolve_http_target("http://127.0.0.1/private")
    with pytest.raises(UnsafeUrlError):
        resolve_http_target("http://[::ffff:127.0.0.1]/")
    assert resolve_http_target("https://1.1.1.1/") is None


def test_resolution_errors_are_not_accepted(monkeypatch):
    def fail_dns(*a, **kw):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    with pytest.raises(UnsafeUrlError, match="Could not resolve"):
        resolve_http_target("https://missing.example/test")


def test_trailing_dot_hosts_cannot_escape_curl_resolve_mapping(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("Unexpected DNS lookup"))
    with pytest.raises(UnsafeUrlError, match="trailing dot"):
        resolve_http_target("https://example.com./file")


def test_private_target_exception_requires_explicit_operator_policy(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns(["10.0.0.5"]))
    assert resolve_http_target("http://media-source:9000/file", allow_private=True) == "media-source:9000:10.0.0.5"
    with pytest.raises(UnsafeUrlError):
        resolve_http_target("http://media-source:9000/file", allow_private=False)


def test_generic_redirects_cannot_bypass_safe_dns_pin(tmp_path, monkeypatch):
    monkeypatch.setattr("app.adapters.generic.resolve_http_target", lambda *a: pytest.fail("Unexpected lookup"))
    async def on_progress(_): pass
    async def on_log(_): pass
    async def is_cancelled(): return False
    with pytest.raises(UnsafeUrlError, match="redirects are disabled"):
        asyncio.run(run_generic(
            executable="curl",
            url="https://example.com/download",
            workdir=tmp_path,
            global_settings={},
            overrides={"follow_redirects": True},
            on_progress=on_progress,
            on_log=on_log,
            is_cancelled=is_cancelled,
        ))


def test_real_curl_download_uses_pinned_local_target_when_explicitly_trusted(tmp_path):
    import http.server
    import shutil
    import threading

    if shutil.which("curl") is None:
        pytest.skip("curl CLI not available")
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/sample.bin"
            payload = b"verified real curl transfer"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        async def on_progress(_): pass
        async def on_log(_): pass
        async def is_cancelled(): return False
        output = asyncio.run(run_generic(
            executable="curl", url=f"http://localhost:{server.server_port}/sample.bin",
            workdir=tmp_path / "download",
            global_settings={}, overrides={},
            on_progress=on_progress, on_log=on_log, is_cancelled=is_cancelled,
            allow_private_targets=True,
        ))
        assert len(output) == 1
        assert output[0].read_bytes() == b"verified real curl transfer"
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=2)
