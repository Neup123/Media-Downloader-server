import asyncio
import sys

import pytest

from app.adapters.common_cli import run_cli
from app.engine_failures import (
    EngineFailure, EngineFailureKind, aria2_failure_kind, classify_cli_error, curl_failure_kind,
)
from app.user_failures import user_failure_message


@pytest.mark.parametrize("line,expected", [
    ("ERROR: 429 Too Many Requests token=private", EngineFailureKind.RATE_LIMITED),
    ("ERROR: login required https://private.invalid", EngineFailureKind.AUTH_REQUIRED),
    ("ERROR: 403 Forbidden", EngineFailureKind.ACCESS_DENIED),
    ("ERROR: 404 Not Found", EngineFailureKind.NOT_FOUND),
    ("ERROR: Unsupported URL", EngineFailureKind.UNSUPPORTED),
    ("ERROR: unknown private text", EngineFailureKind.UNKNOWN),
    ("INFO: 429 bytes downloaded", None),
])
def test_fixed_error_categories_do_not_reflect_diagnostics(line, expected):
    assert classify_cli_error(line) == expected
    if expected is not None:
        result = user_failure_message(EngineFailure(expected, line))
        assert "private" not in result and line not in result


def test_native_error_codes():
    assert curl_failure_kind(22, "curl: (22) The requested URL returned error: 404") == EngineFailureKind.NOT_FOUND
    assert curl_failure_kind(22, "unrelated 404") == EngineFailureKind.UNKNOWN
    assert curl_failure_kind(60, "") == EngineFailureKind.CERTIFICATE
    assert aria2_failure_kind("9") == EngineFailureKind.DISK_FULL
    assert aria2_failure_kind("999") == EngineFailureKind.UNKNOWN


def test_real_failed_cli_reports_category_not_raw_text(tmp_path):
    async def no_op(_): pass
    async def no(): return False
    logs = []
    async def log(line): logs.append(line)
    with pytest.raises(EngineFailure) as caught:
        asyncio.run(run_cli(name="fixture", argv=[sys.executable, "-c",
                         "print('ERROR: 429 Too Many Requests token=fixture-private'); raise SystemExit(3)"],
                         workdir=tmp_path, on_progress=no_op, on_log=log, is_cancelled=no))
    assert caught.value.kind == EngineFailureKind.RATE_LIMITED
    assert "limiting requests" in user_failure_message(caught.value)
    assert "fixture-private" not in user_failure_message(caught.value)
    assert any("429" in line for line in logs)


def test_missing_binary_and_no_output(tmp_path):
    async def no_op(_): pass
    async def no(): return False
    for argv, expected in [
        ([str(tmp_path / "nonexistent")], EngineFailureKind.MISSING_BINARY),
        ([sys.executable, "-c", "pass"], EngineFailureKind.NO_OUTPUT),
    ]:
        with pytest.raises(EngineFailure) as caught:
            asyncio.run(run_cli(name="fixture", argv=argv, workdir=tmp_path,
                                on_progress=no_op, on_log=no_op, is_cancelled=no))
        assert caught.value.kind == expected


def test_unknown_and_delivery_errors_preserve_safe_boundary():
    assert "private" not in user_failure_message(RuntimeError("private"))
    assert "file could not be saved" in user_failure_message(
        EngineFailure(EngineFailureKind.RATE_LIMITED, "private"), during_delivery=True)


@pytest.mark.parametrize("codes,expected,remedy", [
    (("2",), EngineFailureKind.TIMEOUT, "did not respond in time"),
    (("3", "4"), EngineFailureKind.NOT_FOUND, "could not be found"),
    (("5",), EngineFailureKind.SLOW_TRANSFER, "too slow"),
    (("6", "19"), EngineFailureKind.NETWORK, "could not reach"),
    (("7",), EngineFailureKind.INCOMPLETE, "before completion"),
    (("8", "10"), EngineFailureKind.RESUME_FAILED, "could not be resumed"),
    (("9",), EngineFailureKind.DISK_FULL, "insufficient disk space"),
    (("11", "12"), EngineFailureKind.DUPLICATE_TRANSFER, "already downloading"),
    (("13",), EngineFailureKind.FILE_CONFLICT, "already exists"),
    (("14", "15", "16", "17", "18"), EngineFailureKind.STORAGE_IO, "read or write"),
    (("20", "25", "26", "27"), EngineFailureKind.INVALID_METADATA, "invalid"),
    (("21", "22"), EngineFailureKind.SOURCE_PROTOCOL, "unexpected response"),
    (("23",), EngineFailureKind.REDIRECT_LIMIT, "redirected too many"),
    (("24",), EngineFailureKind.AUTH_REQUIRED, "valid authentication"),
    (("28", "30"), EngineFailureKind.ENGINE_CONFIGURATION, "Contact the administrator"),
    (("29",), EngineFailureKind.SOURCE_BUSY, "temporarily unavailable"),
    (("32",), EngineFailureKind.CHECKSUM_FAILED, "integrity check"),
])
def test_aria2_documented_failure_codes(codes, expected, remedy):
    for code in codes:
        assert aria2_failure_kind(code) == expected
        assert aria2_failure_kind(int(code)) == expected
        message = user_failure_message(EngineFailure(expected, "fixture diagnostic only"))
        assert remedy in message
        assert "fixture diagnostic only" not in message


@pytest.mark.parametrize("code", ["0", "1", "31", "999", "", None, True, "32x"])
def test_aria2_unknown_reserved_and_success_codes_remain_generic(code):
    category = aria2_failure_kind(code)
    assert category == EngineFailureKind.UNKNOWN
    assert user_failure_message(EngineFailure(category, "fixture diagnostic only")) == (
        "The download failed. Check the technical log or try again.")
