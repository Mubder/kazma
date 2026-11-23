"""Spaces S2-09: every action is recorded under the person's name.

Every route of the built app that changes something (``POST``, ``PUT``,
``PATCH``, ``DELETE``) is enumerated and called as a person through the real
tenant middleware and the activity recorder (``kazma_ui.activity``): each
writes one row of the space's activity naming that person's account, never
a session label. The handlers are stood in for (the recorder sees a route
and its answer, never what a handler does), so nothing a route does runs
here; ``test_a_real_route_of_the_built_app_is_recorded`` drives a real one
through the whole app.

The control: a recorder that writes the session's label (the memory
operator trail's actor before S2-09) leaves every route without the
person's account.

The Activity page shows a space's rows to its members only (the membership
check every request passes), newest first, with each person's name. An
install that does not enforce Spaces records nothing. The browser half is
``tests/e2e/test_activity_page_browser.py``.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from kazma_core.spaces import activity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix

A, B = Matrix.SPACE_A, Matrix.SPACE_B
_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def private_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "_activity_db", lambda: str(tmp_path / "activity.db"))


@pytest.fixture(scope="module")
def mutating_routes() -> list[tuple[str, str]]:
    """Every (method, path template) of the built app that changes something."""
    from kazma_ui.app import create_app

    from tests.test_route_scopes import served_routes

    found = sorted({(method, path) for method, path in served_routes(create_app()) if method in _MUTATING})
    assert len(found) > 200, len(found)  # the whole app, not a corner of it
    return found


def _stand_in(routes: list[tuple[str, str]]) -> Any:
    """The built app's mutating routes, each answering 200, behind the real
    tenant middleware and the recorder, in the built app's order."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from kazma_ui import auth
    from kazma_ui.activity import ActivityRecorder

    app = FastAPI()

    def answer() -> JSONResponse:
        return JSONResponse({"ok": True})

    for method, path in routes:
        app.add_api_route(path, answer, methods=[method])
    app.middleware("http")(auth.create_tenant_middleware())
    app.add_middleware(ActivityRecorder)
    return app


