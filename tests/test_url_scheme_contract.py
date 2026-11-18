"""URL-scheme capability contract must reflect the actual Core validator."""
import pytest
from app.security import UnsafeUrlError, supported_url_schemes, validate_target_url
from app.worker import ENGINE_DEFAULTS

def example_url(scheme):
    if scheme == "magnet":
        return "magnet:?xt=urn:btih:0123456789abcdef"
    return f"{scheme}://example.org/test"

def test_every_advertised_scheme_is_accepted_by_core_validator(client, auth_headers):
    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert names == set(ENGINE_DEFAULTS)
    for item in response.json():
        schemes = item["url_schemes"]
        assert schemes == supported_url_schemes(item["name"])
        assert schemes
        for scheme in schemes:
            url = example_url(scheme)
            assert validate_target_url(url, item["name"]) == url

@pytest.mark.parametrize("engine", list(ENGINE_DEFAULTS))
def test_sftp_is_never_advertised_or_accepted(engine):
    assert "sftp" not in supported_url_schemes(engine)
    with pytest.raises(UnsafeUrlError, match="does not accept"):
        validate_target_url("sftp://example.org/file", engine)

def test_ftp_magnet_and_ftps_require_aria2c():
    for scheme in ("ftp", "ftps", "magnet"):
        assert validate_target_url(example_url(scheme), "aria2c") == example_url(scheme)
        with pytest.raises(UnsafeUrlError):
            validate_target_url(example_url(scheme), "yt-dlp")
