"""First boot must persist the same YAML choice that fallback reads expose.

The HA fixture exposed a local provider/model becoming the shipped default
after reconciliation. Workspace alignment then removed its YAML fallback.
These tests exercise that transition and deliberate retired-default choices.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kazma_core import config_defaults, config_store
from kazma_core.config_defaults import RETIRED_APPLIED_KEY, RetiredDefault, entry_id
from kazma_core.config_store import ConfigStore


# Verified against disposable PostgreSQL 16; included by postgres_suite.py.
pytestmark = pytest.mark.postgres


_RETIRED_KEY = "notifications.lifecycle.events"
_OLD = ["legacy-event"]
_NEW = ["started"]
_RETIREMENT = RetiredDefault(
    key=_RETIRED_KEY, old=(_OLD,), since="2026-10-06", why="test retirement",
)
_KEYS = (
    "llm.model", "llm.provider", "providers.list", "agent.name",
    "test.only.local", _RETIRED_KEY, RETIRED_APPLIED_KEY,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config_defaults, "RETIRED_DEFAULTS", (_RETIREMENT,))
    value = ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(tmp_path / "kazma.yaml"),
    )
    # The marked PG suite shares a scratch DB; preserve neighbouring tests'
    # rows and isolate only this fixture's known keys, never truncate a store.
    before = {key: value._db_get_raw(key) for key in _KEYS}
    for key in _KEYS:
        value.delete(key)
    try:
        yield value
    finally:
        for key in _KEYS:
            value.delete(key)
        value.batch_set([
            (key, previous, key.split(".")[0])
            for key, previous in before.items() if previous is not config_store._MISSING
        ])
        value.close()


def _yaml(tmp_path: Path, name: str, values: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def _move_fallback(store: ConfigStore, tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    store.reload_from_root(root)
    store.close()  # SQLite reopens lazily; PG reads the same durable rows.


def test_fresh_local_provider_and_model_survive_workspace_alignment(store, tmp_path):
    _yaml(tmp_path, "kazma.yaml", {
        "llm": {"provider": "openai", "model": "shipped-model"},
        "providers": {"list": [{"name": "shipped-provider"}]},
        "agent": {"name": "Kazma"},
    })
    providers = [{"name": "fixture", "base_url": "http://127.0.0.1:8081/v1"}]
    _yaml(tmp_path, "kazma.local.yaml", {
        "llm": {"provider": "fixture", "model": "local-model"},
        "providers": {"list": providers},
    })
    assert store.get("llm.model") == "local-model"
    store.reconcile_from_yaml()
    _move_fallback(store, tmp_path)
    assert store.get("llm.provider") == "fixture"
    assert store.get("llm.model") == "local-model"
    assert store.get("providers.list") == providers
    assert store.get("agent.name") == "Kazma"


def test_saved_choice_wins_and_local_only_key_is_seeded(store, tmp_path):
    _yaml(tmp_path, "kazma.yaml", {"llm": {"model": "shipped-model"}})
    _yaml(tmp_path, "kazma.local.yaml", {
        "llm": {"model": "local-model"}, "test": {"only": {"local": 17}},
    })
    store.set("llm.model", "saved-model", category="llm")
    store.reconcile_from_yaml()
    _move_fallback(store, tmp_path)
    assert store.get("llm.model") == "saved-model"
    assert store.get("test.only.local") == 17


def test_local_only_file_survives_workspace_alignment(store, tmp_path):
    _yaml(tmp_path, "kazma.local.yaml", {"llm": {"model": "local-model"}})
    store.reconcile_from_yaml()
    _move_fallback(store, tmp_path)
    assert store.get("llm.model") == "local-model"


def test_fresh_local_choice_of_retired_value_is_preserved(store, tmp_path):
    _yaml(tmp_path, "kazma.yaml", {"notifications": {"lifecycle": {"events": _NEW}}})
    _yaml(tmp_path, "kazma.local.yaml", {"notifications": {"lifecycle": {"events": _OLD}}})
    store.reconcile_from_yaml()
    assert store.get(_RETIRED_KEY) == _OLD
    assert entry_id(_RETIREMENT) in store.get(RETIRED_APPLIED_KEY)
    store.reconcile_from_yaml()
    assert store.get(_RETIRED_KEY) == _OLD


def test_existing_retired_default_follows_shipped_value_once(store, tmp_path):
    _yaml(tmp_path, "kazma.yaml", {"notifications": {"lifecycle": {"events": _NEW}}})
    _yaml(tmp_path, "kazma.local.yaml", {"notifications": {"lifecycle": {"events": ["local-event"]}}})
    store.set(_RETIRED_KEY, _OLD, category="notifications")
    store.reconcile_from_yaml()
    assert store.get(_RETIRED_KEY) == _NEW
    store.set(_RETIRED_KEY, _OLD, category="notifications")
    store.reconcile_from_yaml()
    assert store.get(_RETIRED_KEY) == _OLD


def test_missing_shipped_file_does_not_retire_saved_settings(store, tmp_path):
    _yaml(tmp_path, "kazma.local.yaml", {"llm": {"model": "local-model"}})
    store.set(_RETIRED_KEY, _OLD, category="notifications")
    store.reconcile_from_yaml()
    assert store.get(_RETIRED_KEY) == _OLD
    assert store.get("llm.model") == "local-model"


def test_removed_retired_row_is_replaced_by_local_value(store, tmp_path):
    _yaml(tmp_path, "kazma.yaml", {})
    _yaml(tmp_path, "kazma.local.yaml", {
        "notifications": {"lifecycle": {"events": ["local-event"]}},
    })
    store.set(_RETIRED_KEY, _OLD, category="notifications")
    store.reconcile_from_yaml()
    _move_fallback(store, tmp_path)
    assert store.get(_RETIRED_KEY) == ["local-event"]


def test_malformed_shipped_file_still_refuses_reconciliation(store, tmp_path):
    (tmp_path / "kazma.yaml").write_text("llm: [", encoding="utf-8")
    _yaml(tmp_path, "kazma.local.yaml", {"llm": {"model": "local-model"}})
    with pytest.raises(yaml.YAMLError):
        store.reconcile_from_yaml()
    assert store._db_get_raw("llm.model") is config_store._MISSING
