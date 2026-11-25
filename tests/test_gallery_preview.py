from __future__ import annotations

import asyncio
import json

import pytest
from gallery_dl.extractor.message import Message

from app.discovery import PreviewError
from app.gallery_preview import inspect_gallery_dl, summarize_gallery_messages
from app.schemas import MediaPreviewOut


def test_flat_gallery_metadata_contract_and_no_source_url_leak():
    messages = [
        [Message.Directory, {"title": "Album", "cookie": "private-cookie"}],
        [Message.Url, "https://cdn.example/image.jpg?token=private-url",
         {"filename": "first", "filesize": 100,
          "thumbnail": "https://cdn.example/preview.jpg"}],
        [Message.Url, "https://cdn.example/second.jpg",
         {"title": "Second", "thumbnail": "https://cdn.example/preview.jpg?token=private-thumb",
          "filesize": True}],
        [Message.Url, "https://cdn.example/third.jpg", {}],
    ]
    page = summarize_gallery_messages(messages, 2, 100)
    MediaPreviewOut.model_validate(page)
    assert page["extractor"] == "gallery-dl"
    assert page["entry_count"] is None
    assert [e["index"] for e in page["entries"]] == [101, 102]
    assert page["entries"][0]["size_bytes"] == 100
    assert page["entries"][0]["thumbnail"] == "https://cdn.example/preview.jpg"
    assert page["entries"][1]["thumbnail"] is None
    assert page["entries"][1]["size_bytes"] is None
    assert page["has_more"] and page["next_offset"] == 102
    assert "private-" not in json.dumps(page)
    assert "second.jpg" not in json.dumps(page)


@pytest.mark.parametrize("messages", [
    None, [], [[-1, {"message": "private raw error"}]],
    [[Message.Queue, "https://example.org/chapter", {}]],
    [[Message.Url, "https://example.org/file", None]],
    [[Message.Url, "https://example.org/file", {}]] * 3,
])
def test_invalid_nested_empty_or_out_of_range_gallery_fails_closed(messages):
    with pytest.raises(PreviewError) as exc:
        summarize_gallery_messages(messages, 1)
    assert "private raw error" not in str(exc.value)


def test_gallery_cli_flags_window_and_timeout(tmp_path):
    executable = tmp_path / "fake-gallery-dl"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert '--dump-json' in sys.argv and '--resolve-json' not in sys.argv\n"
        "assert '--config-ignore' in sys.argv and '--no-input' in sys.argv\n"
        "assert sys.argv[sys.argv.index('--range')+1] == '101-103'\n"
        "print(json.dumps([[3, 'https://cdn.example/a.jpg', {'filename':'A'}], "
        "[3, 'https://cdn.example/b.jpg', {'filename':'B'}]]))\n"
    )
    executable.chmod(0o755)
    result = asyncio.run(inspect_gallery_dl(
        "https://example.org/album", executable=str(executable), max_entries=2, offset=100))
    assert [e["index"] for e in result["entries"]] == [101, 102]
    assert not result["has_more"]
    assert summarize_gallery_messages([], 2, 102)["entries"] == []
    executable.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
    with pytest.raises(PreviewError, match="timed out"):
        asyncio.run(inspect_gallery_dl(
            "https://example.org/album", executable=str(executable), timeout_seconds=0.1))


def test_gallery_cli_rejects_private_source_and_unbounded_offset():
    with pytest.raises(ValueError):
        asyncio.run(inspect_gallery_dl("https://example.org/album", offset=1001))
    with pytest.raises(ValueError):
        asyncio.run(inspect_gallery_dl("http://127.0.0.1/private"))


def test_gallery_output_limit_terminates_child_without_waiting_on_full_pipe(tmp_path):
    executable = tmp_path / "noisy-gallery-dl"
    executable.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('x' * 4000000)\nsys.stdout.flush()\n")
    executable.chmod(0o755)

    async def bounded():
        return await asyncio.wait_for(inspect_gallery_dl(
            "https://example.org/album", executable=str(executable)), timeout=8)

    with pytest.raises(PreviewError, match="output limit"):
        asyncio.run(bounded())


def test_authenticated_gallery_preview_dispatch_and_window(client, auth_headers, monkeypatch):
    from app.gallery_preview import summarize_gallery_messages
    from gallery_dl.extractor.message import Message

    calls = []
    async def gallery(url, *, max_entries, offset):
        calls.append(("gallery", url, max_entries, offset))
        return summarize_gallery_messages([
            [Message.Directory, {"title": "Album"}],
            [Message.Url, "https://cdn.example/private.jpg?token=hidden",
             {"filename": "First", "filesize": 100}],
        ], max_entries, offset)

    async def ytdlp(url, *, max_entries, offset):
        calls.append(("yt-dlp", url, max_entries, offset))
        from app.discovery import summarize_metadata
        return summarize_metadata({"title": "Video", "id": "video"}, max_entries, offset)

    dns = []
    monkeypatch.setattr("app.main.inspect_gallery_dl", gallery)
    monkeypatch.setattr("app.main.inspect_ytdlp", ytdlp)
    monkeypatch.setattr("app.main.preflight_source_dns", dns.append)
    body = {"url": "https://example.com/album", "engine": "gallery-dl",
            "max_entries": 20, "offset": 100}
    assert client.post("/api/v1/preview", json=body).status_code == 401
    for bad in ({**body, "offset": 1001}, {**body, "engine": "generic"},
                {**body, "url": "http://127.0.0.1/private"}):
        assert client.post("/api/v1/preview", headers=auth_headers, json=bad).status_code == 422
    assert not calls
    result = client.post("/api/v1/preview", headers=auth_headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["extractor"] == "gallery-dl"
    assert result.json()["kind"] == "playlist"
    assert result.json()["entries"][0]["index"] == 101
    assert result.json()["entries"][0]["size_bytes"] == 100
    assert "private.jpg" not in result.text and "token=hidden" not in result.text
    assert calls == [("gallery", body["url"], 20, 100)]
    assert dns == [body["url"]]
    default = client.post("/api/v1/preview", headers=auth_headers,
                          json={"url": "https://example.com/video"})
    assert default.status_code == 200
    assert default.json()["kind"] == "video"
    assert calls[-1][0] == "yt-dlp"
