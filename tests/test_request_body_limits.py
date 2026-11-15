"""Every request's body has one limit, refused before it is read (2026-11-15).

``POST /api/auth/login`` is open before sign-in, and it read a 64 MB chunked
body in full before answering 401: ``read_json`` called ``request.body()``
with no cap, and FastAPI parsed a Pydantic or ``dict`` body of any size into
memory. ``UploadSizeLimitMiddleware`` (FA-134) bounded the two upload routes
only. ``kazma_ui.body_limits.BodySizeLimitMiddleware`` now gives every
request one limit: small for a route open before sign-in, a default for the
rest, and a declared larger one where a feature needs it.

Measured through ``create_app()``'s whole middleware stack by the bytes the
app pulls from the connection. The same app with the body layer taken out
-- every route reading what arrives, as before -- is the negative control.
The gate holds :func:`body_limits._route_body_limits` to the built app's
routes both ways.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap

import pytest

from tests._secured_app import SECRET, call, secured_app

MB = 1024 * 1024


@pytest.fixture
def app(monkeypatch):
    return secured_app(monkeypatch)


def _without_body_layer(app):
    from kazma_ui.body_limits import BodySizeLimitMiddleware

    app.user_middleware = [m for m in app.user_middleware if m.cls is not BodySizeLimitMiddleware]
    app.middleware_stack = None
    return app


def _json_of(size: int) -> bytes:
    return b'{"secret": "' + b"x" * size + b'"}'


# -- open before sign-in -------------------------------------------------------


def test_a_declared_oversized_sign_in_is_refused_unread(app):
    from kazma_ui.body_limits import OPEN_BODY_LIMIT

    answer = call(app, "POST", "/api/auth/login", body=_json_of(OPEN_BODY_LIMIT * 4),
                  headers={"content-type": "application/json"})
    assert (answer.status, answer.read) == (413, 0)
    assert answer.json()["code"] == "body_too_large"


def test_a_chunked_oversized_sign_in_stops_at_the_limit(app):
    from kazma_ui.body_limits import OPEN_BODY_LIMIT

    body = _json_of(8 * MB)
    answer = call(app, "POST", "/api/auth/login", body=body, declare=False, piece=16 * 1024,
                  headers={"content-type": "application/json"})
    assert answer.status == 413
    assert answer.read <= OPEN_BODY_LIMIT + 16 * 1024, answer.read


def test_an_ordinary_sign_in_still_answers(app):
    answer = call(app, "POST", "/api/auth/login", body=b'{"secret": "wrong"}',
                  headers={"content-type": "application/json"})
    assert answer.status == 401
    ok = call(app, "POST", "/api/auth/login", body=json.dumps({"secret": SECRET}).encode(),
              headers={"content-type": "application/json"}, client=("127.0.0.1", 50001))
    assert ok.status == 200


def test_negative_control_without_the_layer_the_whole_sign_in_body_is_read(app):
    body = _json_of(8 * MB)
    answer = call(_without_body_layer(app), "POST", "/api/auth/login", body=body, declare=False,
                  headers={"content-type": "application/json"})
    assert answer.status == 401
    assert answer.read == len(body)


# -- signed in -------------------------------------------------------------------


def test_a_signed_in_json_body_past_the_default_is_refused(app):
    from kazma_ui.body_limits import DEFAULT_BODY_LIMIT

    headers = {"content-type": "application/json", "x-kazma-secret": SECRET}
    # A Pydantic body and a body read by read_json.
    for path in ("/api/bookmarks", "/api/alerts/read"):
        declared = call(app, "POST", path, body=_json_of(3 * MB), headers=headers)
        assert (declared.status, declared.read) == (413, 0), path
        chunked = call(app, "POST", path, body=_json_of(6 * MB), headers=headers,
                       declare=False, piece=32 * 1024)
        assert chunked.status == 413, path
        assert chunked.read <= DEFAULT_BODY_LIMIT + 32 * 1024, (path, chunked.read)


def test_negative_control_without_the_layer_a_signed_in_body_is_read_whole(app):
    headers = {"content-type": "application/json", "x-kazma-secret": SECRET}
    body = _json_of(3 * MB)
    answer = call(_without_body_layer(app), "POST", "/api/alerts/read", body=body, headers=headers)
    assert answer.status != 413
    assert answer.read == len(body)


def test_a_route_that_needs_more_gets_its_declared_limit(app):
    """A settings backup is up to 10 MB: 3 MB passes the layer to the route."""
    body = b"# not a backup\n" + b"x" * (3 * MB)
    answer = call(app, "POST", "/api/settings/system/restore", body=body,
                  headers={"content-type": "text/plain", "x-kazma-secret": SECRET})
    assert answer.status != 413
    assert answer.read == len(body)


def test_an_unauthenticated_request_to_a_gated_route_is_not_read(app):
    body = _json_of(1 * MB)
    answer = call(app, "POST", "/api/bookmarks", body=body, headers={"content-type": "application/json"})
    assert answer.status == 401
    assert answer.read == 0


# -- the gate: the table and the app's routes, both ways -------------------------

_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: What in a route's code means it may take a body larger than a JSON
#: request: a file, a multipart form or the raw stream. (A text ``Form()``
#: field is a small body, like JSON.)
_TAKES_MORE = re.compile(r"UploadFile|\bFile\(|request\.stream\(|request\.form\(")

#: Modules whose routes read the raw stream through their own reader, capped
#: below the default limit: module -> the cap's name. They need no more.
SELF_BOUNDED_READERS = {
    "kazma_ui.kb_api": "_BODY_MAX_BYTES",
    "kazma_ui.memory_api": "_BODY_MAX_BYTES",
}


def _all_routes(routes) -> list:
    out = []
    for route in routes:
        effective = getattr(route, "effective_candidates", None)
        if callable(effective):
            out.extend(_all_routes(effective()))
            continue
        inner = getattr(route, "original_router", None)
        out.extend(_all_routes(inner.routes) if inner is not None else [route])
    return out


def _reach(endpoint) -> str:
    """The endpoint's source and the functions of its module it calls, to
    any depth (a route that streams through a helper streams)."""
    import sys

    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
    except (OSError, TypeError):
        return ""
    module = sys.modules.get(getattr(endpoint, "__module__", ""), None)
    try:
        tree = ast.parse(inspect.getsource(module)) if module else None
    except (OSError, TypeError):
        tree = None
    defs: dict[str, list[str]] = {}
    if tree is not None:
        text = inspect.getsource(module)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(node.name, []).append(ast.get_source_segment(text, node) or "")
    seen, texts, todo = {endpoint.__name__}, [source], [source]
    while todo:
        try:
            node_tree = ast.parse(textwrap.dedent(todo.pop()))
        except SyntaxError:
            continue
        for node in ast.walk(node_tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name and name not in seen and name in defs:
                    seen.add(name)
                    texts.extend(defs[name])
                    todo.extend(defs[name])
    return "\n".join(texts)


@pytest.fixture(scope="module")
def body_routes():
    with pytest.MonkeyPatch.context() as mp:
        app = secured_app(mp)
        routes = []
        for route in _all_routes(app.routes):
            methods = (getattr(route, "methods", None) or set()) & _BODY_METHODS
            if methods and getattr(route, "endpoint", None) is not None:
                routes.append((route, sorted(methods)))
        yield routes


def _concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", template)


def test_every_declared_limit_names_a_route_the_app_serves(body_routes):
    from kazma_ui.body_limits import _route_body_limits

    served = {(m, r.path) for r, methods in body_routes for m in methods}
    stale = sorted(key for key in _route_body_limits() if key not in served)
    assert not stale, f"declared body limits for routes the app does not serve: {stale}"


def test_every_route_that_takes_a_file_or_a_stream_declares_its_limit(body_routes):
    from kazma_ui.body_limits import _route_body_limits

    declared = set(_route_body_limits())
    missing = sorted(
        (m, r.path) for r, methods in body_routes for m in methods
        if _TAKES_MORE.search(_reach(r.endpoint)) and (m, r.path) not in declared
        and r.endpoint.__module__ not in SELF_BOUNDED_READERS
    )
    assert not missing, (
        "a route that takes a file, a form or the raw stream needs its limit in "
        f"body_limits._route_body_limits: {missing}"
    )


def test_a_self_bounded_reader_is_below_the_default_and_still_used(body_routes):
    import importlib

    from kazma_ui.body_limits import DEFAULT_BODY_LIMIT

    for module, cap in SELF_BOUNDED_READERS.items():
        assert getattr(importlib.import_module(module), cap) <= DEFAULT_BODY_LIMIT, module
        assert any(r.endpoint.__module__ == module for r, _m in body_routes), (
            f"{module} serves no body route: remove it from SELF_BOUNDED_READERS")


def test_every_body_route_has_its_limit_and_open_ones_stay_small(body_routes):
    from kazma_ui.auth import is_always_open, is_sensitive_path
    from kazma_ui.body_limits import (
        DEFAULT_BODY_LIMIT,
        DOCUMENT_UPLOAD_CEILING,
        OPEN_BODY_LIMIT,
        body_limit,
        _route_body_limits,
    )

    declared = _route_body_limits()
    checked = 0
    for route, methods in body_routes:
        path = _concrete(route.path)
        for method in methods:
            limit = body_limit(method, path)
            # The layer passes a declared length up to OPEN_BODY_LIMIT without
            # a lookup: no route may take less.
            assert OPEN_BODY_LIMIT <= limit <= DOCUMENT_UPLOAD_CEILING, (method, route.path, limit)
            if (method, route.path) in declared:
                assert limit == declared[(method, route.path)], (method, route.path)
            elif is_always_open(path) or not is_sensitive_path(path):
                assert limit == OPEN_BODY_LIMIT, (method, route.path, limit)
            else:
                assert limit == DEFAULT_BODY_LIMIT, (method, route.path, limit)
            checked += 1
    assert checked > 200, "the route walk found too few routes: it is broken"


def test_the_gate_finds_a_planted_stream_route():
    """Negative control for the scan: a route reading the raw stream through a
    helper of its module is found."""

    async def _helper(request):
        async for _chunk in request.stream():
            pass

    async def planted(request):
        await _helper(request)

    planted.__module__ = __name__
    assert _TAKES_MORE.search(_reach(planted))
