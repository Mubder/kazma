"""A changed shipped default reaches installs that booted before it -- or says
why not (2026-09-29).

The first boot stores every kazma.yaml value in the settings database, and a
stored value wins from then on. So "three messages on every restart" could
be fixed in kazma.yaml and the live install would still send all three:
``notifications.lifecycle.events`` there was the copy its first boot stored.
A read-only audit of the live settings the same day found ten keys still
holding an older shipped value (most no longer read by anything).

``kazma_core.config_defaults`` declares each changed default once: installs
follow (``RETIRED_DEFAULTS``, applied once per entry at boot), or keep the
old value (``NEW_INSTALLS_ONLY``, with the reason). This file holds the
snapshot gate: kazma.yaml against ``tests/fixtures/shipped_config_defaults.json``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from kazma_core import config_defaults
from kazma_core.config_defaults import RETIRED_APPLIED_KEY, RetiredDefault, entry_id
from kazma_core.config_store import ConfigStore, shipped_settings

REPO = Path(__file__).resolve().parents[1]


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "shipped_defaults_script", REPO / "scripts" / "shipped_defaults.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


shipped_defaults = _load_script()

KEY = "notifications.lifecycle.events"
OLD = ["starting", "started", "shutting_down", "startup_failed"]
NEW = ["started", "startup_failed"]


# ── the gate ─────────────────────────────────────────────────────────────


def test_kazma_yaml_matches_its_snapshot():
    delta = shipped_defaults.diff(shipped_defaults.current_defaults(), shipped_defaults.load_snapshot())
    assert not any(delta.values()), (
        "kazma.yaml changed since tests/fixtures/shipped_config_defaults.json:\n"
        + shipped_defaults.report(delta)
        + "\nRun `python scripts/shipped_defaults.py`: a changed value must be declared in "
        "kazma_core/config_defaults.py (installs follow, or keep the old value), then "
        "`--write` refreshes the snapshot."
    )


def test_an_undeclared_changed_default_is_refused(monkeypatch):
    """Negative control: a change the lists do not declare fails; either
    declaration passes it."""
    changed = {"ui.port": (9090, 9191)}
    assert shipped_defaults.undeclared(changed) == ["ui.port"]

    monkeypatch.setattr(shipped_defaults, "RETIRED_DEFAULTS", (
        RetiredDefault(key="ui.port", old=(9090,), since="2026-09-29", why="test"),
    ))
    assert shipped_defaults.undeclared(changed) == []

    monkeypatch.setattr(shipped_defaults, "RETIRED_DEFAULTS", ())
    monkeypatch.setattr(shipped_defaults, "NEW_INSTALLS_ONLY", {"ui.port": "test"})
    assert shipped_defaults.undeclared(changed) == []


def test_a_retirement_must_name_the_value_that_shipped():
    """Declaring the key is not enough: ``old`` must hold the value the
    snapshot had, or installs holding it would never follow."""
    changed = {KEY: (["started"], NEW)}
    assert shipped_defaults.undeclared(changed) == [KEY]


def test_every_declaration_is_consistent():
    shipped = shipped_defaults.current_defaults()
    ids = [entry_id(e) for e in config_defaults.RETIRED_DEFAULTS]
    assert len(ids) == len(set(ids))
    for entry in config_defaults.RETIRED_DEFAULTS:
        assert entry.key in shipped, f"{entry.key}: not a setting kazma.yaml ships"
        assert not any(config_defaults.same_value(shipped[entry.key], v) for v in entry.old), (
            f"{entry.key}: kazma.yaml still ships a value it retires"
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.since)
        assert entry.why.strip()
    for key, why in config_defaults.NEW_INSTALLS_ONLY.items():
        assert key in shipped and why.strip()


def test_the_snapshot_is_what_the_database_is_seeded_with(tmp_path):
    """The snapshot reads kazma.yaml through ``shipped_settings``, as the
    boot does: a fresh database seeded from kazma.yaml holds exactly it."""
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(REPO / "kazma.yaml"))
    try:
        store.reconcile_from_yaml()
        snapshot = shipped_defaults.load_snapshot()
        for key, value in snapshot.items():
            assert config_defaults.same_value(store._db_get_raw(key), value), key
    finally:
        store.close()


# ── installs follow, once ────────────────────────────────────────────────


def _store(tmp_path: Path, events: list[str] | None) -> ConfigStore:
    (tmp_path / "kazma.yaml").write_text(
        yaml.safe_dump({"notifications": {"lifecycle": {"enabled": True, "events": NEW}}}),
        encoding="utf-8",
    )
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "kazma.yaml"))
    if events is not None:
        store.set(KEY, events, category="notifications")
    return store


def test_a_stored_copy_of_the_old_default_follows_once(tmp_path):
    store = _store(tmp_path, OLD)
    try:
        store.reconcile_from_yaml()
        assert store.get(KEY) == NEW
        assert f"{KEY}@2026-09-29" in store.get(RETIRED_APPLIED_KEY)

        store.set(KEY, OLD, category="notifications")   # the owner picks it again
        store.reconcile_from_yaml()
        assert store.get(KEY) == OLD, "an owner's later choice is theirs"
    finally:
        store.close()


def test_the_old_default_stays_without_the_declaration(tmp_path, monkeypatch):
    """Negative control: the declaration, not the reconcile, moves it."""
    monkeypatch.setattr(config_defaults, "RETIRED_DEFAULTS", ())
    store = _store(tmp_path, OLD)
    try:
        store.reconcile_from_yaml()
        assert store.get(KEY) == OLD
    finally:
        store.close()


def test_an_owners_own_value_is_kept(tmp_path):
    store = _store(tmp_path, ["started", "shutting_down"])
    try:
        store.reconcile_from_yaml()
        assert store.get(KEY) == ["started", "shutting_down"]
        assert f"{KEY}@2026-09-29" in store.get(RETIRED_APPLIED_KEY), "checked once, even so"
    finally:
        store.close()


def test_a_new_install_gets_todays_default(tmp_path):
    store = _store(tmp_path, None)
    try:
        store.reconcile_from_yaml()
        assert store.get(KEY) == NEW
    finally:
        store.close()


def test_a_key_kazma_yaml_dropped_goes_back_to_the_code_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config_defaults, "RETIRED_DEFAULTS", (
        RetiredDefault(key="agent.version", old=("0.5.0",), since="2026-09-29", why="test"),
    ))
    store = _store(tmp_path, None)
    try:
        store.set("agent.version", "0.5.0", category="agent")
        store.reconcile_from_yaml()
        assert store.get("agent.version") is None
    finally:
        store.close()


@pytest.mark.parametrize("a, b, same", [
    (["x", "y"], ["x", "y"], True), (["y", "x"], ["x", "y"], False),
    (1, True, False), ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True),
])
def test_same_value_compares_as_stored(a, b, same):
    assert config_defaults.same_value(a, b) is same


def test_shipped_settings_is_the_one_reading(tmp_path):
    """``reconcile_from_yaml`` seeds through ``shipped_settings`` (nested HITL
    keys land flat; the retired 60 s approval timeout is never seeded)."""
    data = {"safety": {"hitl": {"approval_timeout_seconds": 60, "enabled": True}}, "a": {"b": [1]}}
    assert shipped_settings(data) == [("safety.hitl_enabled", True, "safety"), ("a.b", [1], "a")]
