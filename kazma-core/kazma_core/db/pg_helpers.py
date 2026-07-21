"""Shared helpers for Postgres dual-backend stores."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def use_postgres() -> bool:
    try:
        from kazma_core.db.backend import is_postgres

        return is_postgres()
    except Exception:
        return False


def get_pool() -> Any:
    from kazma_core.db.postgres_pool import get_postgres_pool

    pool = get_postgres_pool()
    if pool is None:
        raise RuntimeError("Postgres pool unavailable (set KAZMA_DATABASE_URL)")
    return pool


def json_loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return default


def json_dumps(val: Any) -> str:
    return json.dumps(val, ensure_ascii=False, default=str)
