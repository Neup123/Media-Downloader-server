"""Issue #183: one engine identity/default/option contract for API and standalone UI."""
from __future__ import annotations

from pathlib import Path
import pytest
from typing import get_args

import yaml

from app.engine_registry import (
    ENGINE_NAMES, ENGINE_REGISTRY, ENGINE_DEFAULTS, ENGINE_SETTING_KEYS,
    ENGINE_DESCRIPTIONS, OPTION_CATALOG, get_catalog,
)
from app.schemas import EngineName, EngineSelection
from app.main import validate_engine_settings
from app.worker import ENGINE_DEFAULTS as WORKER_DEFAULTS
from scripts.generate_engine_registry_ts import OUTPUT, render

ROOT = Path(__file__).resolve().parents[1]


def test_identity_defaults_descriptions_and_catalog_are_same_registry():
    assert len(ENGINE_NAMES) == 7
    assert len(ENGINE_NAMES) == len(set(ENGINE_NAMES))
    assert set(ENGINE_NAMES) == set(ENGINE_DEFAULTS) == set(OPTION_CATALOG)
    assert set(ENGINE_NAMES) == {row["name"] for row in ENGINE_DESCRIPTIONS}
    assert WORKER_DEFAULTS is ENGINE_DEFAULTS
    for engine in ENGINE_NAMES:
        row = ENGINE_REGISTRY[engine]
        assert row["available"] is True
        assert row["defaults"] is ENGINE_DEFAULTS[engine]
        assert row["fields"] is get_catalog(engine)
        assert row["title"] and row["purpose"] and row["role"] and row["label"]
        assert len({field["key"] for field in row["fields"]}) == len(row["fields"])


def test_pydantic_engine_literals_are_derived_not_hand_copied():
    assert get_args(EngineName) == ENGINE_NAMES
    assert get_args(EngineSelection) == ("auto", *ENGINE_NAMES)


def test_standalone_types_and_defaults_have_exact_generated_parity():
    assert OUTPUT.read_text(encoding="utf-8") == render()
    component = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    assert "from './engine-registry.generated'" in component
    assert "ENGINE_REGISTRY['yt-dlp'].defaults" in component
    assert "ENGINE_REGISTRY.aria2c.defaults" in component
    assert "ENGINE_REGISTRY['get-sauce'].defaults" in component
    assert "const ENGINE_DESCRIPTIONS:" not in component
    assert "type Engine = 'yt-dlp'" not in component
    assert "const ytDefaults: YtSettings = {" not in component.replace(
        "const ytDefaults: YtSettings = {...ENGINE_REGISTRY", "const ytDefaults: YtSettings = ENGINE_REGISTRY"
    )


def test_openapi_engine_status_and_selection_contract_covers_every_engine():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    name = spec["components"]["schemas"]["EngineStatusOut"]["properties"]["name"]["enum"]
    assert set(name) == set(ENGINE_NAMES)
    options = spec["components"]["schemas"]["EngineOptionsOut"]["properties"]["engine"]["enum"]
    assert set(options) == set(ENGINE_NAMES)
    assert "auto" not in name


def test_every_engine_catalog_is_served_by_same_api_contract(client, auth_headers):
    for name in ENGINE_NAMES:
        opts = client.get(f"/api/v1/engines/{name}/options", headers=auth_headers)
        assert opts.status_code == 200, (name, opts.text)
        assert opts.json()["fields"] == get_catalog(name)

def test_every_engine_has_one_registered_setting_key_boundary():
    # The same registry owns defaults and accepted top-level setting names.
    # A newly advertised engine must fail CI until its validator is implemented.
    assert set(ENGINE_SETTING_KEYS) == set(ENGINE_NAMES)
    for name in ENGINE_NAMES:
        row = ENGINE_REGISTRY[name]
        accepted = set(row["defaults"]) | set(row.get("additional_settings", ()))
        assert ENGINE_SETTING_KEYS[name] == accepted
        assert validate_engine_settings(name, {}) == {}
        with pytest.raises(ValueError, match="Unknown"):
            validate_engine_settings(name, {"__unregistered__": "ignored"})
    with pytest.raises(ValueError, match="Unsupported engine"):
        validate_engine_settings("future-engine", {})
    assert ENGINE_SETTING_KEYS["yt-dlp"] - set(ENGINE_DEFAULTS["yt-dlp"]) == {"cookies_file"}
    assert ENGINE_SETTING_KEYS["aria2c"] == {"options"}


def test_registry_is_the_only_source_of_key_allowlists_for_api_validation():
    code = (ROOT / "app/main.py").read_text(encoding="utf-8")
    section = code.split("def validate_engine_settings(", 1)[1].split(
        "def validate_shared_preset_settings(", 1
    )[0]
    assert "ENGINE_SETTING_KEYS[engine]" in section
    assert "allowed = {" not in section
    assert 'set(values) - {"options"}' not in section
