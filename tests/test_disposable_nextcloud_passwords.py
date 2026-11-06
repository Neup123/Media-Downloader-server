"""Disposable Nextcloud admin passwords must not be parsed as CLI options.

The Nextcloud Docker entrypoint passes the generated password to occ's
maintenance:install --admin-pass argument. URL-safe secrets can start with '-',
which Symfony Console interprets as a new flag, failing installation.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_every_core_nextcloud_install_password_uses_non_option_prefix():
    workflows = {
        ".github/workflows/live-nextcloud-e2e.yml":
            'password = "nc-" + secrets.token_urlsafe(24)',
        ".github/workflows/manual-p2-site-webdav.yml":
            '"NEXTCLOUD_ADMIN_PASSWORD": "nc-" + secrets.token_urlsafe(24)',
        ".github/workflows/pinned-engine-updates.yml":
            '"NEXTCLOUD_ADMIN_PASSWORD": "nc-" + secrets.token_urlsafe(24)',
    }
    for path, expected in workflows.items():
        source = (ROOT / path).read_text()
        assert expected in source, path
        assert not re.search(r"NEXTCLOUD_ADMIN_PASSWORD[^\n]*: secrets\.token_urlsafe", source), path
        assert "secrets.token_urlsafe" in source
