"""Load Kazma YAML config with a clean split between ship defaults and user data.

Architecture (industry-standard — same idea as VS Code, Docker Compose, etc.):

| File | Tracked in git? | Purpose |
|------|-----------------|---------|
| ``kazma.yaml`` | **Yes** | Product defaults shipped with the repo. Never edit for day-to-day ops. |
| ``kazma.local.yaml`` | **No** (gitignored) | Optional machine-local overrides (ports, tokens seed, voice flags). |
| ConfigStore SQLite | **No** (``kazma-data/``) | Runtime settings from Web UI / slash commands. Highest priority. |

Users who only use Settings UI never touch YAML → ``git pull`` / ``kazma update``
never conflict. Users who need file-based overrides use ``kazma.local.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "LOCAL_CONFIG_NAME",
    "SHIPPED_CONFIG_NAME",
    "deep_merge",
    "load_merged_yaml",
    "resolve_config_paths",
]

logger = logging.getLogger(__name__)

SHIPPED_CONFIG_NAME = "kazma.yaml"
LOCAL_CONFIG_NAME = "kazma.local.yaml"


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* into a copy of *base* (overlay wins)."""
    out: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_config_paths(
    config_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Return ``(shipped_yaml, local_yaml)`` paths.

    If *config_path* is given it is treated as the shipped file; the local
    override sits next to it as ``kazma.local.yaml``.
    """
    if config_path is not None:
        shipped = Path(config_path)
    else:
        shipped = Path(SHIPPED_CONFIG_NAME)
    local = shipped.parent / LOCAL_CONFIG_NAME
    return shipped, local


def load_merged_yaml(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load shipped defaults + optional local overrides (no ConfigStore).

    Missing files are treated as empty. Logs when a local override is used.
    """
    shipped_path, local_path = resolve_config_paths(config_path)
    base: dict[str, Any] = {}
    if shipped_path.is_file():
        try:
            with open(shipped_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                base = loaded
        except Exception as exc:
            logger.warning("Failed to read %s: %s", shipped_path, exc)
    else:
        logger.debug("Shipped config not found: %s", shipped_path)

    if local_path.is_file():
        try:
            with open(local_path, encoding="utf-8") as f:
                overlay = yaml.safe_load(f) or {}
            if isinstance(overlay, dict) and overlay:
                base = deep_merge(base, overlay)
                logger.info(
                    "[config] Applied local overrides from %s",
                    local_path.resolve(),
                )
        except Exception as exc:
            logger.warning("Failed to read %s: %s", local_path, exc)

    return base
