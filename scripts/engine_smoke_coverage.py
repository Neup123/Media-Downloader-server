"""Engines whose pinned PyPI upgrades have real disposable-WebDAV acceptance.

This list is an *approval gate*, not a list of bundled or unit-tested engines.
Only add an engine after tests/e2e_live_webdav.py downloads a deterministic
media fixture with it and independently verifies delivered file SHA-256.
"""

PYPI_ENGINE_SMOKE_VERIFIED = frozenset({"yt-dlp", "gallery-dl", "you-get"})
