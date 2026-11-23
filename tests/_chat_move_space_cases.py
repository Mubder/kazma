"""The matrix cases of Spaces S2-12: moving one's own chat.

Every case runs with Spaces enforced, as person A against person B. A gets a
personal space of their own beside the matrix's two shared spaces; each
person has a chat of their own space, holding a mark. A moves A's own chat;
B's chat, and the line B's space keeps, never move or show for A: named in
A's own space it is no chat, and naming B's space is refused by the
membership check every request passes. Memory's part of the move is its own
file's (``tests/test_chat_move_memory.py``): here it is recorded only.

Nothing here builds the app or decides the vault while pytest imports it.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import closing, contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import chat_moves, identity, scope, threads
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S2-12"
OWN_A = "matrix-a-own"


def _chat(space: str, account: str, mark: str) -> tuple[str, str]:
    """A chat *account* opened in *space*: (session id, thread id)."""
    from kazma_ui.session_manager import get_session_manager

    session_id, thread_id = f"s{secrets.token_hex(6)}", f"t{secrets.token_hex(6)}"
    with scope.bind_scope(space, account):
        store = get_session_manager()
        session = store.get_or_create(session_id, thread_id=thread_id)
        session.add_message("user", f"question {mark}", author=account)
        session.add_message("assistant", f"answer {mark}")
        session.title = f"title {mark}"
        store.put(session)
    threads.record_thread(thread_id, space, account)
    return session_id, thread_id


@contextmanager
def moves(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; A's personal space; a chat of each person's space;
    the chat move router behind the real tenant middleware."""
    from kazma_ui.chat_move import create_chat_move_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("chat-move")

    def personal(reg: dict[str, Any]) -> None:
        reg["spaces"][OWN_A] = {"id": OWN_A, "name": "", "kind": "sandboxed",
                                "personal_of": matrix.account_a, "created_by": matrix.account_a}
        reg["memberships"].append({"account": matrix.account_a, "space": OWN_A, "role": "owner"})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(threads, "_spaces", {})
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        followed: list[dict[str, Any]] = []
        patch.setattr("kazma_ui.chat_move._memory_follows",
                      lambda entry: followed.append(dict(entry)) or {"ok": True})
        identity.update_registry(personal)
        with closing(chat_moves._connection()) as conn:
            conn.execute("DELETE FROM chat_moves_done")
        a_chat = _chat(OWN_A, matrix.account_a, marks.a)
        b_chat = _chat(Matrix.SPACE_B, matrix.account_b, marks.b)
        chat_moves.record_taken_out(space_id=Matrix.SPACE_B, thread_id=b_chat[1], title=f"line {marks.b}",
                                    by=matrix.account_b)
        app = FastAPI()
        Matrix.role_checked(app)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_chat_move_router())
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, marks=marks, a_chat=a_chat, b_chat=b_chat, followed=followed)


def _as(who: str, space: str) -> dict[str, str]:
    return {"x-matrix-person": who, space_binding.SPACE_HEADER: space}


def _b_chat_stays(matrix: Matrix, w: SimpleNamespace) -> None:
    from kazma_ui.session_manager import get_session_manager

    session, thread = w.b_chat
    assert threads.record_of(thread).space_id == Matrix.SPACE_B
    with scope.bind_scope(Matrix.SPACE_B, matrix.account_b):
        assert get_session_manager().get(session) is not None


@case("GET /api/chat/sessions/{session_id}/move", ITEM)
def move_preview(matrix: Matrix) -> None:
    with moves(matrix) as w:
        mine = w.client.get(f"/api/chat/sessions/{w.a_chat[0]}/move", headers=_as("a", OWN_A))
        assert mine.status_code == 200 and mine.json()["turns"] == 1, mine.text
        theirs = w.client.get(f"/api/chat/sessions/{w.b_chat[0]}/move", headers=_as("a", OWN_A))
        assert theirs.status_code == 404 and w.marks.b not in theirs.text, theirs.text
        named = w.client.get(f"/api/chat/sessions/{w.b_chat[0]}/move", headers=_as("a", Matrix.SPACE_B))
        assert named.status_code == 403 and w.marks.b not in named.text, named.text


@case("POST /api/chat/sessions/{session_id}/move", ITEM)
def move_chat(matrix: Matrix) -> None:
    with moves(matrix) as w:
        # B's chat, named in A's own space: no chat here.
        theirs = w.client.post(f"/api/chat/sessions/{w.b_chat[0]}/move", headers=_as("a", OWN_A),
                               json={"to_space": Matrix.SPACE_A})
        assert theirs.status_code == 404 and w.marks.b not in theirs.text, theirs.text
        # B's space named: refused before the route.
        named = w.client.post(f"/api/chat/sessions/{w.b_chat[0]}/move", headers=_as("a", Matrix.SPACE_B),
                              json={"to_space": OWN_A})
        assert named.status_code == 403 and w.marks.b not in named.text, named.text
        # A's own chat into B's space: not A's space.
        into = w.client.post(f"/api/chat/sessions/{w.a_chat[0]}/move", headers=_as("a", OWN_A),
                             json={"to_space": Matrix.SPACE_B})
        assert into.status_code == 403, into.text
        _b_chat_stays(matrix, w)
        assert threads.record_of(w.a_chat[1]).space_id == OWN_A
        # A's own chat into A's shared space moves.
        moved = w.client.post(f"/api/chat/sessions/{w.a_chat[0]}/move", headers=_as("a", OWN_A),
                              json={"to_space": Matrix.SPACE_A})
        assert moved.status_code == 200, moved.text
        assert threads.record_of(w.a_chat[1]).space_id == Matrix.SPACE_A
        assert [f["target"] for f in w.followed] == [Matrix.SPACE_A]
        _b_chat_stays(matrix, w)


@case("GET /api/chat/taken-out", ITEM)
def taken_out(matrix: Matrix) -> None:
    with moves(matrix) as w:
        chat_moves.record_taken_out(space_id=Matrix.SPACE_A, thread_id=w.a_chat[1], title=f"line {w.marks.a}",
                                    by=matrix.account_a)
        mine = w.client.get("/api/chat/taken-out", headers=_as("a", Matrix.SPACE_A))
        assert mine.status_code == 200, mine.text
        assert [line["title"] for line in mine.json()["lines"]] == [f"line {w.marks.a}"]
        assert w.marks.b not in mine.text
        named = w.client.get("/api/chat/taken-out", headers=_as("a", Matrix.SPACE_B))
        assert named.status_code == 403 and w.marks.b not in named.text, named.text
