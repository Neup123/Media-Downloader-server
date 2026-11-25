"""One admission policy for registry, submission and scheduler.

Extracted from #173, adapted to the canonical registry on main. A missing
row uses the canonical registry default (enabled for existing engines). A disabled engine is not
admitted; already running transfers finish normally, and queued work remains
queued until enabled or explicitly cancelled. No new job statuses.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import EnginePolicy
from app.engine_registry import ENGINE_NAMES, ENGINE_REGISTRY


def disabled_engines(session: Session) -> set[str]:
    saved = {row.engine: row.enabled for row in session.scalars(select(EnginePolicy)).all()}
    return {name for name in ENGINE_NAMES if not saved.get(
        name, ENGINE_REGISTRY[name].get("default_enabled", True))}


def engine_permitted(session: Session, engine: str) -> bool:
    if engine not in ENGINE_NAMES:
        return False
    row = session.get(EnginePolicy, engine)
    return row.enabled if row is not None else ENGINE_REGISTRY[engine].get("default_enabled", True)


def set_engine_enabled(session: Session, engine: str, enabled: bool) -> bool:
    if engine not in ENGINE_NAMES or type(enabled) is not bool:
        raise ValueError("Invalid engine policy")
    row = session.get(EnginePolicy, engine)
    if row is None:
        row = EnginePolicy(engine=engine, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    session.commit()
    session.refresh(row)
    return row.enabled
