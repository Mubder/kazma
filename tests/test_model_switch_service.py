"""Regression tests for the model-switch pipeline (reliability sprint).

Covers:
  - switch_active_model / ensure_active_model
  - env lock honesty
  - no silent ok on failure
  - chat.js no longer uses wall-clock forceEndTurn Done
  - source contracts for getters / turn_complete
"""

from __future__ import annotations

from tests._module_source import module_source

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kazma_core.config_store import ConfigStore
from kazma_core.model_registry import ModelRegistry, initialize_model_registry
from kazma_core.runtime.model_switch import (
    ensure_active_model,
    switch_active_model,
    switch_active_provider,
)

_UI = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_SSE = _UI / "sse_chat.py"
_WS = _UI / "routes" / "ws_chat.py"
_APP = _UI / "app.py"


@pytest.fixture()
def registry(tmp_path):
    db = tmp_path / "cfg.db"
    store = ConfigStore(str(db))
    reg = ModelRegistry(store)
    reg.upsert_provider({
        "name": "deepseek",
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test-real-key",
        "enabled": True,
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
    })
    reg.set_active_provider(provider="deepseek", model="deepseek-v4-pro")
    initialize_model_registry(store)
    # Re-bind the test registry into the process singleton used by switch helpers
    from kazma_core import model_registry as mr

    mr._registry = reg  # type: ignore[attr-defined]
    yield reg
    mr._registry = None  # type: ignore[attr-defined]


class TestSwitchActiveModel:
    def test_switch_ok(self, registry):
        agent = MagicMock()
        result = switch_active_model("deepseek-v4-flash", agent=agent, registry=registry)
        assert result.ok is True
        assert result.model == "deepseek-v4-flash"
        assert registry._active_model == "deepseek-v4-flash"
        agent.sync_active_model.assert_called_once()
        # Mirror chat model key
        assert registry._config_store.get("registry.active_chat_model") == "deepseek-v4-flash"

    def test_empty_model_errors(self, registry):
        result = switch_active_model("", registry=registry)
        assert result.ok is False
        assert result.error_code == "invalid_model"

    def test_env_lock_errors(self, registry, monkeypatch):
        monkeypatch.setenv("KAZMA_MODEL", "deepseek-v4-pro")
        result = switch_active_model("deepseek-v4-flash", registry=registry)
        assert result.ok is False
        assert result.error_code == "env_locked"
        # Registry must not flip under lock
        assert registry._active_model == "deepseek-v4-pro"
        monkeypatch.delenv("KAZMA_MODEL", raising=False)

    def test_ensure_noop_when_same(self, registry):
        agent = MagicMock()
        result = ensure_active_model("deepseek-v4-pro", agent=agent, registry=registry)
        assert result.ok is True
        agent.sync_active_model.assert_not_called()

    def test_ensure_switches_when_different(self, registry):
        agent = MagicMock()
        result = ensure_active_model("deepseek-v4-flash", agent=agent, registry=registry)
        assert result.ok is True
        assert result.model == "deepseek-v4-flash"
        agent.sync_active_model.assert_called_once()


class TestSwitchProviderNoMask:
    def test_masked_key_not_written(self, registry):
        # Seed a real key
        registry.upsert_provider({
            "name": "deepseek",
            "api_key": "sk-real-keep-me",
            "base_url": "https://api.deepseek.com/v1",
            "enabled": True,
        })
        result = switch_active_provider(
            "deepseek",
            model="deepseek-v4-flash",
            api_key="***",  # must be ignored
            registry=registry,
        )
        assert result.ok is True
        entry = registry.get_provider("deepseek")
        assert entry is not None
        assert entry.get("api_key") == "sk-real-keep-me"


class TestMarkdownRendererContracts:
    def test_streaming_js_supports_tables_lists_quotes(self):
        from pathlib import Path

        js = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui"
            / "kazma_ui"
            / "static"
            / "js"
            / "streaming.js"
        ).read_text(encoding="utf-8")
        assert "type: 'table'" in js or 'type: "table"' in js or "type: 'table'" in js
        assert "md-table" in js
        assert "isTableRow" in js
        assert "listItemMatch" in js
        assert "blockquote" in js
        assert "md-task" in js


class TestSourceContracts:
    def test_chat_js_no_wall_clock_force_done(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "TURN_IDLE_WATCHDOG_MS" in js
        assert "TURN_WATCHDOG_MS = 3 * 60 * 1000" not in js
        # Idle watchdog must not call forceEndTurn as success path
        # (forceEndTurn may still exist for Stop/ESC)
        assert "Still working in background" in js or "still_working_bg" in js
        # Turn Delivery V2: catch-up is the unconditional snapshot resync
        # (the old pollBackgroundTurn patch mechanism was deleted).
        assert "_resyncDelivery" in js
        assert "pollBackgroundTurn" not in js

    def test_chat_js_awaits_model_put_errors(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "status === 'error'" in js or 'status === "error"' in js
        assert "error_code" in js or "Model switch failed" in js

    def test_sse_has_llm_provider_getter(self):
        src = module_source(_SSE)
        assert "llm_provider_getter" in src
        assert "pin_turn_model" in src
        assert "ensure_active_model" not in src
        assert "turn_complete" in src

    def test_ws_ensure_active_model(self):
        src = _WS.read_text(encoding="utf-8")
        assert "pin_turn_model" in src
        assert "ensure_active_model" not in src
        assert "turn_complete" in src
        assert 'payload.get("model")' in src or "payload.get('model')" in src

    def test_ws_approve_emits_turn_complete_not_append_delta(self):
        """Post-HITL resume must paint via turn_complete (replace), not llm_delta append.

        Industry regression: YOLO/approve finished server-side but the client only
        saw the answer after F5, and full-text llm_delta doubled the bubble.
        """
        src = _WS.read_text(encoding="utf-8")
        assert 'source": "hitl_resume"' in src or "source': 'hitl_resume'" in src or 'source": "hitl_resume"' in src
        assert "emit_delta=False" in src
        assert "preparePostApprovalTurn" not in src  # client-side only
        # Approve path must still register turn_complete TelemetryEvent
        assert "approve turn_complete" in src

    def test_app_uses_live_getters(self):
        src = _APP.read_text(encoding="utf-8")
        assert "llm_provider_getter" in src
        assert "graph_getter=lambda: self._graph_holder.get(\"graph\")" in src
        # Old broken self.graph getter must be gone
        assert "graph_getter=lambda: self.graph" not in src
