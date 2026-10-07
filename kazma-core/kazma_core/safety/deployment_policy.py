"""Install policies saved through Settings; deployment environment remains a floor.

These settings only tighten execution. ConfigStore failures must never grant
host execution or workspace access. Callers doing database work use a thread.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

WORKSPACE_ROOTS_KEY = "security.workspace_roots"
CONTAINER_REQUIRED_KEY = "security.execution.container_required"


def policy_read_failures() -> tuple[type[Exception], ...]:
    """Storage, configuration and dependency failures that refuse access.

    Unexpected programming errors propagate to the caller's error boundary;
    they must never become a permissive policy value.
    """
    failures: tuple[type[Exception], ...] = (sqlite3.Error, OSError, RuntimeError, ValueError, ImportError)
    try:
        from psycopg import Error as PostgresError
    except ImportError:
        pass
    else:
        failures += (PostgresError,)
    try:
        from psycopg_pool import PoolTimeout
    except ImportError:
        pass
    else:
        failures += (PoolTimeout,)
    return failures


def normalize_workspace_roots(value: Any) -> list[str]:
    """Explicit absolute roots, resolved through symlinks before containment."""
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError("Workspace roots must be a non-empty list of up to 16 absolute directories.")
    roots: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or any(ord(c) < 32 for c in item):
            raise ValueError("Each workspace root must be an absolute directory.")
        path = Path(item.strip()).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Each workspace root must be an existing absolute directory.")
        root = str(path.resolve())
        if root not in roots:
            roots.append(root)
    return roots


def normalize_container_required(value: Any) -> bool:
    """Reject strings such as 'false', which are truthy in Python."""
    if type(value) is not bool:
        raise ValueError("Container required must be a boolean.")
    return value


def configured_workspace_roots() -> list[Path]:
    from kazma_core.config_store import get_config_store

    value = get_config_store().get(WORKSPACE_ROOTS_KEY)
    if value is None:
        return []
    return [Path(root) for root in normalize_workspace_roots(value)]


def container_required() -> bool:
    """A persisted strict profile wins over legacy environment escape hatches."""
    from kazma_core.config_store import get_config_store

    value = get_config_store().get(CONTAINER_REQUIRED_KEY)
    return False if value is None else normalize_container_required(value)
