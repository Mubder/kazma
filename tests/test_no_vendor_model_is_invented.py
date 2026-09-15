"""No code path may invent a vendor model when nothing is configured.

WHERE THIS CAME FROM

A stress run asked Kazma to state its own model. It answered:

    agent.model = "deepseek-flash"  and  llm.model = "gpt-4o-mini"

Two configured models, disagreeing. The operator's live Postgres store does
hold `llm.model = "gpt-4o-mini"` and `models.default = "gpt-4o-mini"`, and the
shipped `kazma.yaml` ships both plus `llm.base_url: https://api.openai.com/v1`
-- on an install whose OpenAI key is empty and which actually runs DeepSeek.

The stale pin was survivable only because a later fix (abe1099d) routes a
keyless OpenAI pin to a ready provider. That silenced the 401 and left the
wrong value in place, so every self-report about the running model was wrong.

Underneath it were ten hardcoded `gpt-4o-mini` literals acting as universal
last resorts -- `or "gpt-4o-mini"` in the registry, in the agent config, in
`LLMConfig`, in the UI. A bare fallback like that is not a default; it is a
guess that an unconfigured install should dial OpenAI.

WHAT IS LEGITIMATE, AND WHAT IS NOT

Legitimate: a map saying "openai's small chat model is gpt-4o-mini". That is
data about a vendor. It lived in two copies that had already drifted --
`model_registry`'s inline dict knew 5 providers, `model_switch`'s
`_FALLBACK_CHAT_MODEL` knew 8 -- so the same question got different answers
depending on who asked. One copy now, in `providers.py`.

Not legitimate: reaching for that map's OpenAI entry when the provider in hand
is Anthropic, or when there is no provider at all. Then the answer is "" and
the caller must say so.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SOURCES = [
    _ROOT / "kazma-core" / "kazma_core",
    _ROOT / "kazma-ui" / "kazma_ui",
    _ROOT / "kazma-gateway" / "kazma_gateway",
]
#: The one file allowed to name vendor CHAT models: the map itself.
_MAP_FILE = _ROOT / "kazma-core" / "kazma_core" / "providers.py"

#: Speech-to-text is a separate namespace with its own configuration path
#: (`_get_configured_stt_model`). `openai/whisper-large-v3` there is the name
#: NVIDIA's OWN API uses for that model, sitting beside NVIDIA-specific URL
#: construction -- provider data, not an invented OpenAI default. Exempted
#: deliberately and narrowly; if STT grows a second such literal it wants its
#: own map, not a wider hole in this one.
_EXEMPT_FILES = {"stt.py"}


def _python_files():
    for root in _SOURCES:
        if not root.is_dir():
            continue
        for f in root.rglob("*.py"):
            if "_tests" in f.parts or "tests" in f.parts:
                continue
            yield f


class TestTheMapIsTheOnlyPlace:
    def test_no_module_uses_a_vendor_model_as_a_bare_fallback(self) -> None:
        """`x or "gpt-4o-mini"` and friends, found by PARSING not grepping.

        Grepping would match the comments explaining why this is wrong -- a
        mistake already made twice in this repo.
        """
        offenders: list[str] = []
        vendor = re.compile(
            r"^(gpt-|claude-|gemini-|deepseek-|llama-|grok-|mistral-|openai/)",
            re.I,
        )
        for f in _python_files():
            if f == _MAP_FILE or f.name in _EXEMPT_FILES:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                # `something or "gpt-4o-mini"`
                if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                    for v in node.values:
                        if (
                            isinstance(v, ast.Constant)
                            and isinstance(v.value, str)
                            and vendor.match(v.value)
                        ):
                            offenders.append(f"{f.name}:{node.lineno} or {v.value!r}")
                # `d.get("k", "gpt-4o-mini")`
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "get" and len(node.args) == 2:
                        d = node.args[1]
                        if (
                            isinstance(d, ast.Constant)
                            and isinstance(d.value, str)
                            and vendor.match(d.value)
                        ):
                            offenders.append(
                                f"{f.name}:{node.lineno} .get(..., {d.value!r})"
                            )
        assert not offenders, (
            "a vendor model is being invented as a fallback; ask "
            "providers.default_model_for(provider) instead:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_map_is_not_duplicated(self) -> None:
        """model_switch and model_registry must read it, not re-declare it."""
        switch = (
            _ROOT / "kazma-core" / "kazma_core" / "runtime" / "model_switch.py"
        ).read_text(encoding="utf-8")
        registry = (
            _ROOT / "kazma-core" / "kazma_core" / "model_registry.py"
        ).read_text(encoding="utf-8")
        assert "DEFAULT_MODEL_FOR" in switch, "model_switch re-declared the map"
        assert "default_model_for" in registry, "model_registry re-declared the map"
        # The literal that used to head both copies.
        assert '"deepseek": "deepseek-chat"' not in switch
        assert '"deepseek": "deepseek-chat"' not in registry


class TestTheMapAnswersHonestly:
    def test_it_knows_the_providers_both_copies_knew(self) -> None:
        """The registry copy was missing three the switch copy had."""
        from kazma_core.providers import default_model_for

        for p in ("deepseek", "openai", "anthropic", "google", "groq",
                  "xai", "openrouter", "mistral"):
            assert default_model_for(p), p

    def test_an_unknown_provider_gets_empty_not_openai(self) -> None:
        from kazma_core.providers import default_model_for

        assert default_model_for("some-local-thing") == ""
        assert default_model_for("") == ""

    def test_it_is_case_and_whitespace_insensitive(self) -> None:
        from kazma_core.providers import default_model_for

        assert default_model_for("  OpenAI ") == default_model_for("openai")


class TestTheDefaultsAreEmpty:
    def test_llm_config_names_no_model(self) -> None:
        from kazma_core.llm_provider import LLMConfig

        assert LLMConfig().model == ""
        assert LLMConfig.from_dict({}).model == ""

    def test_agent_config_names_no_model(self) -> None:
        import dataclasses

        from kazma_core.agent_runner import AgentConfig

        field = {f.name: f for f in dataclasses.fields(AgentConfig)}["default_model"]
        assert field.default == "", field.default
