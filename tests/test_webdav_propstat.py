"""PROPFIND verification must not treat an unrelated or failed 207 property as delivery proof."""

from __future__ import annotations

from xml.sax.saxutils import escape

import pytest

from app.delivery import _webdav_uploaded_size


TARGET = "https://cloud.example.test/remote.php/dav/files/u/Downloader/job/album%20one/image.jpg"
PATH = "/remote.php/dav/files/u/Downloader/job/album%20one/image.jpg"


def _response(href: str, size: int = 7, status: int = 200) -> str:
    return (
        "<d:response><d:href>" + escape(href) + "</d:href>"
        "<d:propstat><d:prop><d:getcontentlength>" + str(size) +
        "</d:getcontentlength></d:prop>"
        "<d:status>HTTP/1.1 " + str(status) + " Test</d:status>"
        "</d:propstat></d:response>"
    )


def _multistatus(*responses: str) -> bytes:
    return ("<d:multistatus xmlns:d='DAV:'>" + "".join(responses) + "</d:multistatus>").encode()


@pytest.mark.parametrize("href", [TARGET, PATH, PATH.replace("%20", "%20")])
def test_accepts_successful_property_only_for_target(href):
    assert _webdav_uploaded_size(_multistatus(_response(href, size=17)), TARGET) == 17


def test_rejects_matching_size_for_unrelated_file():
    assert _webdav_uploaded_size(_multistatus(_response(PATH + ".other", size=17)), TARGET) is None


def test_rejects_not_found_property_even_when_size_matches():
    assert _webdav_uploaded_size(_multistatus(_response(PATH, size=17, status=404)), TARGET) is None


def test_rejects_other_file_success_and_target_property_failure():
    xml = _multistatus(_response(PATH + ".other", 17), _response(PATH, 17, 403))
    assert _webdav_uploaded_size(xml, TARGET) is None


def test_rejects_ambiguous_duplicate_successful_property_values():
    xml = _multistatus(_response(PATH, 17), _response(PATH, 17))
    assert _webdav_uploaded_size(xml, TARGET) is None


def test_rejects_missing_href_or_status():
    without_href = b"<d:multistatus xmlns:d='DAV:'><d:response><d:propstat><d:prop><d:getcontentlength>17</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    without_status = _multistatus(_response(PATH, 17).replace("<d:status>HTTP/1.1 200 Test</d:status>", ""))
    assert _webdav_uploaded_size(without_href, TARGET) is None
    assert _webdav_uploaded_size(without_status, TARGET) is None


def test_rejects_invalid_or_oversized_xml():
    assert _webdav_uploaded_size(b"<d:multistatus", TARGET) is None
    assert _webdav_uploaded_size(b"x" * 1_000_001, TARGET) is None
