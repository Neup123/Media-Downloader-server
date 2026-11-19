"""Compatibility surface: engine metadata lives in app.engine_registry.

No separate option definitions are maintained here.
"""
from app.engine_registry import OPTION_CATALOG, field, get_catalog

__all__ = ["OPTION_CATALOG", "field", "get_catalog"]
