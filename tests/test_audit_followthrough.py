"""Regression tests for the A–F audit follow-through (one-shot)."""

from __future__ import annotations

import os
from pathlib import Path

from kazma_core.memory.hygiene import is_blocked_belief_triple
from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS
from kazma_core.constants import GRAPH_HITL_DANGER_TOOLS, SWARM_BUS_DANGER_TOOLS
from kazma_core.sessions.directory import format_session_list, SessionEntry
from kazma_core.ide.workspace_scope import current_workspace_id, pin_workspace, reset_workspace
from kazma_core.security.web_sessions import create_session
from kazma_gateway.routers.github import save_github_token_to_env
from kazma_tui.theme import KAZMA_THEME


def test_danger_list_aliases_are_canonical():
    assert GRAPH_HITL_DANGER_TOOLS == frozenset(CANONICAL_DANGER_TOOLS)
    assert SWARM_BUS_DANGER_TOOLS == frozenset(CANONICAL_DANGER_TOOLS)
    assert "email_send" in GRAPH_HITL_DANGER_TOOLS
    assert "install_agent_skill" in SWARM_BUS_DANGER_TOOLS


def test_belief_extractor_rejects_assistant_prose_predicate():
    assert is_blocked_belief_triple(
        "user",
        "here's the honest read of the situation",
        "memory embedding fixes",
    )
    assert not is_blocked_belief_triple("user", "lives_in", "Kuwait")


def test_github_token_not_written_to_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("FOO=1\n", encoding="utf-8")
    save_github_token_to_env("ghp_should_not_land_on_disk")
    text = env.read_text(encoding="utf-8")
    assert "ghp_should_not_land_on_disk" not in text
    assert os.environ.get("GITHUB_TOKEN") == "ghp_should_not_land_on_disk"


def test_tui_theme_overrides_hatch_with_percentage():
    assert "hatch: right 12%" in KAZMA_THEME
    assert "Screen.-maximized-view" in KAZMA_THEME


def test_session_list_prefers_short_id():
    text = format_session_list(
        [
            SessionEntry(
                session_id="aaaaaaaa-bbbb-cccc-ddddeeee",
                thread_id="aaaaaaaa-bbbb-cccc-ddddeeee",
                title="/yolo",
                platform="web",
                origin="web",
                message_count=2,
                updated_at="",
            )
        ]
    )
    assert "/session #short_id" in text
    assert "#ddddeeee" in text


def test_pin_workspace_roundtrip():
    assert current_workspace_id() is None
    tok = pin_workspace("ws-audit-1")
    assert current_workspace_id() == "ws-audit-1"
    reset_workspace(tok)
    assert current_workspace_id() is None


def test_create_session_stores_tenant_id(tmp_path, monkeypatch):
    from kazma_core.config_store import ConfigStore, set_config_store, reset_config_store

    store = ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(tmp_path / "missing.yaml"),
    )
    set_config_store(store)
    try:
        sid = create_session(actor="test", username="u", tenant_id="tenant-x")
        assert sid
        import hashlib

        digest = hashlib.sha256(sid.encode("utf-8")).hexdigest()
        payload = store.get(f"web_session.{digest}")
        assert payload["tenant_id"] == "tenant-x"
    finally:
        reset_config_store()


def test_discord_sender_includes_user():
    from kazma_gateway.adapters.discord_parse import parse_message_create

    msg = parse_message_create(
        {
            "id": "1",
            "content": "hi",
            "channel_id": "chan9",
            "author": {"id": "user7", "username": "ali"},
        }
    )
    assert msg is not None
    assert msg.sender_id == "discord:user7:chan9"


def test_commitment_unknown_mutators_default_on():
    from kazma_core.safety.commitment.config import get_commitment_config

    cfg = get_commitment_config()
    assert cfg["enforce_unknown_mutators"] is True
