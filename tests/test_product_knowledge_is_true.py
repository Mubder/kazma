"""What the model is told about Kazma matches the code (FA-118).

The 2026-11 functional audit: the product knowledge in every system prompt
said checkpoints are SQLite (Postgres installs keep them in Postgres), called
Chroma the memory (memory is ``memory_state.db``; Chroma holds Knowledge
vectors), offered hub skills (hub install is not built), put the Soul in
``agent_evolution.json`` (it is in the settings store), listed four of the
five always-ask tools (``request_path_access`` missing), and gave a
Telegram > Discord > Slack approval order (every chat app is asked at once;
the first decision wins). The model states these to the owner as facts.

Every backticked tool-like name in the block is a registered tool (or a
declared non-tool name), the always-ask sentence names exactly
``ALWAYS_HITL_TOOLS``, and the six stale claims are gone. The old block's
lines are the negative control.
"""

from __future__ import annotations

import re

from kazma_core.agent.tool_builtins import register_builtin_tools
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.product_knowledge import _always_ask_sentence, build_product_knowledge
from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

#: Backticked snake_case names in the block that are not tools, and what they are.
NOT_TOOLS = {
    "code_exec": "named as a module that is not a tool",
    "set_active_model": "the registry function a model switch goes through",
    "approval_timeout_seconds": "a setting",
    "proposal_id": "an argument",
    "memory_state": "part of memory_state.db",
    "workspace_bound": "an MCP server's configuration flag",
}
_STALE = (
    "durable SQLite checkpoints",
    "vector (Chroma/local or remote embeddings)",
    "hub skills",
    "agent_evolution.json",
    "Telegram>Discord>Slack",
)
_OLD_ALWAYS_ASK = ("never for an X write (`x_post`, `x_schedule_post`, `x_delete_post`, "
                   "`x_cancel_scheduled_post`): those take a one-time approval only")


def _registered() -> set[str]:
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return set(registry._tools)


def tool_like_names(text: str) -> set[str]:
    """Backticked names shaped like a tool: snake_case with an underscore."""
    return {m for m in re.findall(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?:\([^`]*\))?`", text)}


def always_asked_names(sentence: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", sentence))


def test_every_tool_the_block_names_is_registered():
    text = build_product_knowledge()
    registered = _registered()
    unknown = {name for name in tool_like_names(text) if name not in registered and name not in NOT_TOOLS}
    assert not unknown, f"product knowledge names tools Kazma does not have: {sorted(unknown)}"


def test_the_always_ask_sentence_is_the_always_ask_list():
    text = build_product_knowledge()
    sentence = _always_ask_sentence()
    assert sentence in text
    assert always_asked_names(sentence) == set(ALWAYS_HITL_TOOLS)


def test_the_stale_claims_are_gone():
    text = build_product_knowledge()
    for claim in _STALE:
        assert claim not in text, claim


def test_the_old_lines_fail_both_rules():
    """Negative control: the always-ask line before FA-118 left
    ``request_path_access`` out, and the stale claims match."""
    assert always_asked_names(_OLD_ALWAYS_ASK) != set(ALWAYS_HITL_TOOLS)
    assert "request_path_access" not in always_asked_names(_OLD_ALWAYS_ASK)
    old_line = "- **Memory** — vector (Chroma/local or remote embeddings) + FTS; auto-store + per-turn retrieval when enabled."
    assert any(claim in old_line for claim in _STALE)
