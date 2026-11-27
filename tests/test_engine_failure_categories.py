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


@pytest.mark.parametrize("codes,kind", [
    ((1,), EngineFailureKind.UNSUPPORTED),
    ((2, 4, 48, 53, 54, 59, 66, 77), EngineFailureKind.ENGINE_CONFIGURATION),
    ((3,), EngineFailureKind.INVALID_METADATA),
    ((5, 6, 7, 45, 55, 56), EngineFailureKind.NETWORK),
    ((16, 52, 61, 92, 95), EngineFailureKind.SOURCE_PROTOCOL),
    ((18,), EngineFailureKind.INCOMPLETE),
    ((23, 26), EngineFailureKind.STORAGE_IO),
    ((28,), EngineFailureKind.TIMEOUT),
    ((33, 36), EngineFailureKind.RESUME_FAILED),
    ((47,), EngineFailureKind.REDIRECT_LIMIT),
    ((51, 60, 83, 90, 91), EngineFailureKind.CERTIFICATE),
    ((67, 94), EngineFailureKind.AUTH_REQUIRED),
    ((78,), EngineFailureKind.NOT_FOUND),
    ((0, 20, 35, 98, 999, -9), EngineFailureKind.UNKNOWN),
])
def test_curl_documented_numeric_categories(codes, kind):
    for code in codes:
        assert curl_failure_kind(code, "synthetic-private-diagnostic") == kind
        assert "synthetic-private-diagnostic" not in user_failure_message(
            EngineFailure(kind, "synthetic-private-diagnostic"))


@pytest.mark.parametrize("status,kind", [
    (401, EngineFailureKind.AUTH_REQUIRED), (403, EngineFailureKind.ACCESS_DENIED),
    (404, EngineFailureKind.NOT_FOUND), (408, EngineFailureKind.TIMEOUT),
    (410, EngineFailureKind.NOT_FOUND), (429, EngineFailureKind.RATE_LIMITED),
    (502, EngineFailureKind.SOURCE_PROTOCOL), (503, EngineFailureKind.SOURCE_BUSY),
    (504, EngineFailureKind.TIMEOUT), (418, EngineFailureKind.UNKNOWN),
])
def test_curl_http_status_requires_native_error_line(status, kind):
    line = f"curl: (22) The requested URL returned error: {status}"
    assert curl_failure_kind(22, line) == kind
    assert curl_failure_kind(22, "unrelated " + line) == EngineFailureKind.UNKNOWN
    assert curl_failure_kind(22, line + " synthetic-private") == EngineFailureKind.UNKNOWN
    assert curl_failure_kind(999, line) == EngineFailureKind.UNKNOWN


@pytest.mark.parametrize("path,kind", [
    ("/410", EngineFailureKind.NOT_FOUND),
    ("/503", EngineFailureKind.SOURCE_BUSY),
    ("/partial", EngineFailureKind.INCOMPLETE),
    ("/redirect", EngineFailureKind.REDIRECT_LIMIT),
])
def test_real_curl_adapter_failure_against_loopback(tmp_path, path, kind):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from app.adapters.generic import run_generic

    executable = shutil.which("curl")
    if executable is None:
        pytest.skip("curl binary unavailable")

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/partial":
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"synthetic")
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/redirect")
                self.end_headers()
            else:
                self.send_response(int(self.path[1:]))
                self.end_headers()

    async def no_op(_):
        pass

    async def no():
        return False

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(EngineFailure) as caught:
                asyncio.run(run_generic(
                    executable=executable, url=f"http://127.0.0.1:{server.server_port}{path}",
                    workdir=tmp_path, global_settings={}, overrides={
                        "retry_count": 0, "follow_redirects": path == "/redirect", "max_redirects": 1,
                    }, on_progress=no_op, on_log=no_op, is_cancelled=no,
                    allow_private_targets=True,
                ))
            assert caught.value.kind == kind
            message = user_failure_message(caught.value)
            assert "127.0.0.1" not in message and "curl:" not in message
            assert message != "The download failed. Check the technical log or try again."
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("line,kind", [
    ("ERROR: [youtube] fixture: HTTP Error 403: Forbidden", EngineFailureKind.ACCESS_DENIED),
    ("[gallery][error] 401 Unauthorized", EngineFailureKind.AUTH_REQUIRED),
    ("you-get: [error] login required", EngineFailureKind.AUTH_REQUIRED),
    ("ERROR: Sign in to confirm you're not a bot", EngineFailureKind.AUTH_REQUIRED),
    ("ERROR: cookies are expired", EngineFailureKind.AUTH_REQUIRED),
    ("ERROR: HTTP status code 503", EngineFailureKind.SOURCE_BUSY),
    ("ERROR: HTTP Error 504", EngineFailureKind.TIMEOUT),
    ("ERROR: https://example.invalid/401?token=429", EngineFailureKind.UNKNOWN),
    ("ERROR: failed to load https://example.invalid/sign-in/404", EngineFailureKind.UNKNOWN),
    ("ERROR: fixture 429 frames", EngineFailureKind.UNKNOWN),
    ("INFO: error 429 in media title", None),
    ("[download] filename error 403.mp4", None),
    ("ERROR: unrelated operation; sign in the title", EngineFailureKind.UNKNOWN),
    ("ERROR: connection refused", EngineFailureKind.NETWORK),
])
def test_cli_explicit_diagnostic_marker_and_status_context(line, kind):
    assert classify_cli_error(line) == kind


