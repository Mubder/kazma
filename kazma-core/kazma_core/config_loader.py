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

import copy
import logging
import threading
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "LOCAL_CONFIG_NAME",
    "SHIPPED_CONFIG_NAME",
    "deep_merge",
    "install_yaml_section",
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


# ── The install's YAML, parsed once per file version ─────────────────────

_PARSED_LOCK = threading.Lock()
#: shipped path -> ((shipped stamp, local stamp), merged YAML)
_PARSED: dict[str, tuple[tuple[Any, Any], dict[str, Any]]] = {}


def _install_config_path() -> Path:
    """The install's ``kazma.yaml``: at the project root, beside ``kazma-data``.

    Resolved like the data directory (:func:`kazma_core.paths.get_project_root`),
    not from wherever the process happens to be running.
    """
    from kazma_core.paths import get_project_root

    return get_project_root() / SHIPPED_CONFIG_NAME


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def install_yaml_section(*keys: str) -> dict[str, Any]:
    """A block of the install's merged YAML, e.g. ``("memory", "embedding")``.

    Shipped ``kazma.yaml`` plus ``kazma.local.yaml``, parsed once per file
    version: the parse is keyed by both files' modification time and size, so
    an edit is read on the next call, and a call without one costs two stats.
    Memory recall used to parse the file from the working directory several
    times per question (Stage 2, S4). Returns a copy the caller may change;
    ``{}`` when the file or the block is missing.
    """
    shipped, local = resolve_config_paths(_install_config_path())
    stamp = (_stamp(shipped), _stamp(local))
    with _PARSED_LOCK:
        hit = _PARSED.get(str(shipped))
    if hit is not None and hit[0] == stamp:
        merged = hit[1]
    else:
        merged = load_merged_yaml(shipped)
        with _PARSED_LOCK:
            _PARSED[str(shipped)] = (stamp, merged)
    node: Any = merged
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return copy.deepcopy(node) if isinstance(node, dict) else {}
