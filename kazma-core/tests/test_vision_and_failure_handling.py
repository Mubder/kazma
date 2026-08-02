"""Tests for the two intertwined bug fixes:

1. **Silent finalization on LLM failure** ("model stopped thinking"):
   - ``LLMError.transient`` classifies failures.
   - The supervisor retries transient errors and surfaces honest errors
     (``turn_failed``) instead of disguising them as a final answer.
   - ``respond_node`` skips synthesis when the turn failed.

2. **Images routed to text-only models** (``image_url`` 400):
   - ``vision_capability`` classifies models (allow/deny lists).
   - ``analyze_image`` picks a vision model or fails clearly before the call.

These are pure-logic and monkeypatched tests — no real network/API calls.
"""

from __future__ import annotations

import pytest


# ──────────────────────────────────────────────────────────────────────
# Bug 2: vision_capability classifier
# ──────────────────────────────────────────────────────────────────────


def test_text_only_models_are_downgraded():
    from kazma_core.vision_capability import is_text_only

    assert is_text_only("deepseek-v4-pro")
    assert is_text_only("deepseek-chat")
    assert is_text_only("deepseek-r1")
    assert is_text_only("DeepSeek-V4-Pro")  # case-insensitive
    assert is_text_only("o1-mini")
    assert is_text_only("o3-mini")
    assert is_text_only("some-model-reasoner")


def test_vision_capable_models():
    from kazma_core.vision_capability import is_vision_capable

    assert is_vision_capable("gpt-4o")
    assert is_vision_capable("gpt-4o-mini")
    assert is_vision_capable("gpt-4o-2024-08-06")
    assert is_vision_capable("gpt-4.1")
    assert is_vision_capable("claude-3-5-sonnet-20241022")
    assert is_vision_capable("claude-sonnet-4")
    assert is_vision_capable("gemini-2.0-flash")
    assert is_vision_capable("pixtral-12b")
    assert is_vision_capable("qwen2-vl-7b")


def test_unknown_models_are_not_downgraded_fail_open():
    """Fail-safe: an unrecognized model is NOT treated as text-only."""
    from kazma_core.vision_capability import is_text_only, is_vision_capable

    assert is_text_only("totally-unknown-model") is False
    # And it is not on the vision allow-list either.
    assert is_vision_capable("totally-unknown-model") is False


def test_text_only_model_is_never_vision_capable():
    """Even if a model matched both lists, text-only wins (deny-list priority)."""
    from kazma_core.vision_capability import is_vision_capable

    assert is_vision_capable("deepseek-v4-pro") is False


def test_empty_and_none_inputs():
    from kazma_core.vision_capability import is_text_only, is_vision_capable

    assert is_text_only("") is False
    assert is_text_only(None) is False
    assert is_vision_capable("") is False
    assert is_vision_capable(None) is False


def test_env_override_adds_vision_model(monkeypatch):
    """KAZMA_VISION_MODELS extends the vision allow-list at runtime."""
    monkeypatch.setenv("KAZMA_VISION_MODELS", "my-custom-vlm,another-vision")
    from kazma_core.vision_capability import is_vision_capable

    assert is_vision_capable("my-custom-vlm")
    assert is_vision_capable("another-vision")


class _FakeRegistry:
    """Minimal stand-in for ModelRegistry used by find_configured_vision_model."""

    def __init__(self, providers, visible):
        self._providers = providers
        self._visible = visible

    def list_providers(self):
        return self._providers

    def get_visible_models(self, name):
        return self._visible.get(name, [])


def test_find_configured_vision_model_picks_first_match():
    from kazma_core.vision_capability import find_configured_vision_model

    registry = _FakeRegistry(
        providers=[
            {"name": "deepseek", "enabled": True, "api_key": "sk-ds"},
            {"name": "openai", "enabled": True, "api_key": "sk-oa"},
        ],
        visible={
            "deepseek": ["deepseek-v4-pro", "deepseek-chat"],
            "openai": ["gpt-4o", "gpt-4o-mini"],
        },
    )
    assert find_configured_vision_model(registry) == "gpt-4o"


def test_find_configured_vision_model_skips_disabled_and_keyless():
    from kazma_core.vision_capability import find_configured_vision_model

    registry = _FakeRegistry(
        providers=[
            {"name": "openai", "enabled": False, "api_key": "sk"},  # disabled
            {"name": "gemini", "enabled": True, "api_key": ""},      # no key
            {"name": "anthropic", "enabled": True, "api_key": "sk-cl"},
        ],
        visible={
            "openai": ["gpt-4o"],
            "gemini": ["gemini-2.0-flash"],
            "anthropic": ["claude-3-5-sonnet"],
        },
    )
    assert find_configured_vision_model(registry) == "claude-3-5-sonnet"


def test_find_configured_vision_model_returns_none_when_absent():
    from kazma_core.vision_capability import find_configured_vision_model

    registry = _FakeRegistry(
        providers=[{"name": "deepseek", "enabled": True, "api_key": "sk"}],
        visible={"deepseek": ["deepseek-v4-pro"]},
    )
    assert find_configured_vision_model(registry) is None


