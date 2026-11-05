"""Regression coverage for ambiguous URL spellings and numeric-IP aliases.

Some downloader clients accept historical IPv4 notations that ipaddress
does not parse. Protect all engines at the common entry point without DNS.
"""
from __future__ import annotations

import socket

import pytest

from app.security import UnsafeUrlError, resolve_http_target, validate_target_url


@pytest.mark.parametrize("engine", ["yt-dlp", "aria2c", "gallery-dl", "generic", "you-get", "svtplay-dl", "get-sauce"])
@pytest.mark.parametrize("host", [
    "127.1",
    "2130706433",
    "0x7f000001",
    "0177.0.0.1",
    "169.254.43518",
])
def test_all_engines_reject_ambiguous_numeric_hosts_without_dns(monkeypatch, host, engine):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: pytest.fail("Must not do a network lookup"))
    with pytest.raises(UnsafeUrlError, match="Noncanonical IPv4"):
        validate_target_url(f"http://{host}/private", engine)


@pytest.mark.parametrize("url", [
    "http://127.1/private",
    "http://0x7f000001/admin",
    "https://2130706433/",
])
def test_curl_dns_pinning_rejects_legacy_numeric_aliases_before_lookup(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_kw: pytest.fail("Must not do a network lookup"))
    with pytest.raises(UnsafeUrlError, match="Noncanonical IPv4"):
        resolve_http_target(url)


@pytest.mark.parametrize("url", [
    "https://example.com:0/path",
    "https://example.com:65536/file",
    "https://example.com:abc/video",
    "https://example.com:/file",
    "https://example.com/%0Afile\n",
    "https://example.com/hello\rworld",
    "https://example.com/path\tbroken",
    "http://example.com\\@private.example/path",
    "https://example%2Ecom/path",
])
def test_rejects_ambiguous_authority_or_raw_control_characters(url):
    with pytest.raises(UnsafeUrlError):
        validate_target_url(url, "yt-dlp")
    with pytest.raises(UnsafeUrlError):
        resolve_http_target(url)


@pytest.mark.parametrize("url", [
    "https://1.1.1.1/video",
    "https://example.com/path",
    "https://media.example.org:8443/file",
])
def test_standard_uris_remain_accepted_by_common_validator(url):
    assert validate_target_url(url, "yt-dlp") == url


def test_trusted_private_mode_is_explicit_and_does_not_disable_url_syntax_checks():
    assert validate_target_url("http://127.1/file", "yt-dlp", allow_private=True) == "http://127.1/file"
    with pytest.raises(UnsafeUrlError):
        validate_target_url("http://127.1:0/file", "yt-dlp", allow_private=True)
