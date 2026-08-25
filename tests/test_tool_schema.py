"""Strict JSON Schema + structured-output helpers (quality item 2)."""

from __future__ import annotations

from kazma_core.agent.tool_schema import (
    _generate_schema,
    _python_type_to_json_schema,
    apply_openai_strict_tools,
    filter_tool_arguments,
    json_schema_response_format,
    strict_tools_enabled,
    to_openai_strict_schema,
)
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.llm_provider import LLMConfig, LLMProvider


class TestGenerateSchemaClosed:
    def test_root_forbids_additional_properties(self) -> None:
        async def fn(a: str, b: int = 1) -> str:
            return a

        schema = _generate_schema(fn)
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["a"]
        assert schema["properties"]["b"]["default"] == 1

    def test_typed_dict_keeps_value_schema(self) -> None:
        frag = _python_type_to_json_schema(dict[str, int])
        assert frag["type"] == "object"
        assert frag["additionalProperties"] == {"type": "integer"}
        assert "properties" not in frag

    def test_bare_dict_is_open_object(self) -> None:
        frag = _python_type_to_json_schema(dict)
        assert frag == {"type": "object"}

    def test_union_anyof(self) -> None:
        frag = _python_type_to_json_schema(str | int)
        assert "anyOf" in frag
        types = {item["type"] for item in frag["anyOf"]}
        assert types == {"string", "integer"}


class TestOpenAIStrict:
    def test_optionals_become_nullable_and_required(self) -> None:
        async def fn(query: str, limit: int = 10) -> str:
            return query

        converted, ok = to_openai_strict_schema(_generate_schema(fn))
        assert ok is True
        assert converted["additionalProperties"] is False
        assert set(converted["required"]) == {"query", "limit"}
        limit = converted["properties"]["limit"]
        assert "anyOf" in limit
        assert {"type": "null"} in limit["anyOf"]
        assert "default" not in limit

    def test_open_dict_param_is_not_strict_compatible(self) -> None:
        async def fn(env: dict[str, str] | None = None) -> str:
            return ""

        converted, ok = to_openai_strict_schema(_generate_schema(fn))
        assert ok is False
        # conversion clone must not be used when incompatible
        assert converted["properties"]["env"]["type"] == "object"

    def test_apply_stamps_strict_only_when_compatible(self) -> None:
        async def closed(path: str) -> str:
            return path

        async def open_dict(meta: dict[str, str]) -> str:
            return ""

        defs = [
            {
                "type": "function",
                "function": {
                    "name": "closed",
                    "description": "c",
                    "parameters": _generate_schema(closed),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_dict",
                    "description": "o",
                    "parameters": _generate_schema(open_dict),
                },
            },
        ]
        apply_openai_strict_tools(defs)
        assert defs[0]["function"]["strict"] is True
        assert "strict" not in defs[1]["function"]


class TestFilterArgs:
    def test_drops_invented_keys_keeps_hitl_ids(self) -> None:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        out = filter_tool_arguments(
            {"path": "a.py", "invented": 1, "task_id": "t1"},
            schema,
        )
        assert out == {"path": "a.py", "task_id": "t1"}

    def test_null_optional_uses_python_default(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["path"],
        }
        out = filter_tool_arguments({"path": "a.py", "limit": None}, schema)
        assert out == {"path": "a.py"}


class TestStrictEnvAndRegistry:
    def test_strict_tools_enabled(self, monkeypatch) -> None:
        monkeypatch.delenv("KAZMA_STRICT_TOOLS", raising=False)
        assert strict_tools_enabled() is False
        monkeypatch.setenv("KAZMA_STRICT_TOOLS", "1")
        assert strict_tools_enabled() is True

    def test_get_tool_definitions_stamps_strict(self, monkeypatch) -> None:
        monkeypatch.setenv("KAZMA_STRICT_TOOLS", "1")
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="closed tool")
        async def echo(text: str, loud: bool = False) -> str:
            return text

        defs = registry.get_tool_definitions()
        fn = defs[0]["function"]
        assert fn["strict"] is True
        assert set(fn["parameters"]["required"]) == {"text", "loud"}
        assert fn["parameters"]["additionalProperties"] is False

    def test_get_tool_definitions_default_has_no_strict(self, monkeypatch) -> None:
        monkeypatch.delenv("KAZMA_STRICT_TOOLS", raising=False)
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="closed tool")
        async def echo(text: str) -> str:
            return text

        fn = registry.get_tool_definitions()[0]["function"]
        assert "strict" not in fn
        assert fn["parameters"]["additionalProperties"] is False
        assert fn["parameters"]["required"] == ["text"]


class TestResponseFormat:
    def test_json_schema_helper(self) -> None:
        body = json_schema_response_format(
            "answer",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        )
        assert body["type"] == "json_schema"
        assert body["json_schema"]["name"] == "answer"
        assert body["json_schema"]["strict"] is True

    def test_chat_payload_includes_response_format(self) -> None:
        provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="k"))
        rf = {"type": "json_object"}
        payload = provider._chat_payload(
            [{"role": "user", "content": "hi"}],
            None,
            None,
            None,
            None,
            rf,
        )
        assert payload["response_format"] == rf
        assert "tools" not in payload

    def test_chat_payload_omits_response_format_when_none(self) -> None:
        provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="k"))
        payload = provider._chat_payload(
            [{"role": "user", "content": "hi"}],
            None,
            None,
            None,
            None,
        )
        assert "response_format" not in payload