# ──────────────────────────────────────────────────────────────────────
# Bug 1: LLMError.transient classification
# ──────────────────────────────────────────────────────────────────────


def test_llm_error_transient_flag_defaults_false():
    from kazma_core.llm_provider import LLMError

    assert LLMError("boom").transient is False


def test_llm_error_transient_flag_settable():
    from kazma_core.llm_provider import LLMError

    assert LLMError("net", transient=True).transient is True
    assert LLMError("bad", transient=False).transient is False


def test_friendly_llm_error_marks_warnings():
    """friendly_llm_error must prefix failures with ⚠️ so they are never
    mistaken for a normal model reply (the 'model stopped thinking' symptom)."""
    from kazma_core.retry import friendly_llm_error
    from kazma_core.llm_provider import LLMError

    transient_msg = friendly_llm_error(LLMError("net", transient=True))
    assert transient_msg.startswith("⚠️")
    assert "lost the connection" in transient_msg or "unavailable" in transient_msg

    permanent_msg = friendly_llm_error(
        LLMError("LLM call failed (HTTP 400): bad image", transient=False)
    )
    assert permanent_msg.startswith("⚠️")
    assert "bad image" in permanent_msg


def test_friendly_llm_error_truncates_long_messages():
    from kazma_core.retry import friendly_llm_error
    from kazma_core.llm_provider import LLMError

    long = "x" * 500
    msg = friendly_llm_error(LLMError(long, transient=False))
    assert msg.startswith("⚠️")
    # 240-char cap + ellipsis + prefix keeps it readable.
    assert len(msg) < 300


# ──────────────────────────────────────────────────────────────────────
# Bug 1: respond_node skips synthesis when the turn failed
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_respond_node_skips_synthesis_when_turn_failed(monkeypatch):
    """When turn_failed=True, respond_node must NOT call the LLM to
    synthesize a fake final answer — it surfaces the honest error."""
    from kazma_core.agent import graph_builder as gb

    call_count = {"n": 0}

    class _LLM:
        async def chat(self, messages, tools=None):
            call_count["n"] += 1
            # If synthesis runs, it would return a made-up answer.
            return type("_Resp", (), {"content": "FABRICATED ANSWER"})()

    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )

    state = {
        "messages": [
            {"role": "user", "content": "do something"},
            # The honest error notice the supervisor appended on failure.
            {
                "role": "assistant",
                "content": "⚠️ I lost the connection to the model mid-turn.",
            },
        ],
        "iteration": 4,
        "max_iterations": 15,
        "turn_failed": True,
        "error_message": "⚠️ I lost the connection to the model mid-turn.",
    }

    out = await gb.respond_node(state, llm=_LLM())

    # Synthesis was skipped — no fabricated answer, no extra LLM call.
    assert call_count["n"] == 0
    finals = [
        m["content"]
        for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    assert "FABRICATED ANSWER" not in finals
    assert any("lost the connection" in t for t in finals)


@pytest.mark.asyncio
async def test_respond_node_synthesizes_when_turn_not_failed(monkeypatch):
    """Normal max-iter behavior is preserved when turn_failed is not set."""
    from kazma_core.agent import graph_builder as gb

    class _LLM:
        async def chat(self, messages, tools=None):
            assert tools is None
            return type("_Resp", (), {"content": "Here is the synthesized report."})()

    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )

    state = {
        "messages": [
            {"role": "user", "content": "investigate"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "some result"},
        ],
        "iteration": 15,
        "max_iterations": 15,
        # turn_failed intentionally absent / False
    }

    out = await gb.respond_node(state, llm=_LLM())
    finals = [
        m["content"]
        for m in out["messages"]
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    assert any("synthesized report" in t for t in finals)


# ──────────────────────────────────────────────────────────────────────
# Bug 2: analyze_image fails cleanly when no vision model is configured
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_image_clear_error_when_no_vision_model(monkeypatch, tmp_path):
    """With a text-only active model and no vision model configured,
    analyze_image returns an actionable error BEFORE any API call."""
    from kazma_core.tools import vision_analyze as va

    # Make a tiny valid PNG so image loading succeeds (we want to reach the
    # provider-selection step, not fail on file read).
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x5c\xcd\xff\x69"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img = tmp_path / "t.png"
    img.write_bytes(png)

    import sys
    fw_mod = sys.modules["kazma_core.tools.file_write"]
    monkeypatch.setattr(fw_mod, "_is_within_workspace", lambda path, ws: True)
    monkeypatch.setattr(
        va, "_get_llm_provider",
        lambda: (None, "deepseek-v4-pro", "no-vision-model"),
    )

    result = await va.analyze_image(str(img), "what is this?")

    assert isinstance(result, str)
    assert "Error" in result
    assert "vision-capable" in result
    assert "deepseek-v4-pro" in result  # names the offending active model
    assert "Settings" in result  # actionable guidance
