"""The prompt rules Kazma documents are in the prompt a shipped install uses
(2026-11, FA-121).

``agent_runner._default_system_prompt`` held the document-generation rule,
the iteration-efficiency rule and a "stop all tool calls on any tool error"
rule; the shipped ``kazma.yaml`` sets its own prompt, which wins, so none of
them reached the model on any install. The rules still wanted are now in
both; the tool-error rule is gone (a refusal is an ordinary result the turn
carries on past, AGENTS 20 and 30G).

Read from the shipped file; the shipped prompt before this change (no
document or efficiency rule) is the negative control.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kazma_core import agent_runner

ROOT = Path(__file__).resolve().parents[1]


def _shipped_prompt() -> str:
    return yaml.safe_load((ROOT / "kazma.yaml").read_text(encoding="utf-8"))["system_prompt"]


def test_every_rule_is_in_the_shipped_prompt():
    prompt = _shipped_prompt()
    missing = [rule for rule in agent_runner._PROMPT_RULES if rule not in prompt]
    assert not missing, missing


def test_every_rule_is_in_the_default_prompt():
    default = agent_runner.KazmaAgent._default_system_prompt(None)
    assert all(rule in default for rule in agent_runner._PROMPT_RULES)


def test_the_tool_error_rule_is_gone_from_both():
    for prompt in (_shipped_prompt(), agent_runner.KazmaAgent._default_system_prompt(None)):
        assert "CRITICAL SYSTEM RULE" not in prompt and "stop issuing further tool calls" not in prompt


def test_negative_control_the_old_shipped_prompt_lacked_them():
    old = ("TOOL SELECTION: Prefer native tools over shell_exec. ... CRITICAL LANGUAGE RULE: Match "
           "the language of the user's latest message only.")
    assert [r for r in agent_runner._PROMPT_RULES if r not in old] == [
        "DOCUMENT GENERATION RULE:", "ITERATION EFFICIENCY:"]
