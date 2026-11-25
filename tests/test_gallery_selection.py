import asyncio

import pytest

from app.adapters.gallery_dl import run_gallery_dl
from app.gallery_selection import gallery_selection, validate_gallery_selection_settings
from app.main import validate_engine_settings


@pytest.mark.parametrize("value", ["", "0", "1101", "3-1", "1,1", "1-3,2",
                                  "1, 2", "-1", "1;2", 1, None])
def test_gallery_range_rejects_invalid_or_ambiguous_values(value):
    with pytest.raises(ValueError):
        gallery_selection(value)


def test_gallery_range_normalizes_and_bounds_source_order():
    assert gallery_selection("5,1,3-4") == ("1,3-5", 4)
    assert gallery_selection("1-1100") == ("1-1100", 1100)


def test_gallery_selection_is_job_only(client, auth_headers):
    request = {"url": "https://example.com/album", "engine": "gallery-dl",
               "overrides": {"gallery_items": "3,1"}}
    ok = client.post("/api/v1/jobs", headers=auth_headers, json=request)
    assert ok.status_code == 202, ok.text
    assert ok.json()["overrides"]["gallery_items"] == "1,3"
    for overrides in ({"gallery_items": "1,1"},
                      {"gallery_items": "1", "options": {"extractor.file-range": "2"}},
                      {"gallery_items": "1", "cookies_file": "account.txt"}):
        bad = client.post("/api/v1/jobs", headers=auth_headers,
                          json={**request, "overrides": overrides})
        assert bad.status_code == 422
    with pytest.raises(ValueError, match="Unknown"):
        validate_engine_settings("gallery-dl", {"gallery_items": "1"}, partial=True)
    assert validate_gallery_selection_settings({"gallery_items": "1",
                                               "options": {"extractor.sleep": "2"}}) == ("1", 1)


def test_adapter_uses_explicit_range_and_fails_closed_on_missing_output(tmp_path):
    executable = tmp_path / "fake-gallery-dl"
    executable.write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys\n"
        "assert sys.argv[sys.argv.index('--range')+1] == '1,3'\n"
        "assert sys.argv[-2] == '--'\n"
        "pathlib.Path('first.jpg').write_bytes(b'a')\n"
        "if 'complete' in sys.argv[-1]: pathlib.Path('third.jpg').write_bytes(b'b')\n"
    )
    executable.chmod(0o755)

    async def progress(_): pass
    async def log(_): pass
    async def cancelled(): return False

    def download(url, folder):
        return asyncio.run(run_gallery_dl(
            executable=str(executable), url=url, workdir=tmp_path / folder,
            global_settings={}, overrides={"gallery_items": "3,1"},
            cookie_dir=tmp_path / "cookies", on_progress=progress, on_log=log,
            is_cancelled=cancelled,
        ))

    assert [p.name for p in download("https://example.com/complete", "full")] == [
        "first.jpg", "third.jpg"]
    with pytest.raises(RuntimeError, match="no files will be delivered"):
        download("https://example.com/partial", "partial")
