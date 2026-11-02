"""Settings' Per-Tool Timeout bounds every tool call (FA-125).

The 2026-11 functional audit: Settings -> Agent -> Non-Stop's "Per-Tool
Timeout" saved ``agent.nonstop.watchdog.tool_timeout_seconds``, which only the
Non-Stop config loaded and nothing used; the tool worker reads
``agent.tool_timeout_seconds``. An owner who set 0 or 600 still got the 120 s
cut, and the page read back the saved value, so it looked applied. The field
was also hidden while Non-Stop was off, though the bound applies either way.

The field now writes and reads the key the tool worker reads, shows whatever
Non-Stop is, and a value stored under the old key is moved at boot, once. The
negative control is the dead-control gate (FA-250's) run on the old key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.agent import graph_helpers

REPO = Path(__file__).resolve().parents[1]
OLD = "agent.nonstop.watchdog.tool_timeout_seconds"
NEW = "agent.tool_timeout_seconds"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore

    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.delenv("KAZMA_TOOL_TIMEOUT_SECONDS", raising=False)
    s = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: s)
    yield s
    s.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_the_saved_timeout_is_the_tool_workers_bound(store, enabled):
    from kazma_core.settings_manager import SettingsManager

    manager = SettingsManager(store)
    manager.save_nonstop_settings({"enabled": enabled, "tool_timeout_seconds": 7})
    assert store.get(NEW) == 7.0 and store.get(OLD) is None
    assert graph_helpers._resolve_tool_timeout("web_search") == 7.0
    assert manager.get_nonstop_settings()["tool_timeout_seconds"] == 7.0
    manager.save_nonstop_settings({"tool_timeout_seconds": 0})
    assert graph_helpers._resolve_tool_timeout("web_search") == 0.0  # 0 switches it off


def test_a_value_under_the_old_key_is_moved_once(store):
    store.set(OLD, 300.0, category="agent")
    assert store.move_renamed_keys() == [NEW]
    assert store.get(NEW) == 300.0 and store.get(OLD) is None
    assert graph_helpers._resolve_tool_timeout("web_search") == 300.0
    assert store.move_renamed_keys() == []  # once


def test_the_owners_value_under_the_new_key_wins(store):
    store.set(NEW, 45.0, category="agent")
    store.set(OLD, 300.0, category="agent")
    assert store.move_renamed_keys() == []
    assert store.get(NEW) == 45.0 and store.get(OLD) is None


def test_the_field_shows_whatever_non_stop_is():
    html = (REPO / "kazma-ui/kazma_ui/templates/settings.html").read_text(encoding="utf-8")
    field = html.index('data-setting="agent.tool_timeout_seconds"')
    block = html.index('<div x-show="nonstop.enabled" x-cloak>')
    assert field < block, "the field sits inside the Non-Stop block, hidden while it is off"


def test_the_old_key_was_a_dead_control():
    """Negative control: the dead-control gate finds the old key unread now
    that nothing loads it -- the save before FA-125 wrote exactly that."""
    from tests.test_settings_keys_have_readers import _files, reader_index, unread

    lits, cats = reader_index(_files())
    # Its one reader now is the boot move; without it, nothing reads it.
    mover = "kazma-core/kazma_core/config_store.py"
    assert OLD in lits[mover]
    without_mover = {f: (held - {OLD} if f == mover else held) for f, held in lits.items()}
    assert unread({OLD: {"settings_manager.py"}}, without_mover, cats) == [OLD]
    assert unread({NEW: {"settings_manager.py"}}, without_mover, cats) == []
