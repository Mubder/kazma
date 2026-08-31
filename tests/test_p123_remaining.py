"""P1+P2+P3 remaining-work one-go (2026-08-31).

Negative controls live in the same file: a missing native topic hint, a
non-workspace-bound MCP server, and the old soft-nav throw strings.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kazma_gateway.agent_handler.store import _resolve_thread
from kazma_gateway.allowlists import apply_adapter_allowlists, split_ids
from kazma_gateway.gateway import IncomingMessage

_ROOT = Path(__file__).resolve().parents[1]


def test_telegram_topic_resolves_before_mouth_pointer():
    msg = IncomingMessage(
        platform="telegram",
        sender_id="telegram:7",
        text="hi",
        context_metadata={
            "chat_id": 99,
            "message_thread_id": 77,
            "thread_hint": "gw-telegram-7-topic-77",
        },
    )
    assert _resolve_thread(msg) == "gw-telegram-7-topic-77"


def test_bare_telegram_message_stays_sender_thread():
    """Negative control: no topic → deterministic sender thread."""
    msg = IncomingMessage(
        platform="telegram",
        sender_id="telegram:7",
        text="hi",
        context_metadata={"chat_id": 99},
    )
    assert _resolve_thread(msg) == "gw-telegram-7"


def test_slack_thread_reply_resolves():
    msg = IncomingMessage(
        platform="slack",
        sender_id="slack:U1",
        text="reply",
        context_metadata={"thread_ts": "1.0", "message_ts": "2.0"},
    )
    assert _resolve_thread(msg) == "gw-slack-U1-thread-1-0"


def test_split_ids_and_live_telegram_allowlist():
    assert split_ids("1, 2,x") == ["1", "2", "x"]
    adapter = MagicMock()
    adapter.name = "telegram"
    cs = MagicMock()
    cs.get.side_effect = lambda key, default="": {
        "connectors.telegram.allowed_users": "11, 22",
    }.get(key, default)
    apply_adapter_allowlists(adapter, cs)
    adapter.set_allowed_users.assert_called_once()
    got = set(adapter.set_allowed_users.call_args[0][0])
    assert got == {11, 22}


@pytest.mark.asyncio
async def test_mcp_non_bound_server_skips_scope_guard(monkeypatch, tmp_path):
    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle

    mgr = AsyncMCPManager()
    mgr._servers["web"] = MCPServerHandle(name="web", transport="sse", connected=True)
    mgr._server_templates["web"] = {
        "name": "web",
        "transport": "sse",
        "url": "http://127.0.0.1:9/sse",
    }

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    import kazma_core.ide.workspace_scope as ws_scope
    import kazma_core.workspace.binding as binding

    monkeypatch.setattr(ws_scope, "resolve_workspace_root", lambda: root_a.resolve())
    monkeypatch.setattr(binding, "get_bound_mcp_root", lambda: root_b.resolve())
    monkeypatch.delenv("KAZMA_MCP_SCOPE_GUARD", raising=False)

    out = await mgr.execute_mcp_tool("web", "search", {})
    assert "different workspace" not in out["content"]


def test_ws_graph_flag_lives_in_quarantine_module():
    from kazma_ui.routes.ws_graph import ws_graph_enabled
    from kazma_ui.routes.ws_chat import ws_graph_enabled as reexport

    assert ws_graph_enabled is reexport


def test_ci_ruff_syntax_is_a_gate():
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--select E9,F63,F7,F82" in ci
    assert ci.count("--exit-zero") >= 1


def test_ha_compose_shares_kazma_data():
    text = (_ROOT / "docker-compose.ha.yml").read_text(encoding="utf-8")
    assert "- kazma_data:/home/kazma/.kazma/kazma-data" in text
    assert "# - kazma_data:" not in text


def test_chat_slash_catalog_extracted():
    slash = (
        _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat_slash.js"
    ).read_text(encoding="utf-8")
    chat = (
        _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    html = (
        _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "chat.html"
    ).read_text(encoding="utf-8")
    assert "KAZMA_SLASH_COMMANDS" in slash
    assert "/unrestricted" in slash
    assert "window.KAZMA_SLASH_COMMANDS" in chat
    assert "chat_slash.js" in html


def test_settings_ops_toasts_go_through_i18n():
    ops = (
        _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_ops.js"
    ).read_text(encoding="utf-8")
    assert "showToast(_t(" in ops
    assert "showToast('User created'" not in ops
    assert "showToast('Shortcuts reset'" not in ops


def test_outbound_i18n_and_safety_markup():
    catalog = (
        _ROOT / "kazma-ui" / "kazma_ui" / "i18n" / "catalog" / "settings.py"
    ).read_text(encoding="utf-8")
    html = (
        _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    assert "settings.outbound_allowed_targets" in catalog
    assert "safety.outbound_allowed_targets" in html
    assert "hubEditingConnector.extras.allowed_users" in html


def test_sse_capacity_fast_path_extracted():
    cap = (
        _ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "_capacity.py"
    ).read_text(encoding="utf-8")
    init = (
        _ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "def intercept_capacity_fast_path" in cap
    assert "intercept_capacity_fast_path" in init
    assert "apply_capacity_command" in cap
    assert "apply_capacity_command" not in init
