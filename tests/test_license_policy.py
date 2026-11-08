"""Guard the owner-approved source-available licensing policy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_business_source_license_notice_and_bounds():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("Business Source License 1.1")
    assert "Additional Use Grant:" in license_text
    assert "twenty (20)" in license_text
    assert "USD 250,000" in license_text
    assert "employees and independent contractors" in license_text
    assert "parent, subsidiary, affiliate" in license_text
    assert "Required Notice" in license_text
    assert "Change Date:" in license_text
    assert "Change License:" in license_text
    assert "Covenants of Licensor" in license_text
    assert "non-production" in license_text

def test_readme_links_full_license():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[Business Source License 1.1 with Additional Use Grant](LICENSE)" in readme