def _filled(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


_OWNER = {"source": "secret", "role": "admin", "user_id": "", "actor": "secret", "username": "admin"}


def _caller(matrix: Matrix, method: str, path: str) -> tuple[str, str, dict[str, Any]]:
    """Who calls a route: person A in their space; a Development route (only
    where Development is, S1-20) the owner in theirs."""
    from kazma_ui.route_scopes import is_development_route

    if is_development_route(path, method):
        return OWNER_SPACE_ID, OWNER_ACCOUNT_ID, _OWNER
    return A, matrix.account_a, matrix.principal_a


def _every_row(space: str) -> list[Any]:
    rows: list[Any] = []
    while True:
        page = activity.recent(space, limit=500, before=rows[-1].id if rows else None)
        rows += page
        if len(page) < 500:
            return rows


def _call_every_route(matrix: Matrix, routes: list[tuple[str, str]],
                      monkeypatch) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Call each route as its caller: what each row names (space, account) by
    action, and what each should name."""
    from fastapi.testclient import TestClient
    from kazma_ui import auth

    who: dict[str, Any] = {}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: who["principal"])
    expected: dict[str, tuple[str, str]] = {}
    with TestClient(_stand_in(routes)) as client:
        for method, path in routes:
            space, account, who["principal"] = _caller(matrix, method, path)
            expected[f"{method} {path}"] = (space, account)
            answer = client.request(method, _filled(path), headers={"X-Kazma-Space": space})
            assert answer.status_code == 200, (method, path, answer.status_code, answer.text)
    named = {row.action: (row.space, row.account) for space in (A, OWNER_SPACE_ID) for row in _every_row(space)}
    return named, expected


def test_every_route_that_changes_something_names_the_person(matrix, enforced, private_activity,
                                                             mutating_routes, monkeypatch) -> None:
    named, expected = _call_every_route(matrix, mutating_routes, monkeypatch)
    missing = [action for action, caller in expected.items() if named.get(action) != caller]
    assert not missing, missing[:20]
    assert any(caller[0] == A for caller in expected.values())


def test_control_a_recorder_writing_the_session_label_names_no_one(matrix, enforced, private_activity,
                                                                    mutating_routes, monkeypatch) -> None:
    """The memory operator trail's actor before S2-09: the session's label."""
    from kazma_ui import activity as ui_activity

    monkeypatch.setattr(ui_activity, "_who", lambda scope_: "web:operator:0a1b2c3d")
    named, expected = _call_every_route(matrix, mutating_routes, monkeypatch)
    assert not any(named.get(action) == caller for action, caller in expected.items())


def test_the_built_app_mounts_the_recorder_outside_the_tenant_middleware() -> None:
    from kazma_ui.activity import ActivityRecorder
    from kazma_ui.app import create_app

    app = create_app()
    classes = [m.cls for m in app.user_middleware]
    assert ActivityRecorder in classes
    # Added after the tenant middleware: outside it, so it reads what the
    # tenant middleware bound (user_middleware lists the outermost first).
    tenant = next(i for i, m in enumerate(app.user_middleware)
                  if getattr(m.kwargs.get("dispatch"), "__name__", "") == "tenant_middleware")
    assert classes.index(ActivityRecorder) < tenant


def test_nothing_is_recorded_without_spaces(matrix, private_activity, monkeypatch) -> None:
    routes = [("POST", "/api/documents/{document_id}/move")]
    monkeypatch.setattr(scope, "_enforced", False)
    named, _expected = _call_every_route(matrix, routes, monkeypatch)
    assert named == {}


def test_a_request_no_person_made_writes_nothing(enforced, private_activity) -> None:
    activity.record(A, "", "POST /api/x", [], 200)
    activity.record("", OWNER_ACCOUNT_ID, "POST /api/x", [], 200)
    assert activity.recent(A) == []


def test_old_rows_and_rows_past_the_most_go(enforced, private_activity, monkeypatch) -> None:
    monkeypatch.setattr(activity, "MAX_ROWS_PER_SPACE", 5)
    monkeypatch.setattr(activity, "_PRUNE_EVERY", 1)
    for n in range(8):
        activity.record(A, OWNER_ACCOUNT_ID, f"POST /api/x/{n}", [], 200)
    activity.record(B, OWNER_ACCOUNT_ID, "POST /api/y", [], 200)
    kept = [row.action for row in activity.recent(A)]
    assert kept == [f"POST /api/x/{n}" for n in (7, 6, 5, 4, 3)]
    assert [row.action for row in activity.recent(B)] == ["POST /api/y"]


# ── the Activity page ────────────────────────────────────────────────────


@pytest.fixture
def page_client(matrix, enforced, private_activity, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.activity import create_activity_router

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: people.get(request.headers.get("x-matrix-person", "")))
    app = FastAPI()
    app.include_router(create_activity_router(None))
    app.middleware("http")(auth.create_tenant_middleware())
    with TestClient(app) as made:
        yield made


def test_a_member_reads_their_space_s_activity_and_no_other(matrix, page_client) -> None:
    activity.record(A, matrix.account_a, "POST /api/documents/{document_id}/move", ["d1"], 200)
    activity.record(B, matrix.account_b, "DELETE /api/kb/libraries/{library_id}", ["lib"], 200)
    mine = page_client.get("/api/activity", headers={"x-matrix-person": "a", "X-Kazma-Space": A}).json()
    assert [(a["action"], a["who"], a["name"]) for a in mine["actions"]] == [
        ("POST /api/documents/{document_id}/move", matrix.account_a, "matrix-person-a")]
    refused = page_client.get("/api/activity", headers={"x-matrix-person": "a", "X-Kazma-Space": B})
    assert refused.status_code == 403
    theirs = page_client.get("/api/activity", headers={"x-matrix-person": "b", "X-Kazma-Space": B}).json()
    assert [a["who"] for a in theirs["actions"]] == [matrix.account_b]


def test_the_page_reads_older_rows_by_page(matrix, page_client, monkeypatch) -> None:
    for n in range(5):
        activity.record(A, matrix.account_a, f"POST /api/x/{n}", [], 200)
    from kazma_ui import activity as ui_activity

    monkeypatch.setattr(ui_activity, "_PAGE", 2)
    first = page_client.get("/api/activity", headers={"x-matrix-person": "a", "X-Kazma-Space": A}).json()
    assert [a["action"] for a in first["actions"]] == ["POST /api/x/4", "POST /api/x/3"] and first["more"]
    later = page_client.get(f"/api/activity?before={first['actions'][-1]['id']}",
                            headers={"x-matrix-person": "a", "X-Kazma-Space": A}).json()
    assert [a["action"] for a in later["actions"]] == ["POST /api/x/2", "POST /api/x/1"]


def test_a_space_s_activity_is_found_by_its_count_export_and_erase(matrix, enforced) -> None:
    """``activity.db`` is a row store of the data folder with a ``space``
    column: the walk a space's count, export and erase share finds its rows,
    and only its own."""
    import sqlite3

    from kazma_core.spaces import space_data

    activity.record(A, matrix.account_a, "POST /api/x", [], 200)
    activity.record(A, matrix.account_a, "POST /api/x", [], 200)
    activity.record(B, matrix.account_b, "POST /api/y", [], 200)
    try:
        assert space_data.count_space(A)["stores"].get("activity.db") == 2
        assert space_data.count_space(B)["stores"].get("activity.db") == 1
    finally:
        conn = sqlite3.connect(activity._activity_db())
        try:
            conn.execute("DELETE FROM activity WHERE space IN (?, ?)", (A, B))
            conn.commit()
        finally:
            conn.close()
