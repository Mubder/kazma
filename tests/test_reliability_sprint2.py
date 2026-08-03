"""Reliability Sprint 2 — heartbeats, model stamps, health chip, source contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_UI = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_WS = _UI / "routes" / "ws_chat.py"
_SSE = _UI / "sse_chat.py"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_STREAM_JS = _UI / "static" / "js" / "streaming.js"
_DASH_JS = _UI / "static" / "js" / "dashboard.js"
_SESSION = _UI / "session_manager.py"
_HEALTH = _UI / "health.py"
_GRAPH = (
    Path(__file__).resolve().parent.parent
    / "kazma-gateway"
    / "kazma_gateway"
    / "agent_handler"
    / "graph.py"
)


class TestSourceContractsSprint2:
    def test_ws_has_long_turn_heartbeat(self):
        src = _WS.read_text(encoding="utf-8")
        assert "_long_turn_heartbeat" in src
        assert "Still working" in src
        assert "15.0" in src or "15" in src

    def test_ws_persist_stamps_model(self):
        src = _WS.read_text(encoding="utf-8")
        assert 'sess.messages[-1]["model"]' in src or "model_id" in src
        assert "model=" in src  # _persist_final_assistant_message(..., model=)

    def test_sse_stamps_model_on_assistant(self):
        src = _SSE.read_text(encoding="utf-8")
        assert 'temp_assistant_msg["model"]' in src or "_turn_model" in src

    def test_chat_js_sse_status_cot(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "onStatus:" in js
        assert "synthesizing" in js

    def test_streaming_js_dispatches_status(self):
        js = _STREAM_JS.read_text(encoding="utf-8")
        assert "status_update" in js
        assert "onStatus" in js

    def test_session_add_message_accepts_model(self):
        src = _SESSION.read_text(encoding="utf-8")
        assert "model: str | None" in src or "model:" in src
        assert 'msg["model"]' in src

    def test_health_exposes_active_model(self):
        src = _HEALTH.read_text(encoding="utf-8")
        assert "active_model" in src
        assert "active_provider" in src

    def test_dashboard_js_updates_active_model(self):
        js = _DASH_JS.read_text(encoding="utf-8")
        assert "metric-active-model" in js
        assert "active_model" in js

    def test_gateway_graph_getter_proxy(self):
        src = _GRAPH.read_text(encoding="utf-8")
        assert "graph_getter" in src
        assert "_LiveGraph" in src or "_resolve_graph" in src


class TestHealthActiveModel:
    def test_check_model_registry_includes_active_fields(self, tmp_path, monkeypatch):
        from kazma_core.config_store import ConfigStore
        from kazma_core.model_registry import ModelRegistry, initialize_model_registry
        from kazma_ui.health import check_model_registry

        store = ConfigStore(str(tmp_path / "h.db"))
        reg = ModelRegistry(store)
        reg.upsert_provider({
            "name": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test",
            "enabled": True,
            "models": ["deepseek-v4-flash"],
        })
        reg.set_active_provider(provider="deepseek", model="deepseek-v4-flash")
        initialize_model_registry(store)
        from kazma_core import model_registry as mr

        mr._registry = reg  # type: ignore[attr-defined]
        try:
            out = check_model_registry()
            assert out["status"] == "ok"
            assert out.get("active_model") == "deepseek-v4-flash"
            assert out.get("active_provider") == "deepseek"
        finally:
            mr._registry = None  # type: ignore[attr-defined]


class TestSessionModelStamp:
    def test_add_message_stores_model(self):
        from kazma_ui.session_manager import ChatSession

        s = ChatSession(session_id="t1")
        s.add_message("assistant", "hello", model="deepseek-v4-flash")
        assert s.messages[-1]["model"] == "deepseek-v4-flash"
        assert s.messages[-1]["content"] == "hello"
