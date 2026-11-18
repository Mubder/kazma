"""The matrix cases of Spaces S1-10: lessons (``tests/_space_matrix.py``).

A lesson held for confirmation is a commitment of the space it was learned
in. The three routes of the confirm queue are called as a member of space A,
through the real tenant middleware, with a held lesson of space B's: it is
not listed, and confirming or rejecting it answers "not found" and changes
nothing. A's own held lesson is listed, and confirming it teaches A's Soul
alone.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth

from tests._memory_space_cases import _AsMember
from tests._space_matrix import Matrix, case

ITEM = "S1-10"
A, B = Matrix.SPACE_A, Matrix.SPACE_B


@contextmanager
def lesson_world(matrix: Matrix) -> Iterator[SimpleNamespace]:
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.commitment.store import get_commitment
    from kazma_core.skills import self_improvement as si
    from kazma_ui.commitment_api import create_commitment_router

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(
        ignore_cleanup_errors=True
    ) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.delenv("KAZMA_MEMORY_OPS_DB", raising=False)
        patch.setenv("KAZMA_DATA_DIR", tmp)
        patch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
        patch.setattr(si, "_soul_requires_confirm", lambda: True)
        principal = matrix.principal("a")
        patch.setattr(auth, "get_request_principal", lambda request: principal)
        store = get_config_store()
        for key in (si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
            store.delete(key)
        marks = matrix.plant("lesson")
        held = {
            space: si.mint_soul_commitment(f"Remember the {mark} rule.", agent_id="supervisor", space=space)
            for space, mark in ((A, marks.a), (B, marks.b))
        }
        assert all(held.values())
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_commitment_router())
        try:
            yield SimpleNamespace(
                client=_AsMember(TestClient(app, raise_server_exceptions=False), A),
                held=held, marks=marks, status=lambda cid: get_commitment(cid).status,
                lessons=lambda space: si._agent_evolution_block(space=space),
            )
        finally:
            for key in (si._AGENT_EVO_KEY, si._SPACE_EVO_KEY):
                store.delete(key)


@case("GET /api/commitment/soul/pending", ITEM)
def held_lessons_listed(matrix: Matrix) -> None:
    with lesson_world(matrix) as w:
        answer = w.client.get("/api/commitment/soul/pending")
        assert answer.status_code == 200
        assert [row["commitment_id"] for row in answer.json()["pending"]] == [w.held[A]]
        assert w.marks.a in answer.text and w.marks.b not in answer.text


@case("POST /api/commitment/soul/{commitment_id}/confirm", ITEM)
def held_lesson_confirmed(matrix: Matrix) -> None:
    with lesson_world(matrix) as w:
        theirs = w.client.post(f"/api/commitment/soul/{w.held[B]}/confirm")
        assert theirs.status_code == 404 and theirs.json()["code"] == "not_found"
        assert w.status(w.held[B]) == "needs_confirm"
        assert w.lessons(A) == "" and w.lessons(B) == ""
        own = w.client.post(f"/api/commitment/soul/{w.held[A]}/confirm")
        assert own.status_code == 200 and own.json()["applied"] is True
        assert w.marks.a in w.lessons(A)
        assert w.lessons(B) == "" and w.lessons(scope.OWNER_SPACE_ID) == ""


@case("POST /api/commitment/soul/{commitment_id}/reject", ITEM)
def held_lesson_rejected(matrix: Matrix) -> None:
    with lesson_world(matrix) as w:
        theirs = w.client.post(f"/api/commitment/soul/{w.held[B]}/reject")
        assert theirs.status_code == 404 and theirs.json()["code"] == "not_found"
        assert w.status(w.held[B]) == "needs_confirm"
        own = w.client.post(f"/api/commitment/soul/{w.held[A]}/reject")
        assert own.status_code == 200 and w.status(w.held[A]) == "aborted"
