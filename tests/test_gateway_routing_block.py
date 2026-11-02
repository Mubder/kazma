"""The graph state's one routing block holds its declared keys only (2026-11, FA-151).

AGENTS.md §2: the LangGraph state never holds a platform's chat, user or
message id. §16B and §30E2 need one address in it on purpose: the
``_gateway`` block's ``delivery_target`` (``telegram:<chat>``), where a
reminder booked in the turn rings and an expired approval is answered.
Nothing else rides along: every place product code builds the block names
exactly the declared keys, ``SupervisorState`` has no platform id of its
own, and a restore or fork drops any platform id an older checkpoint left in
the block.

A planted ``chat_id`` in a routing block is the negative control.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import get_type_hints

ROOT = Path(__file__).resolve().parents[1]
DECLARED = frozenset({"thread_id", "display_name", "platform", "delivery_target"})
PLATFORM_IDS = frozenset({"chat_id", "user_id", "message_id"})


def _block_literals(source: str) -> list[tuple[int, frozenset[str]]]:
    """Every dict literal written as a ``_gateway`` block: ``x["_gateway"] =
    {...}`` or ``{"_gateway": {...}}``, with its keys."""
    found: list[tuple[int, frozenset[str]]] = []

    def keys_of(node: ast.AST) -> frozenset[str] | None:
        if not isinstance(node, ast.Dict):
            return None
        return frozenset(k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "_gateway"
                ):
                    keys = keys_of(node.value)
                    if keys is not None:
                        found.append((node.lineno, keys))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "_gateway":
                    keys = keys_of(value)
                    if keys is not None:
                        found.append((node.lineno, keys))
        elif isinstance(node, ast.FunctionDef) and node.name == "web_gateway_block":
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and ret.value is not None:
                    keys = keys_of(ret.value)
                    if keys is not None:
                        found.append((ret.lineno, keys))
    return found


def _problems(source: str, label: str) -> list[str]:
    return [
        f"{label}:{line} builds a _gateway block with {sorted(keys - DECLARED)}"
        for line, keys in _block_literals(source)
        if keys - DECLARED
    ]


def _product_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "kazma-*/kazma_*/**.py", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(set(listed))


def test_every_routing_block_names_only_the_declared_keys():
    seen = 0
    problems: list[str] = []
    for rel in _product_files():
        source = (ROOT / rel).read_text(encoding="utf-8")
        if "_gateway" not in source:
            continue
        seen += len(_block_literals(source))
        problems += _problems(source, rel)
    assert seen >= 2, "the gateway store and web_gateway_block were not found"
    assert not problems, "\n".join(problems)


def test_negative_control_a_planted_chat_id_is_found():
    planted = 'state["_gateway"] = {"thread_id": t, "chat_id": msg.chat_id}\n'
    assert _problems(planted, "planted")
    assert _problems('x = {"_gateway": {"user_id": 1}}\n', "planted")


def test_the_web_block_is_the_declared_keys(monkeypatch):
    from kazma_core.tools import send_message

    monkeypatch.setattr(send_message, "resolve_delivery_target", lambda: "telegram:1")
    assert frozenset(send_message.web_gateway_block("t-1")) == DECLARED


def test_supervisor_state_has_no_platform_id_of_its_own():
    from kazma_core.agent.state import SupervisorState

    assert not PLATFORM_IDS & frozenset(get_type_hints(SupervisorState))


def test_a_restore_or_fork_drops_platform_ids_an_older_checkpoint_left():
    from kazma_core.time_travel import checkpoint_payload_from_snapshot

    old = {
        "messages": [],
        "_gateway": {"thread_id": "a", "platform": "telegram", "chat_id": "42", "user_id": "7",
                     "delivery_target": "telegram:42"},
    }
    block = checkpoint_payload_from_snapshot(old, "b")["_gateway"]
    assert not PLATFORM_IDS & set(block)
    assert block["thread_id"] == "b"
