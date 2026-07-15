"""IDE package — transport-agnostic coding backend for Kazma."""

from __future__ import annotations

from kazma_core.ide.env_context import build_env_context, env_context_for_dispatch
from kazma_core.ide.service import IdeService, get_ide_service, reset_ide_service

__all__ = [
    "IdeService",
    "get_ide_service",
    "reset_ide_service",
    "build_env_context",
    "env_context_for_dispatch",
]
