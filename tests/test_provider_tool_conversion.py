"""Regression coverage for OpenAI→native tool-traffic conversion (2026-08-26 audit).

The Kazma agent loop emits standard OpenAI tool traffic (one ``role:"tool"``
dict per result + assistant messages carrying ``tool_calls``). Anthropic
Messages and Bedrock Converse require their own block shapes and strict
alternation — before these fixes both native providers 400'd the first
tool-using turn (Anthropic: invalid ``tool`` role / dropped ``tool_calls``;
Bedrock: one user turn per toolResult), and ``normalize_provider_url``
appended ``/v1`` to Google's OpenAI-compat endpoints (404 on every call).
"""

from __future__ import annotations

from kazma_core.anthropic_llm import AnthropicProvider
from kazma_core.bedrock_llm import BedrockProvider
from kazma_core.url_utils import normalize_provider_url


def _tool_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class TestAnthropicConversion:
    def test_tool_message_becomes_user_tool_result(self) -> None:
        out = AnthropicProvider._convert_message(_tool_msg("tc1", "42"))
        assert out["role"] == "user"
        assert out["content"][0]["type"] == "tool_result"
        assert out["content"][0]["tool_use_id"] == "tc1"
        assert out["content"][0]["content"] == "42"

    def test_assistant_tool_calls_become_tool_use_blocks(self) -> None:
        out = AnthropicProvider._convert_message({
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [{
                "id": "tc1",
                "type": "function",
                "function": {"name": "file_read", "arguments": '{"path": "a.txt"}'},
            }],
        })
        assert out["role"] == "assistant"
        kinds = [b["type"] for b in out["content"]]
        assert kinds == ["text", "tool_use"]
        tu = out["content"][1]
        assert tu["id"] == "tc1"
        assert tu["name"] == "file_read"
        assert tu["input"] == {"path": "a.txt"}  # parsed dict, not a raw string

    def test_merge_consecutive_coalesces_parallel_tool_results(self) -> None:
        msgs = [
            {"role": "user", "content": "check these"},
            {
                "role": "assistant",
                "content": "on it",
                "tool_calls": [
                    {"id": "a", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}},
                    {"id": "b", "type": "function",
                     "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            AnthropicProvider._convert_message(_tool_msg("a", "1")),
            AnthropicProvider._convert_message(_tool_msg("b", "2")),
        ]
        merged = AnthropicProvider._merge_consecutive(msgs)
        # Strict alternation: the N tool-result user turns coalesce into ONE.
        assert [m["role"] for m in merged] == ["user", "assistant", "user"]
        assert [b["tool_use_id"] for b in merged[2]["content"]] == ["a", "b"]

    def test_merge_consecutive_joins_string_user_messages(self) -> None:
        merged = AnthropicProvider._merge_consecutive([
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "there"},
        ])
        assert len(merged) == 1
        assert merged[0]["content"] == "hi\n\nthere"


class TestBedrockConversion:
    def test_tool_results_merge_into_single_user_turn(self) -> None:
        system, convo = BedrockProvider._build_messages([
            {"role": "user", "content": "check these"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "type": "function",
                     "function": {"name": "f", "arguments": {}}},
                    {"id": "b", "type": "function",
                     "function": {"name": "g", "arguments": {}}},
                ],
            },
            _tool_msg("a", "1"),
            _tool_msg("b", "2"),
        ])
        assert system == []
        # Converse requires alternation and all toolResults in ONE user turn.
        assert [m["role"] for m in convo] == ["user", "assistant", "user"]
        tool_ids = [b["toolResult"]["toolUseId"] for b in convo[2]["content"]]
        assert tool_ids == ["a", "b"]

    def test_developer_role_routes_to_system(self) -> None:
        system, convo = BedrockProvider._build_messages([
            {"role": "developer", "content": "mid-stream note"},
            {"role": "user", "content": "hi"},
        ])
        assert system == [{"text": "mid-stream note"}]
        assert [m["role"] for m in convo] == ["user"]


class TestGeminiUrlNormalization:
    def test_google_openai_compat_urls_keep_their_own_versioning(self) -> None:
        for url in (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://us-central1-aiplatform.googleapis.com/endpoints/openapi",
        ):
            assert normalize_provider_url(url) == url, url

    def test_generic_providers_still_get_v1(self) -> None:
        assert (
            normalize_provider_url("https://api.openai.com")
            == "https://api.openai.com/v1"
        )
