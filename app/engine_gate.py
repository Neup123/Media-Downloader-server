"""One admission policy for registry, submission and scheduler.

Extracted from #173, adapted to the canonical registry on main. A missing
row means enabled for the seven existing engines. A disabled engine is not
admitted; already running transfers finish normally, and queued work remains
queued until enabled or explicitly cancelled. No new job statuses.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import EnginePolicy
from app.engine_registry import ENGINE_NAMES


def disabled_engines(session: Session) -> set[str]:
    return set(session.scalars(select(EnginePolicy.engine).where(
        EnginePolicy.enabled.is_(False))).all()) & set(ENGINE_NAMES)


def engine_permitted(session: Session, engine: str) -> bool:
    if engine not in ENGINE_NAMES:
        return False
    row = session.get(EnginePolicy, engine)
    return row is None or row.enabled


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
