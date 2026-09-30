"""MCP server presets: the /mcp page's "Add Server" list.

The list is ``kazma_skills/certified_servers.yaml``. Every entry runs a
package that exists and is maintained, published by the Model Context
Protocol project or by the vendor of the service it connects to;
``tests/test_mcp_catalog.py`` holds it to what npm and PyPI said
(``scripts/verify_mcp_catalog.py``). Until 2026-09-30 the file offered 81
"certified" servers, 78 of which named packages that never existed, and this
module found it through the repository's folder layout, so an install made
from a release wheel showed no preset from it at all.

Usage from the API layer::

    from kazma_ui.mcp_presets import list_presets
    presets = list_presets()  # → [{id, name, description, category, ...}, ...]
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["list_presets", "list_presets_grouped"]

_cache: list[dict[str, Any]] | None = None


def _catalog_path() -> Path:
    """The catalog inside the installed ``kazma_skills`` package (checkout or wheel)."""
    import kazma_skills

    return Path(kazma_skills.__file__).resolve().parent / "certified_servers.yaml"


def list_presets() -> list[dict[str, Any]]:
    """Every preset, sorted by category then name.

    Each has: id, name, description, category, transport, command (list),
    env_keys (the variables the operator fills in; Kazma keeps their values
    in the vault), package ({registry, name}), source, workspace_bound.
    A missing or unreadable catalog raises: the route reports it.
    """
    global _cache
    if _cache is not None:
        return _cache
    import yaml

    data = yaml.safe_load(_catalog_path().read_text(encoding="utf-8")) or {}
    presets: list[dict[str, Any]] = []
    for sid, cfg in (data.get("servers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        env = cfg.get("env") or {}
        presets.append(
            {
                "id": sid,
                "name": cfg.get("name", sid),
                "description": cfg.get("description", ""),
                "category": cfg.get("category", "general"),
                "transport": cfg.get("transport", "stdio"),
                "command": list(cfg.get("command") or []),
                "env_keys": list(env) if isinstance(env, dict) else [],
                "package": dict(cfg.get("package") or {}),
                "source": cfg.get("source", ""),
                "workspace_bound": bool(cfg.get("workspace_bound")),
            }
        )
    presets.sort(key=lambda p: (p["category"], p["name"]))
    logger.info("[mcp_presets] Loaded %d presets from %s", len(presets), _catalog_path().name)
    _cache = presets
    return presets


def list_presets_grouped() -> list[dict[str, Any]]:
    """Return presets grouped by category for dropdown optgroups.

    Shape: ``[{name: "filesystem", presets: [...]}, {name: "web", ...}, ...]``
    """
    presets = list_presets()
    cats: dict[str, list[dict[str, Any]]] = {}
    for p in presets:
        cat = p.get("category", "general")
        cats.setdefault(cat, []).append(p)
    return [{"name": cat, "presets": items} for cat, items in sorted(cats.items())]
