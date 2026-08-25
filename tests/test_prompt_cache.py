"""Stable system prefix + Anthropic cache_control (industry stack part 3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.llm_provider import LLMConfig, hoist_system_messages
from kazma_core.prompt_cache import (
    build_anthropic_system,
    is_dynamic_system,
    pack_system_messages,
    stamp_anthropic_tool_cache,
)


def test_pack_keeps_identity_prefix_stable() -> None:
    identity = "You are Kazma, an autonomous agent."
    msgs = [
        {"role": "system", "content": identity},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "system", "content": "[KAZMA_WORKING_MEMORY]\ngoal: x"},
        {"role": "user", "content": "again"},
    ]
    packed = pack_system_messages(msgs)
    assert packed[0]["content"] == identity
    assert "[KAZMA_WORKING_MEMORY]" in packed[1]["content"]
    assert packed[2]["role"] == "user"
    # Second turn with a different WM must not change message[0].
    msgs2 = [
        {"role": "system", "content": identity},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "[KAZMA_WORKING_MEMORY]\ngoal: y"},
    ]
    packed2 = pack_system_messages(msgs2)
    assert packed2[0]["content"] == packed[0]["content"]


def test_dynamic_markers() -> None:
    assert is_dynamic_system({"role": "system", "content": "LANGUAGE LOCK: ar"})
    assert is_dynamic_system({"role": "system", "content": "SYSTEM BUDGET CHECK: 20"})
    assert not is_dynamic_system({"role": "system", "content": "You are Kazma."})


def test_legacy_hoist_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PROMPT_CACHE", "0")
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "note"},
        {"role": "assistant", "content": "ok"},
    ]
    out = hoist_system_messages(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant"]


def test_anthropic_system_cache_breakpoint() -> None:
    msgs = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "system", "content": "[KAZMA_WORKING_MEMORY]\ngoal"},
        {"role": "user", "content": "hi"},
    ]
    sys_payload = build_anthropic_system(msgs)
    assert isinstance(sys_payload, list)
    assert sys_payload[0]["cache_control"] == {"type": "ephemeral"}
    assert sys_payload[0]["text"] == "You are Kazma."
    assert "[KAZMA_WORKING_MEMORY]" in sys_payload[1]["text"]
    assert "cache_control" not in sys_payload[1]


def test_stamp_last_tool() -> None:
    tools = [{"name": "a"}, {"name": "b"}]
    out = stamp_anthropic_tool_cache(tools)
    assert "cache_control" not in out[0]
    assert out[-1]["cache_control"]["type"] == "ephemeral"
    assert tools[-1].get("cache_control") is None  # original not mutated


@pytest.mark.asyncio
async def test_anthropic_chat_sends_cached_system() -> None:
    from kazma_core.anthropic_llm import AnthropicProvider

    p = AnthropicProvider(LLMConfig(api_key="sk-ant", model="claude-sonnet-4"))
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.is_closed = False
    p._http = mock_client
    await p.chat(
        [
            {"role": "system", "content": "You are Kazma."},
            {"role": "system", "content": "[KAZMA_WORKING_MEMORY]\ngoal"},
            {"role": "user", "content": "hi"},
        ]
    )
    payload = mock_client.post.call_args.kwargs["json"]
    assert isinstance(payload["system"], list)
    assert payload["system"][0]["cache_control"]["type"] == "ephemeral"
    assert payload["messages"][0]["role"] == "user"
