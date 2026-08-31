"""Translation catalog slices (audit O5)."""

from __future__ import annotations

from kazma_ui.i18n.catalog import agents as _agents
from kazma_ui.i18n.catalog import chat as _chat
from kazma_ui.i18n.catalog import common as _common
from kazma_ui.i18n.catalog import dashboard as _dashboard
from kazma_ui.i18n.catalog import knowledge as _knowledge
from kazma_ui.i18n.catalog import memory as _memory
from kazma_ui.i18n.catalog import packages as _packages
from kazma_ui.i18n.catalog import research as _research
from kazma_ui.i18n.catalog import scheduled as _scheduled
from kazma_ui.i18n.catalog import settings as _settings
from kazma_ui.i18n.catalog import swarm as _swarm
from kazma_ui.i18n.catalog import tool as _tool
from kazma_ui.i18n.catalog import workspace as _workspace
from kazma_ui.i18n.catalog import x_studio as _x_studio

__all__ = ["CATALOG_MODULES", "merged"]

CATALOG_MODULES = (
    _agents,
    _chat,
    _common,
    _dashboard,
    _knowledge,
    _memory,
    _packages,
    _research,
    _scheduled,
    _settings,
    _swarm,
    _tool,
    _workspace,
    _x_studio,
)


def merged() -> dict[str, dict[str, str]]:
    """Merge every catalog slice into one TRANSLATIONS dict."""
    out: dict[str, dict[str, str]] = {}
    for module in CATALOG_MODULES:
        out.update(module.TRANSLATIONS)
    return out
