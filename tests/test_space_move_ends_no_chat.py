"""A space brought in, and taken back (Spaces S4-06), deletes no chat (the fifth
review of the chat move, M4).

A deleted chat's thread never runs again (``threads.end_thread``), and removing
its record marks it. A take-back removes the records it wrote, so it marked
them too: the same bundle brought in again landed with every chat refused until
a restart, and so was a chat made here meanwhile that the import had left as it
was. A take-back releases the records (``threads.release_thread``). A gate: only
a chat's delete forgets a thread.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.spaces import scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests.test_space_bundle_round_trip import _bring_in, _files_fail, target  # noqa: F401  (a fixture)
from tests.test_space_move_fourth_review import _meanwhile, _refused_code
from tests.test_space_move_third_review import _said, _save, chats  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]


def _runs(thread: str, space: str, account: str) -> bool:
    with scope.bind_scope(space, account):
        return threads.scope_for_turn(thread).scope.space_id == space


def _taken_back(bundle: Path, person: str, monkeypatch: Any) -> None:
    """An import that fails at its files, and is taken back."""
    with monkeypatch.context() as patch, _files_fail(patch), pytest.raises(OSError):
        _bring_in(bundle, person)


def test_a_space_brought_in_again_after_a_take_back_has_its_chats(chats, target, monkeypatch) -> None:  # noqa: F811
    _taken_back(chats["bundle"], target["person"], monkeypatch)
    assert threads.record_of(chats["thread"]) is None and not threads.thread_ended(chats["thread"])
    _bring_in(chats["bundle"], target["person"])
    assert _runs(chats["thread"], chats["space"], target["person"])


def test_control_a_take_back_that_forgets_them_ends_them(chats, target, monkeypatch) -> None:  # noqa: F811
    real = threads.release_thread

    def forgets(thread: str) -> bool:
        threads.end_thread(thread)
        return real(thread)

    monkeypatch.setattr(threads, "release_thread", forgets)
    _taken_back(chats["bundle"], target["person"], monkeypatch)
    _bring_in(chats["bundle"], target["person"])
    with pytest.raises(threads.ThreadEnded):
        _runs(chats["thread"], chats["space"], target["person"])


def test_a_chat_made_here_meanwhile_runs(chats, target, monkeypatch) -> None:  # noqa: F811
    """The refused import leaves it as it is: its words, and its turns."""
    owners = Path(paths.checkpoints_db())
    _meanwhile(monkeypatch, lambda: _save(owners, chats["thread"], "the owner's words"))
    assert _refused_code(chats["bundle"], target["person"]) == "id_collides"
    assert _said(owners, chats["thread"]) == "the owner's words"
    assert _runs(chats["thread"], OWNER_SPACE_ID, OWNER_ACCOUNT_ID)


def test_a_chat_deleted_here_stays_ended(chats, target, monkeypatch) -> None:  # noqa: F811
    """A guard: a take-back takes no mark away either."""
    threads.end_thread(chats["thread"])
    _taken_back(chats["bundle"], target["person"], monkeypatch)
    assert threads.thread_ended(chats["thread"])


# ── Gate: only a chat's delete forgets a thread ───────────────────────────

#: The chat's delete (its route, the move's), and the Dashboard's, which asks
#: whether a chat was deleted with it.
_DELETES = {"kazma-core/kazma_core/spaces/threads.py", "kazma-ui/kazma_ui/chat_delete.py",
            "kazma-ui/kazma_ui/dashboard.py"}


def _forgets_a_thread(source: str) -> bool:
    """Whether *source* reaches ``spaces.threads.forget_thread``."""
    tree = ast.parse(source)
    if any(isinstance(n, ast.ImportFrom) and (n.module or "").endswith("spaces.threads")
           and any(a.name == "forget_thread" for a in n.names) for n in ast.walk(tree)):
        return True
    return any(isinstance(n, ast.Attribute) and n.attr == "forget_thread" and isinstance(n.value, ast.Name)
               and n.value.id in ("threads", "_threads", "thread_store") for n in ast.walk(tree))


def test_only_a_chats_delete_forgets_a_thread() -> None:
    found = set()
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills"):
        for path in (REPO / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            if "forget_thread" in source and _forgets_a_thread(source):
                found.add(path.relative_to(REPO).as_posix())
    assert found <= _DELETES, "threads.release_thread, unless a chat is deleted"
    assert "kazma-ui/kazma_ui/chat_delete.py" in found, "the gate reads the product"


def test_control_a_take_back_that_forgets_is_caught() -> None:
    assert _forgets_a_thread("from kazma_core.spaces import threads\nthreads.forget_thread('t')\n")
    assert _forgets_a_thread("from kazma_core.spaces.threads import forget_thread\n")
    assert not _forgets_a_thread("from kazma_core.agent.effect_journal import x\njournal.forget_thread('t')\n")
