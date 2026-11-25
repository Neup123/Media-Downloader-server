import asyncio
import functools
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from app.adapters.gallery_dl import run_gallery_dl
from app.gallery_preview import summarize_gallery_messages
from app.gallery_selection import gallery_identities, gallery_url_identity
from app.main import validate_engine_settings


def test_identity_selection_job_only_and_strict(client, auth_headers):
    identities = [gallery_url_identity("https://cdn.example/a.jpg"), gallery_url_identity("https://cdn.example/b.jpg")]
    payload = {"engine": "gallery-dl", "url": "https://example.org/album",
               "overrides": {"gallery_item_ids": identities}}
    result = client.post("/api/v1/jobs", json=payload, headers=auth_headers)
    assert result.status_code == 202, result.text
    assert result.json()["overrides"]["gallery_item_ids"] == sorted(identities)
    for value in ([], [identities[0]] * 2, ["a" * 63], ["--exec"], "a" * 64, ["a" * 64] * 201):
        assert client.post("/api/v1/jobs", headers=auth_headers, json={
            **payload, "overrides": {"gallery_item_ids": value},
        }).status_code == 422
    for extra in ({"gallery_items": "1"}, {"cookies_file": "test.txt"},
                  {"options": {"extractor.file-range": "1"}}):
        assert client.post("/api/v1/jobs", headers=auth_headers, json={
            **payload, "overrides": {"gallery_item_ids": identities, **extra},
        }).status_code == 422
    with pytest.raises(ValueError):
        validate_engine_settings("gallery-dl", {"gallery_item_ids": identities})


def test_real_pinned_gallery_identity_follows_files_not_reordered_positions(tmp_path, monkeypatch):
    executable = shutil.which("gallery-dl")
    assert executable, "Pinned gallery-dl must be installed"
    site = tmp_path / "site"
    site.mkdir()
    for name in ("a", "b", "c"):
        (site / (name + ".jpg")).write_bytes(("fixture-" + name).encode())
    # The upstream generic extractor does not parse explicit ports. Register a
    # synthetic extractor via its actual add() interface in the test child
    # only. The pinned CLI, Job.dispatch, DownloadJob and HTTP downloader are
    # real, including the production identity wrapper and output verification.
    hooks = tmp_path / "fixture-hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "from gallery_dl import extractor\n"
        "from gallery_dl.extractor.common import Extractor, Message\n"
        "class FixtureExtractor(Extractor):\n"
        "    category = 'identityfixture'\n"
        "    subcategory = 'gallery'\n"
        "    pattern = r'fixture:(https?://.+)'\n"
        "    def items(self):\n"
        "        source = self.groups[0]\n"
        "        root = source.rsplit('/', 1)[0]\n"
        "        yield Message.Directory, '', {'title': 'Test'}\n"
        "        for name in self.request(source).json():\n"
        "            yield Message.Url, root + '/' + name + '.jpg', {'filename': name, 'extension': 'jpg'}\n"
        "extractor.add(FixtureExtractor)\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(hooks) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    page = site / "album.json"
    def set_page(names):
        page.write_text(json.dumps(list(names)))
    set_page("ab")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_): pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"fixture:http://127.0.0.1:{server.server_port}/album.json"
    async def noop(_): pass
    async def no(): return False
    def run(identities, folder):
        return asyncio.run(run_gallery_dl(executable=executable, url=url,
            workdir=tmp_path / folder, global_settings={}, overrides={"gallery_item_ids": identities},
            cookie_dir=tmp_path / "cookies", on_progress=noop, on_log=noop, is_cancelled=no))
    try:
        raw = subprocess.check_output([executable, "--config-ignore", "--no-input",
                                       "--dump-json", "--", url], timeout=25)
        preview = summarize_gallery_messages(json.loads(raw), 10)
        ids = [entry["selection_id"] for entry in preview["entries"]]
        assert len(ids) == 2 and len(set(ids)) == 2
        set_page("cba")
        files = run(ids, "reordered")
        assert {p.read_bytes() for p in files} == {b"fixture-a", b"fixture-b"}
        set_page("ca")
        with pytest.raises(RuntimeError):
            run(ids, "missing")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
