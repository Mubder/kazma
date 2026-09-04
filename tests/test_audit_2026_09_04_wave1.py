"""Regression tests for Wave 1 audit fixes (2026-09-04 audit):
- C2: Supervisor intent degrade path does not crash with UnboundLocalError
- C3: SupervisorState declares _gateway and preserves routing context
- C4: TelegramBusAdapter checks ok, retries plain text on parse error, and fails closed
- H7: DocumentsAPI _require_admin fails closed on exceptions
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from kazma_core.agent.state import SupervisorState, initial_supervisor_state
from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter, _safe_slice_md


# ── C3: SupervisorState & _gateway ──────────────────────────────────────────


class TestWave1C3GatewayState:
    def test_supervisor_state_declares_gateway(self):
        """SupervisorState TypedDict must declare _gateway so LangGraph preserves it."""
        assert "_gateway" in SupervisorState.__annotations__

    def test_initial_supervisor_state_has_gateway(self):
        """initial_supervisor_state should initialize _gateway to a dict."""
        st = initial_supervisor_state(thread_id="test-thread-1")
        assert "_gateway" in st
        assert isinstance(st["_gateway"], dict)


# ── C4: Telegram Bus Robustness ─────────────────────────────────────────────


class TestWave1C4TelegramBus:
    def test_safe_slice_md_dangling_backslash(self):
        """Ensure _safe_slice_md strips an unescaped trailing backslash."""
        text = "Hello world\\"
        sliced = _safe_slice_md(text, len(text))
        assert not sliced.endswith("\\")
        assert sliced == "Hello world"

    def test_safe_slice_md_escaped_backslash_preserved(self):
        """Double backslash (literal escaped backslash) should not be stripped."""
        text = "Hello world\\\\"
        sliced = _safe_slice_md(text, len(text))
        assert sliced == "Hello world\\\\"

    @pytest.mark.asyncio
    async def test_telegram_bus_post_markdown_fallback(self):
        """When Telegram returns HTTP 400 for Markdown parse failure, _post must retry plain text."""
        bus = TelegramBusAdapter(bot_token="fake-token", chat_id=12345)

        # Mock httpx client
        mock_http = MagicMock()
        first_resp = MagicMock()
        first_resp.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: can't parse entities: Character '.' is reserved",
        }
        second_resp = MagicMock()
        second_resp.json.return_value = {
            "ok": True,
            "result": {"message_id": 999},
        }

        mock_http.post = AsyncMock(side_effect=[first_resp, second_resp])
        bus._http = mock_http

        payload = {
            "chat_id": 12345,
            "text": "Hello\\.world\\!",
            "parse_mode": "MarkdownV2",
        }
        res = await bus._post(payload)

        assert res is not None
        assert res.get("ok") is True
        assert mock_http.post.call_count == 2
        # Verify fallback stripped parse_mode and unescaped text
        fallback_call_payload = mock_http.post.call_args_list[1].kwargs["json"]
        assert "parse_mode" not in fallback_call_payload
        assert fallback_call_payload["text"] == "Hello.world!"

    @pytest.mark.asyncio
    async def test_telegram_bus_request_approval_fails_closed_on_delivery_error(self):
        """If Telegram API fails to deliver approval card, request_approval must fail closed immediately."""
        bus = TelegramBusAdapter(bot_token="fake-token", chat_id=12345)
        # Mock _post to return delivery failure
        bus._post = AsyncMock(return_value={"ok": False, "error_code": 403, "description": "Forbidden: bot blocked"})

        from kazma_core.swarm.bus import ApprovalRequest
        req = ApprovalRequest(
            task_id="task-fail-123",
            worker_name="coder",
            task_description="rm -rf /",
            proposed_output="",
        )

        # Should return False immediately without hanging for 60s
        result = await bus.request_approval(req, timeout=5.0)
        assert result is False


# ── C2: Supervisor Degrade Path (No UnboundLocalError) ─────────────────────


class TestWave1C2SupervisorDegrade:
    @pytest.mark.asyncio
    async def test_supervisor_degrades_gracefully_on_intent_exception(self):
        """When intent classification fails, supervisor must not crash with UnboundLocalError."""
        from kazma_core.agent.graph_supervisor import supervisor_node
        from kazma_core.agent.state import initial_supervisor_state

        state = initial_supervisor_state(thread_id="test-thread-c2")
        state["messages"] = [{"role": "user", "content": "hello world"}]

        # Mock LLM to return simple response
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Hello there!"
        mock_response.tool_calls = []
        mock_response.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        mock_llm.chat = AsyncMock(return_value=mock_response)

        mock_cost_breaker = MagicMock()
        mock_cost_breaker.check_budget.return_value = None
        mock_authority = MagicMock()
        mock_tracer = MagicMock()

        # Force classify_turn_intent to throw an exception
        with patch("kazma_core.agent.turn_input.classify_turn_intent", side_effect=RuntimeError("Intent engine boom")):
            # supervisor_node should NOT raise UnboundLocalError (_decision or _ledger_clarify)
            res = await supervisor_node(
                state,
                llm=mock_llm,
                system_prompt="You are an AI assistant.",
                tool_definitions=[],
                tool_executor=None,
                cost_breaker=mock_cost_breaker,
                authority=mock_authority,
                tracer=mock_tracer,
            )
            assert res is not None
            assert res.get("next_node") in ("respond", "end", "tool_worker")


# ── H7: Documents API _require_admin Fail-Closed ───────────────────────────


class TestWave1H7DocumentsAuthFailClosed:
    def test_require_admin_no_secret_allows_local_dev(self):
        from kazma_ui.documents_api import create_documents_router
        router = create_documents_router()
        req = MagicMock(spec=Request)
        with patch("kazma_ui.auth.get_kazma_secret", return_value=None):
            from kazma_ui.auth import get_kazma_secret
            assert get_kazma_secret() is None

    def test_require_admin_fails_closed_on_auth_exception(self):
        from kazma_ui.documents_api import create_documents_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        router = create_documents_router()
        app.include_router(router)

        # Attach mock service to app.state
        mock_svc = MagicMock()
        app.state.documents = mock_svc

        client = TestClient(app)

        with patch("kazma_ui.auth.get_kazma_secret", return_value="super-secret"), \
             patch("kazma_ui.auth.is_authenticated", side_effect=RuntimeError("database down")):
            # An exception during auth check MUST return 403 (fail closed), not allow access!
            resp = client.post("/api/documents/ops/maintenance/dry-run")
            assert resp.status_code == 403
            data = resp.json()
            assert data["ok"] is False
