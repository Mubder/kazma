"""Audit D remaining: stable season pick, TUI is a swarm mouth."""

from __future__ import annotations

from pathlib import Path

from kazma_core.sessions.directory import resolve_session


def test_resolve_hash_short_id(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore, reset_config_store, set_config_store
    from kazma_core.sessions.directory import stamp_last_platform
    from kazma_ui.session_manager import ChatSession, get_session_manager, reset_session_manager

    reset_session_manager()
    store = ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(tmp_path / "missing.yaml"),
    )
    set_config_store(store)
    try:
        sm = get_session_manager()
        a = ChatSession(session_id="sess-aaa11111", thread_id="sess-aaa11111", title="A")
        a.add_message("user", "hi")
        sm.put(a)
        stamp_last_platform("sess-aaa11111", "web")
        hit = resolve_session("#aaa11111")
        assert hit is not None
        assert hit.title == "A"
        assert resolve_session("aaa11111") is not None
    finally:
        reset_config_store()
        reset_session_manager()


def test_tui_does_not_construct_local_swarm_engine():
    app = (
        Path(__file__).resolve().parent.parent / "kazma-tui" / "kazma_tui" / "app.py"
    ).read_text(encoding="utf-8")
    swarm = (
        Path(__file__).resolve().parent.parent / "kazma-tui" / "kazma_tui" / "swarm.py"
    ).read_text(encoding="utf-8")
    chat = (
        Path(__file__).resolve().parent.parent / "kazma-tui" / "kazma_tui" / "chat.py"
    ).read_text(encoding="utf-8")
    assert "SwarmEngine(" not in app
    assert "set_swarm_engine" not in app
    assert "/api/swarm/status" in swarm
    assert "/api/replay/fork" in chat
    assert "cmd == \"/fork\"" in chat or "cmd == '/fork'" in chat
