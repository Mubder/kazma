"""A checkpointer Kazma wraps answers every async call LangGraph and Kazma make.

``CheckpointManager`` (the server's per-thread locks over the saver) is a
``BaseCheckpointSaver`` and delegated only what its callers used the day it
was written. ``alist`` was not among them: the base class raises
NotImplementedError, and the start-up pass that continues turns a restart cut
off (``kazma_ui.turn_resume``, resilience plan C1), handed this manager by
the app, failed on every live boot from the day it shipped until 2026-11-06.
Its tests used the bare saver.

Every product subclass of ``BaseCheckpointSaver`` implements each async
method in ``REQUIRED``: the ones a graph run, a state read, a history or a
listing, and a chat delete call. A sync-only or copy/prune method nobody
calls is not required.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: What a running graph (aput, aput_writes, aget_tuple), a state history or
#: a listing (alist: aget_state_history, turn_resume, Time Travel) and a
#: chat delete (adelete_thread) call on a checkpointer.
REQUIRED = {"aget_tuple", "aput", "aput_writes", "alist", "adelete_thread"}


def _wrappers(source: str) -> dict[str, set[str]]:
    """Each class deriving from BaseCheckpointSaver, with the methods it defines."""
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == "BaseCheckpointSaver")
                or (isinstance(b, ast.Attribute) and b.attr == "BaseCheckpointSaver") for b in node.bases):
            found[node.name] = {n.name for n in node.body
                                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return found


def _missing(source: str) -> dict[str, list[str]]:
    return {name: sorted(REQUIRED - defined) for name, defined in _wrappers(source).items()
            if REQUIRED - defined}


def _product_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                          "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                         capture_output=True, text=True, check=True, cwd=REPO).stdout.split()
    return [REPO / f for f in out]


def test_every_checkpointer_wrapper_answers_every_async_call():
    seen, missing = 0, {}
    for path in _product_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "BaseCheckpointSaver" not in text:
            continue
        seen += len(_wrappers(text))
        for name, methods in _missing(text).items():
            missing[f"{path.relative_to(REPO).as_posix()}::{name}"] = methods
    assert seen >= 1, "the gate found no wrapper: the CheckpointManager is not where it looks"
    assert missing == {}, missing


def test_negative_control_a_wrapper_without_a_listing_is_caught():
    planted = (
        "from langgraph.checkpoint.base import BaseCheckpointSaver\n"
        "class Locked(BaseCheckpointSaver):\n"
        "    async def aget_tuple(self, config): ...\n"
        "    async def aput(self, *a): ...\n"
        "    async def aput_writes(self, *a): ...\n"
        "    async def adelete_thread(self, thread_id): ...\n"
    )
    assert _missing(planted) == {"Locked": ["alist"]}
