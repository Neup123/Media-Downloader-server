"""Engine advertised availability must reflect live executable probes."""
from pathlib import Path


def test_availability_not_hardcoded_true_when_uninstalled_or_probe_fails():
    source = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
    assert '"available": executable is not None and version not in (None, "unknown")' in source
