from app.adapters.get_sauce import effective_settings as get_sauce_settings
from app.adapters.svtplay import effective_settings as svtplay_settings
from app.options_catalog import get_catalog


def test_bounded_catalog_choices_match_actual_backend_validation(client, auth_headers):
    cases = (
        ("get-sauce", "merge", ["default", "none", "cbz"], get_sauce_settings),
        ("svtplay-dl", "output_format", ["mp4", "mkv"], svtplay_settings),
    )
    for engine, key, values, validate in cases:
        fields = {item["key"]: item for item in get_catalog(engine)}
        assert fields[key]["type"] == "enum"
        assert fields[key]["choices"] == values
        api = client.get(f"/api/v1/engines/{engine}/options", headers=auth_headers)
        assert api.status_code == 200, api.text
        value = next(item for item in api.json()["fields"] if item["key"] == key)
        assert value["choices"] == values
        for choice in values:
            assert validate({}, {key: choice})[key] == choice
        try:
            validate({}, {key: "invalid-free-text"})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{engine} accepted arbitrary {key} outside its catalog")



def test_engine_default_selection_round_trip_uses_fallback_via_real_settings_api(
    client, auth_headers,
):
    """Run the same UI update helper and save the serialized result through Core."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    assert node, "Node.js must be available for the UI/API round-trip contract"
    root = Path(__file__).resolve().parents[1]
    script = """
      import { setOptionOrDefault } from './web/src/engine-option-default.ts';
      const cases = [['get-sauce', 'merge', 'cbz'], ['svtplay-dl', 'output_format', 'mkv']];
      console.log(JSON.stringify(cases.map(([engine, key, selected]) => ({
        engine, key, selected,
        // Selecting Engine default MUST remove the override, not write ''.
        settings: setOptionOrDefault({[key]: selected}, key, '', ''),
      }))));
    """
    result = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        check=True, capture_output=True, text=True, cwd=root,
    )
    cases = json.loads(result.stdout)
    expected = {"get-sauce": "default", "svtplay-dl": "mp4"}
    for item in cases:
        engine, key = item["engine"], item["key"]
        assert key not in item["settings"]
        # First persist a non-default choice, then choose Engine default.
        selected = client.put(
            f"/api/v1/engines/{engine}/settings",
            headers=auth_headers,
            json={"settings": {key: item["selected"]}},
        )
        assert selected.status_code == 200, selected.text
        reset = client.put(
            f"/api/v1/engines/{engine}/settings",
            headers=auth_headers,
            json={"settings": item["settings"]},
        )
        assert reset.status_code == 200, reset.text
        saved = client.get(
            f"/api/v1/engines/{engine}/settings", headers=auth_headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["settings"][key] == expected[engine]
