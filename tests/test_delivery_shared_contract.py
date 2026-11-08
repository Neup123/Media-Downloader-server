"""WebDAV parser, credential scrubbing and test/prod path parity."""
from app.delivery import (
    _webdav_uploaded_size, _nextcloud_file_id, _webdav_is_collection,
)
from app.redaction import _redact as shared_redact
from app.worker import _redact as worker_redact
from app.adapters.common_cli import _redact as cli_redact


def multistatus(href, property_xml, status=200):
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        '<d:response><d:href>' + href + '</d:href><d:propstat>'
        '<d:prop>' + property_xml + '</d:prop>'
        '<d:status>HTTP/1.1 ' + str(status) + ' OK</d:status>'
        '</d:propstat></d:response></d:multistatus>'
    ).encode()


def test_three_webdav_properties_share_exact_href_and_200_status():
    root = "https://cloud.example.org/remote.php/dav/files/alice"
    target = root + "/Sample%20Folder"
    props = (
        '<d:getcontentlength>17</d:getcontentlength>'
        '<oc:fileid>12345</oc:fileid>'
        '<d:resourcetype><d:collection/></d:resourcetype>'
    )
    good = multistatus("/remote.php/dav/files/alice/Sample%20Folder", props)
    assert _webdav_uploaded_size(good, target) == 17
    assert _nextcloud_file_id(good, target) == "12345"
    assert _webdav_is_collection(good, target)
    bad = multistatus("/remote.php/dav/files/bob/Sample%20Folder", props)
    assert _webdav_uploaded_size(bad, target) is None
    assert _nextcloud_file_id(bad, target) is None
    assert not _webdav_is_collection(bad, target)
    failed = multistatus("/remote.php/dav/files/alice/Sample%20Folder", props, 403)
    assert _webdav_uploaded_size(failed, target) is None
    assert _nextcloud_file_id(failed, target) is None
    assert not _webdav_is_collection(failed, target)


def test_all_downloader_logs_use_same_robust_secret_redactor():
    assert worker_redact is shared_redact
    assert cli_redact is shared_redact
    sample = (
        "download https://user:secret@example.org/a?X-Custom-Signed=private "
        "rpc-secret=hidden and app_password=other "
        "ftps://user2:password@host.example/f?q=secret-query"
    )
    result = cli_redact(sample)
    for secret in ("X-Custom-Signed", "private", "hidden", "other",
                   "password@host", "secret-query", "user:secret"):
        assert secret not in result
    assert "?[REDACTED]" in result


def test_destination_preflight_calls_real_deliver_files_path():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app/delivery.py").read_text()
    prefix = source.split("async def verify_delivery_destination(", 1)[1].split(
        "async def deliver_files(", 1
    )[0]
    assert "await deliver_files([source], job_id, session_factory, settings, transport=transport)" in prefix
    assert "client.put(" not in prefix
    assert "_ensure_webdav_collection(client" not in prefix
    assert "client.request(\"PROPFIND\"" not in prefix
