"""Every ``/swarm config`` word that writes is admin-only (FA-153).

The 2026-11 functional audit: the chat-app admin gate
(``slash_commands.changes_global_config``) listed ``/swarm config``'s words by
hand -- ``group`` and ``clear`` -- and missed ``disable``, which writes the
same global ``swarm.output_target``: any allowed sender could turn the
swarm's output routing off for every surface.

The words are read from the handler: each ``action == "<word>"`` branch that
writes the settings store must be gated; showing the routing stays open.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kazma_gateway.slash_commands import changes_global_config

REPO = Path(__file__).resolve().parents[1]
HANDLER = REPO / "kazma-gateway/kazma_gateway/agent_handler/commands.py"
WRITES = {"set", "delete", "batch_set", "atomic_update"}


def _writing_words() -> set[str]:
    """The ``action == "<word>"`` branches of the /swarm config handler that
    write the settings store."""
    tree = ast.parse(HANDLER.read_text(encoding="utf-8"))
    handler = next(
        f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(n, ast.Constant) and n.value == "swarm.output_target" for n in ast.walk(f))
    )
    words = set()
    for node in ast.walk(handler):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "action"
                and isinstance(test.comparators[0], ast.Constant)):
            continue
        writes = any(isinstance(n, ast.Attribute) and n.attr in WRITES
                     and isinstance(n.value, ast.Name) and n.value.id == "cs"
                     for stmt in node.body for n in ast.walk(stmt))
        if writes:
            words.add(test.comparators[0].value)
    return words


def test_every_writing_word_is_admin_only():
    words = _writing_words()
    assert {"group", "disable", "clear"} <= words
    for word in sorted(words):
        assert changes_global_config(f"/swarm config {word} -1001234"), word
    assert not changes_global_config("/swarm config")  # showing stays open
    assert not changes_global_config("/swarm status")


def test_the_hand_listed_words_missed_disable():
    """Negative control: the rule before the fix."""
    def old(text: str) -> bool:
        parts = text.split()
        return parts[1] == "config" and (parts[2] if len(parts) > 2 else "") in ("group", "clear")

    assert not old("/swarm config disable")
    assert "disable" in _writing_words()
