"""The matrix cases of Spaces S2-12, piece 7: a space's files and moving
one's own.

Every case runs with Spaces enforced, as person A against person B. A gets a
personal space of their own beside the matrix's two shared spaces; each
space's folder holds a file whose name carries a mark. A lists and moves
A's own; B's file never shows or moves for A: named in A's own space it is
no file, and naming B's space is refused by the membership check every
request passes.

Nothing here builds the app or decides the vault while pytest imports it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import identity, scope
from kazma_core.spaces.files import FILES, space_dir
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S2-12"
OWN_A = "matrix-a-files"


def _put(space: str, name: str) -> None:
    folder = space_dir(space) / FILES
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(f"the words of {name}".encode())


@contextmanager
def space_files(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; A's personal space; a file in A's personal space and
    one in B's space; the space files router behind the real tenant
    middleware."""
    from kazma_ui.space_files_api import create_space_files_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("file-move")

    def personal(reg: dict[str, Any]) -> None:
        reg["spaces"][OWN_A] = {"id": OWN_A, "name": "", "kind": "sandboxed",
                                "personal_of": matrix.account_a, "created_by": matrix.account_a}
        reg["memberships"].append({"account": matrix.account_a, "space": OWN_A, "role": "owner"})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        identity.update_registry(personal)
        a_file, b_file = f"a-{marks.a}.txt", f"b-{marks.b}.txt"
        _put(OWN_A, a_file)
        _put(Matrix.SPACE_B, b_file)
        app = FastAPI()
        Matrix.role_checked(app)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_space_files_router())
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, marks=marks, a_file=a_file, b_file=b_file)


def _as(who: str, space: str) -> dict[str, str]:
    return {"x-matrix-person": who, space_binding.SPACE_HEADER: space}


def _b_file_stays(w: SimpleNamespace) -> None:
    assert (space_dir(Matrix.SPACE_B) / FILES / w.b_file).exists()
    for space in (OWN_A, Matrix.SPACE_A):
        assert not (space_dir(space) / FILES / w.b_file).exists()


@case("GET /api/space-files", ITEM)
def files_list(matrix: Matrix) -> None:
    with space_files(matrix) as w:
        mine = w.client.get("/api/space-files", headers=_as("a", OWN_A))
        assert mine.status_code == 200, mine.text
        assert [f["path"] for f in mine.json()["files"]] == [w.a_file]
        assert w.marks.b not in mine.text
        named = w.client.get("/api/space-files", headers=_as("a", Matrix.SPACE_B))
        assert named.status_code == 403 and w.marks.b not in named.text, named.text


@case("POST /api/space-files/move", ITEM)
def files_move(matrix: Matrix) -> None:
    with space_files(matrix) as w:
        # B's file, named in A's own space: no file here.
        theirs = w.client.post("/api/space-files/move", headers=_as("a", OWN_A),
                               json={"path": w.b_file, "to_space": Matrix.SPACE_A})
        assert theirs.status_code == 404 and w.marks.b not in theirs.text, theirs.text
        # B's space named: refused before the route.
        named = w.client.post("/api/space-files/move", headers=_as("a", Matrix.SPACE_B),
                              json={"path": w.b_file, "to_space": OWN_A})
        assert named.status_code == 403 and w.marks.b not in named.text, named.text
        # A's own file into B's space: not A's space.
        into = w.client.post("/api/space-files/move", headers=_as("a", OWN_A),
                             json={"path": w.a_file, "to_space": Matrix.SPACE_B})
        assert into.status_code == 403, into.text
        _b_file_stays(w)
        assert not (space_dir(Matrix.SPACE_B) / FILES / w.a_file).exists()
        # A's own file into A's shared space moves.
        moved = w.client.post("/api/space-files/move", headers=_as("a", OWN_A),
                              json={"path": w.a_file, "to_space": Matrix.SPACE_A})
        assert moved.status_code == 200, moved.text
        assert (space_dir(Matrix.SPACE_A) / FILES / w.a_file).exists()
        assert not (space_dir(OWN_A) / FILES / w.a_file).exists()
        _b_file_stays(w)
