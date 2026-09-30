"""No LLM call is answered from a cache of another request (audit 2026-09-30, AUD-001).

``KAZMA_SEMANTIC_CACHE=true`` keyed a cache on the whole conversation and
replayed any stored response within cosine 0.95 -- tool calls included. The
system prompt dominates such an embedding, so reproduced with the real
encoder, after one turn "Delete the file notes/a.txt", the requests "Delete
the file notes/b.txt" AND "What is the weather in Kuwait tomorrow?" both got
the stored ``file_delete notes/a.txt`` back. The feature is removed; the
switch now only warns at boot.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider

SYSTEM = {"role": "system", "content": "You are Kazma. " + "Follow the operator's rules carefully. " * 300}
TURN_A = [SYSTEM, {"role": "user", "content": "Delete the file notes/a.txt"}]
TURN_B = [SYSTEM, {"role": "user", "content": "What is the weather in Kuwait tomorrow?"}]


def _reply(text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "model": "m",
    }
    resp.raise_for_status = MagicMock()
    return resp


async def _two_turns(chat) -> tuple[str, str]:
    a = await chat(TURN_A)
    b = await chat(TURN_B)
    return a.content, b.content


def _assert_each_request_answered_for_itself(answers: tuple[str, str]) -> None:
    assert answers == ("answer to A", "answer to B"), answers


@pytest.mark.asyncio
async def test_the_old_switch_does_not_bring_replay_back(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_SEMANTIC_CACHE", "true")
    provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="test"))
    client = AsyncMock()
    client.post = AsyncMock(side_effect=[_reply("answer to A"), _reply("answer to B")])
    client.is_closed = False
    provider._http = client

    _assert_each_request_answered_for_itself(await _two_turns(provider.chat))
    assert client.post.await_count == 2, "each request reached the provider"


@pytest.mark.asyncio
async def test_negative_control_a_replaying_cache_is_caught() -> None:
    """The check above fails on the removed behaviour: a cache that answers
    the second request with the first's response."""
    first: dict = {}

    async def replaying_chat(messages):
        if "answer" in first:
            return first["answer"]
        first["answer"] = MagicMock(content="answer to A")
        return first["answer"]

    with pytest.raises(AssertionError):
        _assert_each_request_answered_for_itself(await _two_turns(replaying_chat))


def test_the_module_is_gone_and_nothing_imports_a_response_cache() -> None:
    import importlib.util

    assert importlib.util.find_spec("kazma_core.swarm.semantic_cache") is None


def test_a_set_switch_warns_at_boot(monkeypatch, caplog) -> None:
    from kazma_core import path_refresh
    from kazma_ui.app import KazmaAppBuilder

    monkeypatch.setattr(path_refresh, "_os_path_settings", lambda: [])
    monkeypatch.setenv("KAZMA_SEMANTIC_CACHE", "true")
    builder = KazmaAppBuilder()
    builder._env_files_loaded = []
    with caplog.at_level(logging.WARNING, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    assert any("KAZMA_SEMANTIC_CACHE is set" in r.getMessage() for r in caplog.records)

    caplog.clear()
    monkeypatch.delenv("KAZMA_SEMANTIC_CACHE")
    with caplog.at_level(logging.WARNING, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    assert not any("KAZMA_SEMANTIC_CACHE" in r.getMessage() for r in caplog.records)
