"""Regression tests for the 2026-08-31 remaining-work audit one-shot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_hitl_default_timeout_is_five_minutes():
    from kazma_core.safety.hitl import (
        DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        get_hitl_config,
    )

    assert DEFAULT_APPROVAL_TIMEOUT_SECONDS == 300
    cfg = get_hitl_config({"safety": {"hitl": {"enabled": True}}})
    assert cfg["approval_timeout_seconds"] == 300


def test_graph_commitment_gate_passes_enforce_unknown_mutators():
    src = (
        _ROOT
        / "kazma-core"
        / "kazma_core"
        / "agent"
        / "graph_tool_worker.py"
    ).read_text(encoding="utf-8")
    # The call site passes `enforce_unknown=_enforce_unknown`, which
    # `authorize_effect` accepts as an alias for `enforce_unknown_mutators`.
    # Pinning one spelling failed the build over a keyword rename while the
    # value was still being read from config and handed to the gate -- which
    # is the thing this test is about.
    assert (
        "enforce_unknown_mutators=_enforce_unknown" in src
        or "enforce_unknown=_enforce_unknown" in src
    ), "the configured enforce-unknown-mutators flag is not passed to the gate"
    assert "get_commitment_config()" in src


@pytest.mark.parametrize("owner", ["", "telegram:other"])
async def test_hitl_gateway_cache_cannot_authorize_cross_thread(monkeypatch, owner):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler.hitl import _handle_hitl_resume
    from kazma_gateway.gateway import IncomingMessage

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    if owner:
        record_thread_owner("durable-cross-thread", owner)
    # A cache row claiming this sender must not override missing or different
    # durable ownership, even when it has enough routing data to resume.
    store = SimpleNamespace(get=AsyncMock(return_value={
        "sender_id": "telegram:42", "platform": "telegram", "chat_id": 42,
    }))
    graph = SimpleNamespace(aget_state=AsyncMock(), ainvoke=AsyncMock())
    manager = SimpleNamespace(send=AsyncMock())
    msg = IncomingMessage("telegram", "telegram:42",
                          "/hitl approve durable-cross-thread",
                          context_metadata={"chat_id": 42})

    assert await _handle_hitl_resume(msg, graph, {}, "current-thread", store, manager)
    graph.aget_state.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()
    assert "authorized" in manager.send.call_args.args[0].text.lower()


def test_troubleshooting_does_not_claim_no_429():
    text = (
        _ROOT / "docs" / "docs" / "guide" / "troubleshooting-and-workarounds.md"
    ).read_text(encoding="utf-8")
    assert "has **no 429 backoff**" not in text
    assert "does** retry 429" in text or "does retry 429" in text.lower() or "retry 429" in text


def test_production_checklist_commitment_defaults():
    text = (
        _ROOT / "docs" / "docs" / "ops" / "production-checklist.md"
    ).read_text(encoding="utf-8")
    assert "enforce_unknown_mutators` defaults **ON**" in text


def test_canonical_floor_auto_on_in_production(monkeypatch):
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, get_hitl_config

    narrowed = {
        "safety": {
            "hitl": {
                "enabled": True,
                "require_approval_for": ["file_write"],
            }
        }
    }
    monkeypatch.delenv("KAZMA_HITL_CANONICAL_FLOOR", raising=False)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    effective = get_hitl_config(narrowed)["require_approval_for"]
    assert set(CANONICAL_DANGER_TOOLS) <= set(effective)


def test_supervisor_route_uses_defaults_without_yaml_router():
    from kazma_core.models.router import TaskProfile
    from kazma_core.models.selection import resolve_supervisor_route

    class _Store:
        def get(self, key, default=None):
            if key == "models.defaults.code":
                return "pinned-coder"
            return default

    class _Reg:
        _config_store = _Store()

        def _env_locked(self):
            return False

        def find_provider_for_model(self, model):
            return "openai" if model == "pinned-coder" else None

        def get_client_by_provider(self, name, model=None):
            return object()

    model_id, client, profile = resolve_supervisor_route(
        "write a Python function",
        model_router=None,
        registry=_Reg(),
    )
    assert profile == TaskProfile.CODING.value
    assert model_id == "pinned-coder"
    assert client is not None


def test_single_owner_nginx_has_websocket_and_dynamic_resolution():
    conf = (_ROOT / "deploy" / "nginx-ha.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in conf
    assert "zone kazma_backend" in conf
    assert "server kazma:8000 resolve;" in conf
    assert "Upgrade $http_upgrade" in conf
    assert "location /ws/" in conf


def test_ha_compose_is_labelled_demo_not_full_ha():
    text = (_ROOT / "docker-compose.ha.yml").read_text(encoding="utf-8")
    assert "not qualified cross-host ha" in text.lower()
    assert "replicas: 1" in text
    assert "kazma_data" in text


@pytest.mark.asyncio
async def test_gmail_list_messages_walks_page_tokens():
    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend
    from kazma_skills.native.email_manager.models import ListQuery

    list_pages = [
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        {"messages": [{"id": "c"}, {"id": "d"}]},
    ]
    list_calls: list[dict[str, Any]] = []

    class _Fake(GmailApiBackend):
        async def _request(self, method, path, params=None, json=None):  # noqa: A002
            if path.rstrip("/").endswith("/messages"):
                list_calls.append(dict(params or {}))
                return list_pages[len(list_calls) - 1]
            mid = path.rsplit("/", 1)[-1]
            return {"id": mid}

    fake = _Fake(access_token="x")
    fake._map = lambda meta, body="": meta  # type: ignore[method-assign]
    msgs = await fake.list_messages(ListQuery(limit=2, offset=2))
    assert [m["id"] for m in msgs] == ["c", "d"]
    assert "pageToken" not in list_calls[0]
    assert list_calls[1].get("pageToken") == "p2"


@pytest.mark.asyncio
async def test_bedrock_converse_stream_yields_deltas(monkeypatch):
    from kazma_core.bedrock_llm import BedrockProvider
    from kazma_core.llm_stream import StreamDelta

    events = [
        {"contentBlockDelta": {"delta": {"text": "Hel"}}},
        {"contentBlockDelta": {"delta": {"text": "lo"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 2}}},
    ]

    class _Client:
        def converse_stream(self, **kwargs):
            return {"stream": events}

    provider = BedrockProvider()
    monkeypatch.setattr(provider, "_get_client", lambda: _Client())
    chunks: list[str] = []
    final = None
    async for delta in provider.chat_stream(
        [{"role": "user", "content": "hi"}],
    ):
        assert isinstance(delta, StreamDelta)
        if delta.content:
            chunks.append(delta.content)
        if delta.response is not None:
            final = delta.response
    assert "".join(chunks) == "Hello"
    assert final is not None
    assert "Hello" in (final.content or "")


def test_tui_swarm_dispatch_is_http():
    chat = (_ROOT / "kazma-tui" / "kazma_tui" / "chat.py").read_text(encoding="utf-8")
    assert 'POST", "/api/swarm/dispatch"' in chat or "/api/swarm/dispatch" in chat
    assert "get_swarm_engine" not in chat


def test_soul_confirm_has_web_surface():
    html = (
        _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    assert "soul_requires_confirm" in html
    assert "confirmSoul" in html
    js = (
        _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_agent.js"
    ).read_text(encoding="utf-8")
    assert "/api/commitment/soul/" in js
