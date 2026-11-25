import base64

import pytest
from cryptography.fernet import Fernet

from app.cookies import (
    encrypt_cookie, read_cookie_file, store_cookie_file,
    provided_cookie_dir, provided_cookie_for_url, provided_hostname,
    validate_provided_cookie_export,
)

CONTENT = b"# Netscape HTTP Cookie File\n.example.test\tTRUE\t/\tTRUE\t0\tid\tfixture-value\n"


def test_wrong_key_and_unpadded_fernet_tokens_fail_closed(tmp_path):
    key = Fernet.generate_key().decode("ascii")
    # Choose an input whose encrypted binary length is divisible by 3.
    token = next(encrypt_cookie(b"x" * n, key) for n in range(1, 100)
                 if len(base64.urlsafe_b64decode(encrypt_cookie(b"x" * n, key))) % 3 == 0)
    assert not token.endswith(b"=")
    path = tmp_path / "cookie.txt"
    path.write_bytes(token)
    with pytest.raises(ValueError, match="required before reading"):
        read_cookie_file(path, None)
    with pytest.raises(ValueError, match="cannot be decrypted"):
        read_cookie_file(path, Fernet.generate_key().decode("ascii"))
    assert read_cookie_file(path, key).startswith(b"x")
    path.write_bytes(token[:25])
    with pytest.raises(ValueError, match="cannot be decrypted"):
        read_cookie_file(path, key)


def test_exact_hostname_provisioning_and_atomic_storage(tmp_path):
    key = Fernet.generate_key().decode("ascii")
    directory = provided_cookie_dir(tmp_path)
    assert provided_hostname("WWW.Example.Test.") == "www.example.test"
    validate_provided_cookie_export(CONTENT, "www.example.test")
    store_cookie_file(directory, "www.example.test.txt", CONTENT, key)
    target = provided_cookie_for_url(tmp_path, "https://www.example.test/video")
    assert target and read_cookie_file(target, key) == CONTENT
    assert CONTENT not in target.read_bytes()
    assert provided_cookie_for_url(tmp_path, "https://other.example.test/video") is None
    assert provided_cookie_for_url(tmp_path, "https://www.example.test.evil.test/video") is None
    target.unlink()
    assert provided_cookie_for_url(tmp_path, "https://www.example.test/video") is None


@pytest.mark.parametrize("host", ["example", "https://example.test", "*.example.test", "127.0.0.1", "example.test:80", "local.internal"])
def test_invalid_provisioned_hostname(host):
    with pytest.raises(ValueError):
        provided_hostname(host)


def test_rejects_unrelated_or_malformed_export():
    for content in (b"secret", CONTENT.replace(b".example.test", b".unrelated.test"),
                    CONTENT.replace(b"\tTRUE\t/\t", b"\tmaybe\t/\t")):
        with pytest.raises(ValueError):
            validate_provided_cookie_export(content, "www.example.test")
