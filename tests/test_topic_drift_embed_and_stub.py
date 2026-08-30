"""Embedding topic-drift + prior tool-chain stubbing."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from kazma_core.agent.topic_drift import (
    cosine_distance,
    semantic_topic_drift,
    should_stub_prior_tools,
    stub_prior_tool_chains,
    topic_drift_config,
)
from kazma_core.agent.turn_input import classify_turn_intent


def test_cosine_distance_identical_and_orthogonal():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    c = [0.0, 1.0, 0.0]
    d_same = cosine_distance(a, b)
    assert d_same is not None
    assert abs(d_same) < 1e-6
    d = cosine_distance(a, c)
    assert d is not None
    assert abs(d - 1.0) < 1e-6


def test_semantic_drift_fail_open_no_embedder():
    with patch("kazma_core.memory.embedder.get_embedder", return_value=None):
        assert (
            semantic_topic_drift(
                "What is the weather in Kuwait today?",
                "Please clean up the memory graph entities for kazma",
            )
            is False
        )


def test_semantic_drift_detects_dissimilar_with_mock_embedder():
    class _Emb:
        dim = 2

        def encode(self, text: str) -> list[float]:
            t = text.lower()
            if "weather" in t or "طقس" in t:
                return [1.0, 0.0]
            if "memory" in t or "graph" in t or "entity" in t:
                return [0.0, 1.0]
            return [0.7, 0.7]

        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(x) for x in texts]

    with patch("kazma_core.memory.embedder.get_embedder", return_value=_Emb()):
        # S2-2: interrogatives are gated BEFORE the embedder — a question is
        # categorically not a drift shift ("what going on?" incident class).
        # Drift mechanics are therefore exercised with declarative text.
        assert (
            semantic_topic_drift(
                "Kuwait weather forecast: sunny and humid all week long",
                "Please clean up the memory graph entities for kazma and ShipX",
                threshold=0.55,
                enabled=True,
            )
            is True
        )
        assert (
            semantic_topic_drift(
                "What is the weather in Kuwait today please?",
                "Please clean up the memory graph entities for kazma and ShipX",
                threshold=0.55,
                enabled=True,
            )
            is False  # interrogative gate wins over a maximally distant embedder
        )
        assert (
            semantic_topic_drift(
                "Please also merge duplicate memory graph entities for kazma",
                "Please clean up the memory graph entities for kazma and ShipX",
                threshold=0.55,
                enabled=True,
            )
            is False
        )


def test_classify_uses_embedding_when_goal_set():
    class _Emb:
        dim = 2

        def encode(self, text: str) -> list[float]:
            t = text.lower()
            if "email" in t or "inbox" in t:
                return [1.0, 0.0]
            return [0.0, 1.0]

        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(x) for x in texts]

    goal = "Clean memory graph entities merge junk true false under kazma hierarchy"
    history = [
        {"role": "user", "content": goal},
        {"role": "assistant", "content": "Working on entity merge."},
    ]
    with patch("kazma_core.memory.embedder.get_embedder", return_value=_Emb()):
        mode = classify_turn_intent(
            "Can you summarize my latest inbox emails for today?",
            messages=history,
            task_status="in_progress",
            task_goal_summary=goal,
            use_embedding_drift=True,
        )
    # S2-1: embedding drift is shift_INFERRED — recall stays on, prose kept.
    assert mode == "shift_inferred"


def test_should_stub_prior_tools_matrix():
    assert should_stub_prior_tools(intent_mode="shift", prev_task_status="in_progress")
    # S2-1 split: legacy "shift" is explicit; both shift kinds stub.
    assert should_stub_prior_tools(intent_mode="shift_explicit", prev_task_status="in_progress")
    assert should_stub_prior_tools(intent_mode="shift_inferred", prev_task_status="in_progress")
    assert should_stub_prior_tools(intent_mode="normal", prev_task_status="completed")
    assert should_stub_prior_tools(intent_mode="normal", prev_task_status="superseded")
    assert not should_stub_prior_tools(intent_mode="continue", prev_task_status="in_progress")
    assert not should_stub_prior_tools(intent_mode="continue", prev_task_status="completed")
    assert not should_stub_prior_tools(intent_mode="normal", prev_task_status="in_progress")


def test_stub_prior_tool_chains_collapses_old_tools():
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Kazma."},
        {"role": "user", "content": "Clean memory entities for kazma graph junk"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "memory_list_entities", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "entity A, entity B, " + ("x" * 5000),
        },
        {
            "role": "assistant",
            "content": "I listed entities and will merge next.",
        },
        {"role": "user", "content": "What's the weather?"},
    ]
    out = stub_prior_tool_chains(msgs, keep_last_n_user_turns=1)
    roles = [m.get("role") for m in out if isinstance(m, dict)]
    # No raw tool role left in the prior segment
    assert "tool" not in roles or roles.count("tool") == 0
    # Stubbed assistant present
    stub_hits = [
        m
        for m in out
        if isinstance(m, dict)
        and m.get("role") == "assistant"
        and "Executed tools" in str(m.get("content") or "")
    ]
    assert stub_hits, out
    # Latest user preserved
    assert out[-1]["role"] == "user"
    assert "weather" in out[-1]["content"].lower()
    # Giant tool payload gone
    blob = " ".join(str(m.get("content") or "") for m in out if isinstance(m, dict))
    assert "x" * 100 not in blob


def test_stub_keeps_tools_in_last_user_turn():
    msgs = [
        {"role": "user", "content": "old task clean memory"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old_1",
                    "type": "function",
                    "function": {"name": "memory_list_entities", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old_1", "content": "old results " + ("y" * 2000)},
        {"role": "user", "content": "now do weather"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "new_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "new_1", "content": "sunny 30C"},
    ]
    out = stub_prior_tool_chains(msgs, keep_last_n_user_turns=1)
    # New turn tool retained
    assert any(
        isinstance(m, dict)
        and m.get("role") == "tool"
        and "sunny" in str(m.get("content") or "")
        for m in out
    )
    # Old giant payload gone
    blob = " ".join(str(m.get("content") or "") for m in out if isinstance(m, dict))
    assert "y" * 100 not in blob


def test_topic_drift_config_defaults():
    cfg = topic_drift_config()
    assert "enabled" in cfg
    assert "threshold" in cfg
    assert 0.05 <= float(cfg["threshold"]) <= 0.95
