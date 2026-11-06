"""A local copy of the settings, for a start without the database (resilience plan A4).

With the database away at boot Kazma waited and served nothing: the chat
apps were silent for the whole outage. A copy of the settings is kept on
this machine (``<data dir>/settings_snapshot.yaml``, written by the
maintenance sweep "settings snapshot" every 15 minutes, and once at boot).
Secrets stay ``vault://`` pointers -- the vault is local already. When the
database is still away a minute into a boot, ``kazma_ui.read_only_mode``
starts from this copy: the chat apps answer each message once, the messages
are kept and answered when the database is back.

The copy is only read, never written back: :class:`SnapshotSettings` refuses
every write (:class:`SettingsReadOnly`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SNAPSHOT_NAME", "SettingsReadOnly", "SnapshotSettings", "load_snapshot", "snapshot_sweep",
           "write_snapshot"]

SNAPSHOT_NAME = "settings_snapshot.yaml"


class SettingsReadOnly(RuntimeError):
    """A write to the settings while Kazma runs read-only from its copy."""

    code = "database_away_read_only"


def _snapshot_path() -> Path:
    from kazma_core.paths import data_dir

    return Path(data_dir()) / SNAPSHOT_NAME


def write_snapshot(store: Any = None, path: Path | None = None) -> int:
    """Write the settings' copy (whole, then a rename). Returns its size."""
    from kazma_core.atomic_files import atomic_write_text

    if store is None:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
    text = store.export_yaml()
    atomic_write_text(path or _snapshot_path(), text)
    return len(text)


def snapshot_sweep() -> str:
    """The maintenance sweep: a fresh copy every pass (15 minutes)."""
    size = write_snapshot()
    return f"written ({size} bytes)"


def _flatten(node: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(node, dict) and node:
        for key, value in node.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    elif prefix:
        out[prefix] = node


def _resolve(value: Any) -> Any:
    """A ``vault://`` pointer's secret, from the local vault (install scope)."""
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    if isinstance(value, str) and value.startswith("vault://"):
        from kazma_core.security.vault import get_vault

        vault = get_vault()
        return vault.retrieve(value[len("vault://"):]) if vault is not None else None
    return value


class SnapshotSettings:
    """The settings' local copy, read-only: the reads Kazma's read-only start
    makes (``get`` with child keys merged, as the store does), every write
    refused."""

    def __init__(self, flat: dict[str, Any], *, written_at: float) -> None:
        self._flat = flat
        self.written_at = written_at

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._flat:
            return _resolve(self._flat[key])
        prefix = key + "."
        children = {k[len(prefix):]: v for k, v in self._flat.items() if k.startswith(prefix)}
        if not children:
            return default
        nested: dict[str, Any] = {}
        for sub, value in children.items():
            node = nested
            parts = sub.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {}) if isinstance(node.get(part, {}), dict) else node
            node[parts[-1]] = value
        return _resolve(nested)

    def get_category(self, category: str) -> dict[str, Any]:
        value = self.get(category, {})
        return value if isinstance(value, dict) else {}

    def add_change_listener(self, listener: Callable[..., Any]) -> Callable[[], None]:
        return lambda: None  # a copy never changes

    def _refuse(self, *_a: Any, **_k: Any) -> Any:
        raise SettingsReadOnly("Kazma's database is away: settings cannot be changed until it is back.")

    set = batch_set = delete = atomic_update = import_yaml = reset_all = _refuse  # noqa: A003

    def close(self) -> None:
        return None


def load_snapshot(path: Path | None = None) -> SnapshotSettings | None:
    """The settings' copy, or None when there is none to start from."""
    import yaml

    target = path or _snapshot_path()
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        written = target.stat().st_mtime
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logger.warning("[settings-snapshot] no usable copy at %s: %s", target, type(exc).__name__)
        return None
    if not isinstance(data, dict) or not data:
        return None
    flat: dict[str, Any] = {}
    _flatten(data, "", flat)
    return SnapshotSettings(flat, written_at=written)


def has_snapshot(path: Path | None = None) -> bool:
    target = path or _snapshot_path()
    return target.is_file() and os.path.getsize(target) > 0
