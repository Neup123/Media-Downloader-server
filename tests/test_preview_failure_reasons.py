"""Preview reasons are additive; no error prose is used for classification."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from yt_dlp.networking.exceptions import HTTPError as YtdlpHTTPError
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from app.discovery import PreviewError
from app.preview_probe import classify_extraction_failure
from app.security import SourceResolutionError, UnsafeUrlError


SOURCE = "https://media.example/video/12"


def _source_http_error(status: int, url: str = SOURCE):
    response = SimpleNamespace(status=status, reason="Not Found", url=url)
    return YtdlpHTTPError(response)


@pytest.mark.parametrize("status", [404, 410])
def test_source_http_not_found_is_typed_even_inside_extractor_failure(status):
    error = _source_http_error(status)
    wrapped = DownloadError("metadata extraction failed", exc_info=(type(error), error, None))
    assert classify_extraction_failure(
        wrapped, source_url=SOURCE, source_fetched=False, dedicated_claim=True,
    ) == "item_not_found"


def test_unrelated_404_is_not_proof_that_source_item_is_missing():
    assert classify_extraction_failure(
        _source_http_error(404, "https://assets.example/thumbnail.jpg"),
        source_url=SOURCE, source_fetched=False, dedicated_claim=True,
    ) == "could_not_check"


def test_engine_no_handler_is_unsupported_only_without_fetched_page_or_claim():
    assert classify_extraction_failure(
        UnsupportedError(SOURCE), source_url=SOURCE,
        source_fetched=False, dedicated_claim=False,
    ) == "unsupported_domain"
    # A site-specific extractor claimed the URL; an unsupported sub-resource
    # does not prove that the host itself is unsupported.
    assert classify_extraction_failure(
        UnsupportedError(SOURCE), source_url=SOURCE,
        source_fetched=False, dedicated_claim=True,
    ) == "could_not_check"


def test_fetched_source_page_with_no_media_is_not_media():
    assert classify_extraction_failure(
        UnsupportedError(SOURCE), source_url=SOURCE,
        source_fetched=True, dedicated_claim=False,
    ) == "not_media"


def test_ambiguous_extractor_and_network_failures_do_not_guess():
    for error in (
        ExtractorError("This video is private", expected=True),
        ExtractorError("Video was deleted", expected=True),
        DownloadError("An unknown failure occurred"),
        TimeoutError("timeout"),
        _source_http_error(503),
    ):
        assert classify_extraction_failure(
            error, source_url=SOURCE, source_fetched=False, dedicated_claim=True,
        ) == "could_not_check"


@pytest.mark.parametrize(
    ("url", "expected_detail"),
    [
        ("file:///etc/passwd", "yt-dlp does not accept the file URL scheme"),
        ("http://127.0.0.1/private", "Private, local, and reserved IP addresses are blocked"),
    ],
)
def test_preview_url_safety_keeps_detail_and_422(client, auth_headers, url, expected_detail):
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": url})
    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail, "reason_code": "malformed_link"}


@pytest.mark.parametrize("reason_code", [
    "unsupported_domain", "item_not_found", "not_media", "could_not_check",
])
def test_structured_extractor_failure_keeps_original_detail(client, auth_headers, monkeypatch, reason_code):
    async def fake_preview(_url, *, max_entries, offset):
        raise PreviewError("yt-dlp metadata inspection failed (exit 1)", reason_code)

    monkeypatch.setattr("app.main.inspect_ytdlp", fake_preview)
    monkeypatch.setattr("app.main.preflight_source_dns", lambda _url: None)
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": SOURCE})
    assert response.status_code == 422
    assert response.json() == {
        "detail": "yt-dlp metadata inspection failed (exit 1)",
        "reason_code": reason_code,
    }


def test_dns_lookup_error_is_a_check_failure_not_bad_input(client, auth_headers, monkeypatch):
    def reject(_url):
        raise SourceResolutionError("Could not resolve target hostname")

    monkeypatch.setattr("app.main.preflight_source_dns", reject)
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": SOURCE})
    assert response.status_code == 422
    assert response.json() == {
        "detail": "Could not resolve target hostname",
        "reason_code": "could_not_check",
    }


def test_dns_policy_rejection_is_malformed_link(client, auth_headers, monkeypatch):
    def reject(_url):
        raise UnsafeUrlError("DNS resolution includes a private or reserved address")

    monkeypatch.setattr("app.main.preflight_source_dns", reject)
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": SOURCE})
    assert response.status_code == 422
    assert response.json() == {
        "detail": "DNS resolution includes a private or reserved address",
        "reason_code": "malformed_link",
    }


def test_preview_schema_validation_keeps_fastapi_detail_list(client, auth_headers):
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": SOURCE, "offset": -1})
    assert response.status_code == 422
    assert set(response.json()) == {"detail", "reason_code"}
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"] == ["body", "offset"]
    assert response.json()["reason_code"] == "malformed_link"


def test_success_response_shape_is_unchanged(client, auth_headers, monkeypatch):
    expected = {
        "title": "Example", "kind": "video", "formats": [], "entries": [],
        "thumbnail": None, "has_more": False,
    }

    async def fake_preview(_url, *, max_entries, offset):
        return expected

    monkeypatch.setattr("app.main.inspect_ytdlp", fake_preview)
    monkeypatch.setattr("app.main.preflight_source_dns", lambda _url: None)
    response = client.post("/api/v1/preview", headers=auth_headers, json={"url": SOURCE})
    assert response.status_code == 200
    assert response.json() == expected
    assert "reason_code" not in response.json()


def test_preview_failure_spec_exposes_exact_vocabulary():
    from pathlib import Path
    import yaml

    spec = yaml.safe_load((Path(__file__).resolve().parents[1] / "openapi.yaml").read_text())
    response = spec["paths"]["/preview"]["post"]["responses"]["422"]
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PreviewFailure",
    }
    schema = spec["components"]["schemas"]["PreviewFailure"]
    assert schema["required"] == ["detail", "reason_code"]
    assert schema["properties"]["reason_code"]["enum"] == [
        "malformed_link", "unsupported_domain", "item_not_found", "not_media", "could_not_check",
    ]


def test_probe_records_successful_source_get_across_redirects(monkeypatch):
    from yt_dlp import YoutubeDL
    from app.preview_probe import SourceAwareYoutubeDL

    def fake_urlopen(_self, _request):
        return SimpleNamespace(url="https://redirect.example/landing", status=200)

    monkeypatch.setattr(YoutubeDL, "urlopen", fake_urlopen)
    with SourceAwareYoutubeDL(SOURCE, {"quiet": True}) as ydl:
        ydl.urlopen(SimpleNamespace(url="https://assets.example/image", method="GET"))
        assert not ydl.source_fetched
        ydl.urlopen(SimpleNamespace(url=SOURCE, method="HEAD"))
        assert not ydl.source_fetched
        ydl.urlopen(SimpleNamespace(url=SOURCE, method="GET"))
        assert ydl.source_fetched


def test_default_preview_child_reason_is_used_without_changing_detail(monkeypatch):
    import asyncio
    import sys

    from app.discovery import inspect_ytdlp

    class Stream:
        def __init__(self, content: bytes):
            self.content = content

        async def read(self, _limit):
            chunk, self.content = self.content, b""
            return chunk

    class Process:
        returncode = 1
        stdout = Stream(b"")
        stderr = Stream(b'{"reason_code":"item_not_found"}')

        async def wait(self):
            return 1

    async def subprocess(*argv, **kwargs):
        assert argv[:3] == (sys.executable, "-m", "app.preview_probe")
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        return Process()

    monkeypatch.setattr("app.discovery.asyncio.create_subprocess_exec", subprocess)
    with pytest.raises(PreviewError) as exc:
        asyncio.run(inspect_ytdlp(SOURCE))
    assert str(exc.value) == "yt-dlp metadata inspection failed (exit 1)"
    assert exc.value.reason_code == "item_not_found"


def test_unrecognized_child_diagnostic_falls_back_without_raw_error(monkeypatch):
    import asyncio
    from app.discovery import inspect_ytdlp

    class Stream:
        async def read(self, _limit):
            return b'{"reason_code":"engine_text_that_must_not_be_exposed"}'

    class EmptyStream:
        async def read(self, _limit):
            return b""

    class Process:
        returncode = 1
        stdout = EmptyStream()
        stderr = Stream()

        async def wait(self):
            return 1

    async def subprocess(*argv, **kwargs):
        return Process()

    monkeypatch.setattr("app.discovery.asyncio.create_subprocess_exec", subprocess)
    with pytest.raises(PreviewError) as exc:
        asyncio.run(inspect_ytdlp(SOURCE))
    assert exc.value.reason_code == "could_not_check"
    assert "engine_text" not in str(exc.value)


@pytest.fixture
def local_preview_source():
    """Purely local HTTP fixture: no public sites or live dependencies."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _respond(self, send_body: bool):
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/plain":
                body, content_type = b"<html><body>Just a page</body></html>", "text/html"
            else:
                body, content_type = b"not-a-real-mp4-download", "video/mp4"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_HEAD(self):
            self._respond(False)

        def do_GET(self):
            self._respond(True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_structured_probe_success_matches_existing_cli_preview(local_preview_source):
    import asyncio
    import shutil
    from app.discovery import inspect_ytdlp

    url = f"{local_preview_source}/video.mp4"
    existing_cli = shutil.which("yt-dlp")
    assert existing_cli is not None
    before = asyncio.run(inspect_ytdlp(url, executable=existing_cli))
    after = asyncio.run(inspect_ytdlp(url))
    # Public success metadata remains identical in both content and keys.
    assert after == before


@pytest.mark.parametrize(("path", "expected"), [
    ("/missing", "item_not_found"),
    ("/plain", "not_media"),
])
def test_real_http_origins_are_classified_without_scraping(local_preview_source, path, expected):
    import asyncio
    from app.discovery import inspect_ytdlp

    with pytest.raises(PreviewError) as exc:
        asyncio.run(inspect_ytdlp(f"{local_preview_source}{path}"))
    assert str(exc.value) == "yt-dlp metadata inspection failed (exit 1)"
    assert exc.value.reason_code == expected
