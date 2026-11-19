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
