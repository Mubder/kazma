"""MCP preset library — one-click server setup from the certified catalog.

Reads ``kazma-skills/kazma_skills/certified_servers.yaml`` (81 servers across
12 categories) and exposes them as a JSON list for the /mcp page's "Add Server"
dropdown. The catalog already has the exact data shape we need (id, name,
description, command[], transport, env, category, arabic_support) — it just
wasn't wired to anything.

Also merges in a few high-value servers that aren't in the certified catalog
yet (firecrawl, playwright) so the dropdown covers the common cases the user
actually tries to add.

Usage from the API layer::

    from kazma_ui.mcp_presets import list_presets
    presets = list_presets()  # → [{id, name, description, category, ...}, ...]
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["list_presets"]

_CERTIFIED_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "kazma-skills"
    / "kazma_skills"
    / "certified_servers.yaml"
)

# Servers that should appear in the preset dropdown even though they're not
# (yet) in certified_servers.yaml. These are the high-traffic ones the user
# is likely to search for. Each has the same shape as a certified entry.
_EXTRA_PRESETS: dict[str, dict[str, Any]] = {
    "firecrawl": {
        "name": "Firecrawl (web scraping)",
        "description": "Scrape, crawl, and map websites. Excellent for bot-walled sites.",
        "command": ["npx", "-y", "firecrawl-mcp"],
        "transport": "stdio",
        "certified": False,
        "arabic_support": "none",
        "category": "web",
        "env_keys": ["FIRECRAWL_API_KEY"],
    },
    "playwright": {
        "name": "Playwright (browser automation)",
        "description": "Browser automation — navigate, click, fill forms, screenshot.",
        "command": ["npx", "-y", "@playwright/mcp@latest"],
        "transport": "stdio",
        "certified": False,
        "arabic_support": "none",
        "category": "web",
        "env_keys": [],
    },
    "sequential_thinking": {
        "name": "Sequential Thinking",
        "description": "Dynamic problem-solving through thought sequences.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
        "transport": "stdio",
        "certified": False,
        "arabic_support": "none",
        "category": "ai",
        "env_keys": [],
    },
    "memory": {
        "name": "Memory (knowledge graph)",
        "description": "Persistent knowledge graph for entity/relation storage.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
        "certified": False,
        "arabic_support": "none",
        "category": "data",
        "env_keys": [],
    },
    "time": {
        "name": "Time",
        "description": "Current time, timezone conversion, and time reasoning.",
        "command": ["npx", "-y", "@modelcontextprotocol/server-time"],
        "transport": "stdio",
        "certified": False,
        "arabic_support": "none",
        "category": "productivity",
        "env_keys": [],
    },
}

_cache: list[dict[str, Any]] | None = None


def list_presets() -> list[dict[str, Any]]:
    """Return the full preset list (certified + extras), grouped-capable.

    Each entry has: id, name, description, category, transport, command (list),
    env_keys (list of env var names the user needs to fill), arabic_support,
    certified (bool).
    """
    global _cache
    if _cache is not None:
        return _cache

    presets: list[dict[str, Any]] = []

    # ── Load certified_servers.yaml ──────────────────────────────────
    try:
        import yaml

        if _CERTIFIED_PATH.is_file():
            with open(_CERTIFIED_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            servers = data.get("servers", {})
            if isinstance(servers, dict):
                for sid, cfg in servers.items():
                    if not isinstance(cfg, dict):
                        continue
                    env_keys: list[str] = []
                    env_block = cfg.get("env", {})
                    if isinstance(env_block, dict):
                        env_keys = list(env_block.keys())
                    presets.append(
                        {
                            "id": sid,
                            "name": cfg.get("name", sid),
                            "description": cfg.get("description", ""),
                            "category": cfg.get("category", "general"),
                            "transport": cfg.get("transport", "stdio"),
                            "command": cfg.get("command", []),
                            "env_keys": env_keys,
                            "arabic_support": cfg.get("arabic_support", "none"),
                            "certified": cfg.get("certified", False),
                        }
                    )
                logger.info(
                    "[mcp_presets] Loaded %d certified servers from %s",
                    len(presets),
                    _CERTIFIED_PATH.name,
                )
        else:
            logger.debug("[mcp_presets] certified_servers.yaml not found at %s", _CERTIFIED_PATH)
    except ImportError:
        logger.warning("[mcp_presets] PyYAML not installed — only extra presets available")
    except Exception as exc:
        logger.warning("[mcp_presets] Failed to load certified_servers.yaml: %s", exc)

    # ── Merge extra presets (firecrawl, playwright, etc.) ────────────
    # Extras OVERRIDE certified entries when the ID matches — this lets us
    # correct outdated package names (e.g. the certified catalog has
    # @anthropic-ai/playwright-mcp but the current official package is
    # @playwright/mcp@latest).
    extra_ids = {eid for eid in _EXTRA_PRESETS}
    presets = [p for p in presets if p["id"] not in extra_ids]
    for eid, ecfg in _EXTRA_PRESETS.items():
        entry = {"id": eid, **ecfg}
        presets.append(entry)

    # Sort by category then name for a stable, browseable dropdown.
    presets.sort(key=lambda p: (p.get("category", "zzz"), p.get("name", "")))

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
