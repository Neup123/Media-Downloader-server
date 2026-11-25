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
