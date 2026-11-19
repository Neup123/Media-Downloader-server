# Canonical engine registry (Core)

The source of truth for the seven Core engine IDs, default settings, availability, priority, option catalog, and simple user-facing explanations is `app/engine_registry.py`.

- Python consumers: `app/schemas.py` derives engine-name input validation; `app/worker.py` imports engine defaults and descriptions; `app/options_catalog.py` is a thin compatibility import, not a second catalog. `/api/v1/engines` and `/api/v1/engines/{engine}/options` return that data through the existing API.
- Standalone Core React UI: `web/src/engine-registry.generated.ts` derives allowed engine names, descriptions, order and defaults from the Python registry. Regenerate with `python -m scripts.generate_engine_registry_ts`, or use `--check` to verify a committed file has not drifted.
- Backend validation of engine-specific options remains in the actual adapters and central settings validation. Catalog entries describe the supported options; they do not grant arbitrary CLI argument execution.
- Adding an engine must update its single Python registry record and actual adapter/validator, regenerate the TS contract, and align checked-in OpenAPI. `tests/test_engine_registry_contract.py` fails on missing metadata, stale generated TS, model name drift, or API/catalog mismatch.

**Repository boundary:** The Nextcloud integration has its own *native plugin* UI and does not embed the standalone React app. Its content-category mapping is not the same thing as a Core engine identity. Nextcloud may consume canonical API data from Core, but must not keep a copy of this standalone interface.

The tests establish contract parity, not live extractor support for new sites. Engine readiness and legal real-site acceptance remain separate release gates.
