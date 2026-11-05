"""Engine logs must not persist signed URLs with arbitrary secret parameters."""
from __future__ import annotations

from app.worker import _redact


def test_signed_urls_with_unknown_query_keys_are_scrubbed():
    text = (
        "Failed https://cdn.example/video.mp4?X-Amz-Credential=secret-aws&Expires=1234 "
        "then https://other.example/file?private_vendor_signature=secret-vendor#hidden-fragment"
    )
    result = _redact(text)
    assert "https://cdn.example/video.mp4?[REDACTED]" in result
    assert "https://other.example/file?[REDACTED]" in result
    for secret in ("secret-aws", "secret-vendor", "hidden-fragment", "Expires=1234"):
        assert secret not in result
    assert result.startswith("Failed ")
    assert " then " in result


def test_url_authority_credentials_are_removed_for_http_and_ftp():
    text = (
        "HTTP https://operator:password123@example.org/file.mp4 "
        "FTP ftps://user:password456@files.example.org/private?custom=hidden789"
    )
    result = _redact(text)
    assert "https://[REDACTED]@example.org/file.mp4" in result
    assert "ftps://[REDACTED]@files.example.org/private?[REDACTED]" in result
    for secret in ("password123", "password456", "hidden789", "operator", "user:password"):
        assert secret not in result


def test_nonurl_credentials_still_scrubbed_and_output_stays_bounded():
    text = "authorization: BasicSecret token=12345 password=abcde cookie: value"
    result = _redact(text)
    for secret in ("BasicSecret", "12345", "abcde", "value"):
        assert secret not in result
    assert len(_redact("z" * 10000)) <= 4000


def test_queryless_errors_remain_readable():
    text = "Connection timed out at https://cdn.example/public/path without private data"
    assert _redact(text) == text
