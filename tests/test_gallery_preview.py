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
