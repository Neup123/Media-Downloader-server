"""Product release metadata used by the runtime API."""
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def product_version() -> str:
    """Read the release identifier shipped in this image, not a stale API literal."""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdecimal() for p in parts):
        raise ValueError("VERSION must be major.minor.patch")
    return version
