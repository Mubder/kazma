"""The matrix cases of Spaces S2-09: each space's activity.

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``. An action of each space is planted through the
product's activity store, its path ids holding a mark. A is shown A's
actions and never B's, the page as the API; a request naming B's space is
refused by the membership check every request passes.

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
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S2-09"


class _Page:
    """Stands in for the page templates: the answer names the template."""

    def TemplateResponse(self, request: Any, name: str, context: dict[str, Any]) -> Any:  # noqa: N802
        from fastapi.responses import JSONResponse

        return JSONResponse({"template": name, "active_page": context.get("active_page")})


@contextmanager
def activity(matrix: Matrix) -> Iterator[SimpleNamespace]:
    """Spaces enforced; an action of each space recorded in its own space;
    the activity router behind the real tenant middleware."""
    from kazma_core.spaces.activity import record

    from kazma_ui.activity import create_activity_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    marks = matrix.plant("activity")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        record(Matrix.SPACE_B, matrix.account_b, "DELETE /api/documents/{document_id}", [marks.b], 200)
        record(Matrix.SPACE_A, matrix.account_a, "DELETE /api/documents/{document_id}", [marks.a], 200)
        app = FastAPI()
        Matrix.role_checked(app)
        app.middleware("http")(auth.create_tenant_middleware())
        app.include_router(create_activity_router(_Page()))
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, marks=marks)


def _as(who: str, space: str) -> dict[str, str]:
    return {"x-matrix-person": who, space_binding.SPACE_HEADER: space}


@case("GET /api/activity", ITEM)
def activity_api(matrix: Matrix) -> None:
    with activity(matrix) as w:
        got = w.client.get("/api/activity", headers=_as("a", Matrix.SPACE_A))
        assert got.status_code == 200, got.text
        ids = [i for action in got.json()["actions"] for i in action["ids"]]
        assert w.marks.a in ids and w.marks.b not in str(got.json()), ids
        mine = [a for a in got.json()["actions"] if w.marks.a in a["ids"]]
        assert [a["who"] for a in mine] == [matrix.account_a], mine
        # B's space, named by A: refused before the route.
        theirs = w.client.get("/api/activity", headers=_as("a", Matrix.SPACE_B))
        assert theirs.status_code == 403 and w.marks.b not in theirs.text, theirs.text


@case("GET /activity", ITEM)
def activity_page(matrix: Matrix) -> None:
    with activity(matrix) as w:
        got = w.client.get("/activity", headers=_as("a", Matrix.SPACE_A))
        assert got.status_code == 200 and w.marks.b not in got.text, got.text
        theirs = w.client.get("/activity", headers=_as("a", Matrix.SPACE_B))
        assert theirs.status_code == 403 and w.marks.b not in theirs.text, theirs.text
