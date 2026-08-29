"""Direct FastAPI route registration.

Was a single 3,862-line module (audit O5). Split by domain behind an
unchanged ``register_direct_routes`` facade — the same decomposition the
``swarm_panel`` package already uses. A shadowing check confirmed no two
routes in this table can match the same URL, so grouping cannot change
which handler serves a request.
"""

from __future__ import annotations

from typing import Any

from kazma_ui.routes_direct._shared import _mem_tid, _tenant_clause
from kazma_ui.routes_direct.auth import register_auth_routes
from kazma_ui.routes_direct.backup import register_backup_routes
from kazma_ui.routes_direct.memory import register_memory_routes
from kazma_ui.routes_direct.misc import register_misc_routes
from kazma_ui.routes_direct.settings import register_settings_routes
from kazma_ui.routes_direct.system import register_system_routes

__all__ = [
    "register_direct_routes",
    "_mem_tid",
    "_tenant_clause",
    "register_system_routes",
    "register_misc_routes",
    "register_memory_routes",
    "register_settings_routes",
    "register_auth_routes",
    "register_backup_routes",
]


def register_direct_routes(self: Any) -> None:
    """Register every direct route handler onto ``self.app``."""
    register_system_routes(self)
    register_misc_routes(self)
    register_memory_routes(self)
    register_settings_routes(self)
    register_auth_routes(self)
    register_backup_routes(self)