@pytest.mark.parametrize("runner", ["shared", "yt-dlp", "gallery-dl"])
@pytest.mark.parametrize("last,kind", [
    ("ERROR: HTTP Error 503: Service Unavailable", EngineFailureKind.SOURCE_BUSY),
    ("ERROR: https://example.invalid/401?token=synthetic-private", EngineFailureKind.UNKNOWN),
])
def test_real_cli_runners_keep_last_explicit_category(tmp_path, runner, last, kind):
    from app.adapters.yt_dlp import _execute_ytdlp
    from app.adapters.gallery_dl import _execute_gallery_dl

    logs = []

    async def no_op(_):
        pass

    async def no():
        return False

    async def log(line):
        logs.append(line)

    lines = ["ERROR: login required", last, "INFO: error 429 in media title"]
    argv = [sys.executable, "-c", f"print({lines[0]!r}); print({lines[1]!r}); print({lines[2]!r}); raise SystemExit(3)"]
    with pytest.raises(EngineFailure) as caught:
        if runner == "shared":
            asyncio.run(run_cli(name="fixture", argv=argv, workdir=tmp_path,
                                on_progress=no_op, on_log=log, is_cancelled=no))
        else:
            execute = _execute_ytdlp if runner == "yt-dlp" else _execute_gallery_dl
            asyncio.run(execute(argv, tmp_path, no_op, log, no))
    assert caught.value.kind == kind
    assert "synthetic-private" not in user_failure_message(caught.value)
    assert "example.invalid" not in user_failure_message(caught.value)
    assert any("HTTP Error 503" in x or "example.invalid" in x for x in logs)


@pytest.mark.parametrize("runner", ["shared", "yt-dlp", "gallery-dl"])
@pytest.mark.parametrize("case", ["missing", "empty", "temporary", "reported_partial", "external_symlink", "valid"])
def test_cli_output_and_missing_binary_contract(tmp_path, runner, case):
    from app.adapters.yt_dlp import _execute_ytdlp
    from app.adapters.gallery_dl import _execute_gallery_dl

    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"synthetic outside file")
    scripts = {
        "empty": "pass",
        "temporary": (
            "from pathlib import Path; "
            "[Path(name).write_bytes(b'partial') for name in "
            "('x.part', 'x.tmp', 'x.download', 'x.ytdl', 'x.aria2', 'paired.mp4', 'paired.mp4.aria2')]"
        ),
        "reported_partial": (
            "from pathlib import Path; p = Path('clip.part'); "
            "p.write_bytes(b'partial'); print('__DL_FILE__' + str(p.resolve()))"
        ),
        "external_symlink": "pass",
        "valid": (
            "from pathlib import Path; p = Path('clip.mp4'); "
            "p.write_bytes(b'synthetic media'); print('__DL_FILE__' + str(p.resolve())); "
            "Path('clip.tmp').write_bytes(b'partial')"
        ),
    }
    if case == "external_symlink":
        (work / "link.mp4").symlink_to(outside)
    argv = [str(tmp_path / "absent-executable")] if case == "missing" else [
        sys.executable, "-c", scripts[case],
    ]

    async def no_op(_):
        pass

    async def no():
        return False

    async def execute():
        if runner == "shared":
            return await run_cli(name="fixture", argv=argv, workdir=work,
                                 on_progress=no_op, on_log=no_op, is_cancelled=no)
        adapter = _execute_ytdlp if runner == "yt-dlp" else _execute_gallery_dl
        return await adapter(argv, work, no_op, no_op, no)

    if case == "valid":
        assert asyncio.run(execute()) == [work / "clip.mp4"]
        assert (work / "clip.mp4").read_bytes() == b"synthetic media"
    else:
        with pytest.raises(EngineFailure) as caught:
            asyncio.run(execute())
        expected = EngineFailureKind.MISSING_BINARY if case == "missing" else EngineFailureKind.NO_OUTPUT
        assert caught.value.kind == expected
        message = user_failure_message(caught.value)
        assert ("not installed" if case == "missing" else "returned no file") in message
        assert str(tmp_path) not in message
    assert outside.read_bytes() == b"synthetic outside file"
