"""config_read returns structured missing/unset/set/secret status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_config_read_missing_unset_set_secret(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.config_store import ConfigStore, reset_config_store

    db = tmp_path / "settings.db"
    store = ConfigStore(db_path=str(db))
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    reset_config_store()  # best-effort; we patched get_config_store

    # Direct store for set/get under our path
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store",
        lambda: store,
    )

    reg = LocalToolRegistry(include_builtins=True)

    # Missing
    raw = await reg.execute("config_read", {"key": "agent.personality"})
    data = json.loads(raw["content"])
    # May be missing or set depending on yaml defaults — use a unique key
    raw = await reg.execute("config_read", {"key": "zz.smoke.never_exists_xyz"})
    data = json.loads(raw["content"])
    assert data["status"] == "missing"
    assert data["value"] is None
    assert "not stored" in data["message"].lower() or "not" in data["message"].lower()

    # Unset (empty string stored)
    store.set("zz.smoke.empty", "", category="test")
    raw = await reg.execute("config_read", {"key": "zz.smoke.empty"})
    data = json.loads(raw["content"])
    assert data["status"] == "unset"
    assert data["value"] is None

    # Set
    store.set("zz.smoke.value", "hello-world", category="test")
    raw = await reg.execute("config_read", {"key": "zz.smoke.value"})
    data = json.loads(raw["content"])
    assert data["status"] == "set"
    assert data["value"] == "hello-world"

    # Secret key — value hidden even when set
    store.set("zz.smoke.api_key", "sk-secret-value", category="test")
    raw = await reg.execute("config_read", {"key": "zz.smoke.api_key"})
    data = json.loads(raw["content"])
    assert data["status"] == "secret"
    assert data["value"] is None
    assert "sk-secret" not in raw["content"]
