"""Generate standalone Core UI engine metadata and defaults from Python's registry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.engine_registry import ENGINE_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "web/src/engine-registry.generated.ts"


def render() -> str:
    rows = {
        engine: {
            "label": row["label"],
            "role": row["role"],
            "priority": row["priority"],
            "available": row["available"],
            "title": row["title"],
            "purpose": row["purpose"],
            "defaults": row["defaults"],
        }
        for engine, row in ENGINE_REGISTRY.items()
    }
    metadata = json.dumps(rows, ensure_ascii=False, indent=2)
    return (
        "// GENERATED from app/engine_registry.py by scripts/generate_engine_registry_ts.py.\n"
        "// Do not edit; run python -m scripts.generate_engine_registry_ts.\n\n"
        f"export const ENGINE_REGISTRY = {metadata} as const;\n\n"
        "export type Engine = keyof typeof ENGINE_REGISTRY;\n"
        "export type EngineSelection = 'auto' | Engine;\n"
        "export const ENGINE_ORDER = Object.keys(ENGINE_REGISTRY) as Engine[];\n"
        "export const ENGINE_DESCRIPTIONS: Record<Engine, {title: string; purpose: string}> = "
        "Object.fromEntries(ENGINE_ORDER.map(engine => [engine, {"
        "title: ENGINE_REGISTRY[engine].title, purpose: ENGINE_REGISTRY[engine].purpose"
        "}])) as Record<Engine, {title: string; purpose: string}>;\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != output:
            raise SystemExit("Generated Core engine metadata drifted; regenerate it")
    else:
        OUTPUT.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
