"""Every route of the built app says whose data it touches (Spaces S1-05).

``kazma_ui.route_scopes`` is one table that classes each route ``install``,
``space``, ``account`` or ``open``. Later items enforce it: a ``space`` route
is bound to one space and checks membership, and the admin is kept out of
spaces they are not a member of. A route missing from the table would be
missing from both, so this gate builds the real app and requires a scope for
every route it serves: HTTP routes, WebSocket routes and mounts.

The table is also held to what Kazma enforces today:

* a path the role table gives to the admin alone (a GET an operator is
  refused) is classed ``install``, or is listed in ``ADMIN_BUT_NOT_INSTALL``
  with why its data is a space's or a person's;
* a path sign-in lets through (``auth``'s open lists) is classed ``open``,
  and nothing else is, unless ``OPEN_BY_DESIGN`` says why it is served
  before sign-in;
* every rule decides at least one route, so a rule for a route that is gone,
  or one that an earlier rule always answers first, fails.

Each check is a pure function that returns problems, and each is run on a
planted mistake first (negative controls at the end).
"""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from kazma_core.security.platform_rbac import role_allows
from kazma_ui.auth import (
    ALWAYS_OPEN_PATHS,
    HEALTH_PROBE_PATHS,
    is_always_open,
    is_sensitive_path,
)
from kazma_ui.route_scopes import (
    ROUTE_SCOPE_RULES,
    ROUTE_SCOPES,
    Rule,
    compile_rules,
    deciding_rule,
    sample_requests,
    scope_of,
    unclassed,
)

from tests.test_api_route_callers import _all_routes, _route_rx

Route = tuple[str, str]

#: Admin-only today (the role table refuses an operator's GET), and classed
#: as a space's or a person's: the path, then why.
ADMIN_BUT_NOT_INSTALL: dict[str, str] = {
    "/api/email": (
        "connected accounts belong to the space that connected them (plan "
        "4.6); the mailbox is one for the install today, so its routes are "
        "the admin's until it moves"
    ),
    "/api/calendar": (
        "a calendar sign-in is a connected account of a space, like mail (plan 4.6)"
    ),
    "/api/commitment": (
        "lessons are kept per space (plan 4.6): the queue of lessons waiting "
        "for a yes is the space's own"
    ),
    "/api/import/history": (
        "an imported chat history becomes memories, and memory is a space's"
    ),
    "/api/sessions": (
        "lists chats with their titles: a space's content, whoever may ask today"
    ),
    "/api/settings/account": (
        "a person's own password, sessions and API tokens; they sit under "
        "/api/settings, which is the admin's as a whole"
    ),
    "/api/settings/appearance": (
        "theme and accent are a person's preference (plan 4.6, Settings)"
    ),
    "/api/settings/shortcuts": (
        "keyboard shortcuts are a person's preference (plan 4.6, Settings)"
    ),
}

#: Classed ``open`` and not on ``auth``'s open lists: sign-in never gates
#: these paths, and this is why they are served before it.
OPEN_BY_DESIGN: dict[str, str] = {
    "/static": "the pages' own scripts, styles and fonts; the login page needs them",
    "/sw.js": "the service worker is registered by every page, the login page included",
    "/manifest.webmanifest": "the browser reads the web app manifest before sign-in",
    "/.well-known/agent-card.json": (
        "other agents read the A2A card to find Kazma; it answers 404 while A2A is off"
    ),
}

#: Rules whose routes a switch puts into the app or takes out of it, so they
#: may decide no route of the app this test builds: the rule's pattern, then
#: the switch.
MOUNTED_BY_A_SWITCH: dict[str, str] = {
    "/api/chaos": "mounted only with KAZMA_CHAOS_ENABLED",
    "/docs": "the API pages are not mounted with KAZMA_PRODUCTION",
    "/redoc$": "the API pages are not mounted with KAZMA_PRODUCTION",
    "/openapi.json$": "the API pages are not mounted with KAZMA_PRODUCTION",
}


# ── the routes of the built app ──────────────────────────────────────────

def served_routes(app: FastAPI) -> list[Route]:
    """``(METHOD, path)`` for everything *app* serves.

    An HTTP route gives one pair per method. A WebSocket route is
    ``WEBSOCKET`` and a mount is ``MOUNT``. A route inside an included router
    that is not an API route (a WebSocket, a mount) keeps its served path on
    ``starlette_route``; without it the path read as empty.
    """
    out: list[Route] = []
    for route in _all_routes(app.routes):
        inner = getattr(route, "starlette_route", None) or route
        path = getattr(route, "path", "") or getattr(inner, "path", "")
        methods = getattr(route, "methods", None) or getattr(inner, "methods", None)
        assert path, f"a route with no path: {route!r}"
        if methods:
            out.extend((method.upper(), path) for method in sorted(methods))
            continue
        kind = type(getattr(route, "original_route", None) or inner).__name__
        out.append(("WEBSOCKET" if "WebSocket" in kind else "MOUNT", path))
    return out


@pytest.fixture(scope="module")
def routes() -> list[Route]:
    from kazma_ui.app import create_app

    return served_routes(create_app())


# ── the checks ───────────────────────────────────────────────────────────

def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def admin_only_problems(
    routes: list[Route],
    rules: tuple[Rule, ...] | None = None,
    exceptions: dict[str, str] | None = None,
) -> list[str]:
    """Admin-only GETs that are not classed ``install`` and have no reason."""
    exceptions = ADMIN_BUT_NOT_INSTALL if exceptions is None else exceptions
    problems: list[str] = []
    used: set[str] = set()
    for method, path in sorted(set(routes)):
        # Sign-in lets an always-open path through before any role is asked.
        if method != "GET" or is_always_open(path):
            continue
        if role_allows("operator", path, "GET"):
            continue
        scope = scope_of(method, path, rules)
        if scope in ("install", None):  # None is the unclassed check's to report
            continue
        covering = [prefix for prefix in exceptions if _under(path, prefix)]
        if covering:
            used.update(covering)
            continue
        problems.append(
            f"GET {path} is the admin's alone today and is classed {scope}: "
            "class it install, or say why not in ADMIN_BUT_NOT_INSTALL"
        )
    for prefix in sorted(set(exceptions) - used):
        problems.append(f"ADMIN_BUT_NOT_INSTALL entry excuses nothing now: {prefix}")
    return problems


def _on_auth_open_lists(path: str) -> bool:
    """Whether sign-in lets *path* (a route's own path) through by its lists."""
    if is_always_open(path) or path in HEALTH_PROBE_PATHS:
        return True
    served = _route_rx(path)  # a route with a parameter: /docs/guide/{guide}
    return any(served.match(listed) for listed in ALWAYS_OPEN_PATHS)


def open_problems(
    routes: list[Route],
    rules: tuple[Rule, ...] | None = None,
    by_design: dict[str, str] | None = None,
) -> list[str]:
    """Where ``open`` in the table and "before sign-in" in ``auth`` disagree."""
    by_design = OPEN_BY_DESIGN if by_design is None else by_design
    problems: list[str] = []
    used: set[str] = set()
    for method, path in sorted(set(routes)):
        scope = scope_of(method, path, rules)
        listed = _on_auth_open_lists(path)
        if listed and scope != "open":
            problems.append(
                f"{method} {path} is answered before sign-in and is classed {scope}"
            )
        if scope != "open" or listed:
            continue
        if path not in by_design:
            problems.append(
                f"{method} {path} is classed open and sign-in's open lists do not "
                "hold it: class it, or say why in OPEN_BY_DESIGN"
            )
            continue
        used.add(path)
        if is_sensitive_path(path):
            problems.append(
                f"{method} {path} is classed open, and sign-in is asked for there"
            )
    for path in sorted(set(by_design) - used):
        problems.append(f"OPEN_BY_DESIGN entry excuses nothing now: {path}")
    return problems


def stale_rules(
    routes: list[Route],
    rules: tuple[Rule, ...] | None = None,
    switched: dict[str, str] | None = None,
) -> list[str]:
    """Rules that decide no route: the route is gone, or an earlier rule
    always answers first."""
    switched = MOUNTED_BY_A_SWITCH if switched is None else switched
    rules = compile_rules(ROUTE_SCOPE_RULES) if rules is None else rules
    deciding = {deciding_rule(method, path, rules) for method, path in routes}
    problems = [
        f"rule decides no route: {rule.pattern!r} -> {rule.scope}"
        for rule in rules
        if rule not in deciding and rule.pattern not in switched
    ]
    known = {rule.pattern for rule in rules}
    problems += [
        f"MOUNTED_BY_A_SWITCH names no rule: {pattern}"
        for pattern in sorted(set(switched) - known)
    ]
    return problems


# ── the gate ─────────────────────────────────────────────────────────────

def test_every_route_of_the_built_app_has_a_scope(routes) -> None:
    assert len(routes) > 500, "the app's routes were not listed"
    missing = unclassed(routes)
    assert not missing, (
        "These routes are in no rule of kazma_ui.route_scopes. Read what the "
        "handler reads and writes, then class it install, space, account or "
        "open:\n  " + "\n  ".join(missing)
    )


def test_every_kind_of_route_is_listed(routes) -> None:
    """HTTP routes, the WebSockets (one sits in an included router) and the
    static mount are all among the routes the gate reads."""
    listed = set(routes)
    assert ("WEBSOCKET", "/ws/chat/{session_id}") in listed
    assert ("WEBSOCKET", "/ws/voice") in listed
    assert ("WEBSOCKET", "/ws/dashboard") in listed
    assert ("MOUNT", "/static") in listed
    assert ("POST", "/api/chat/stream") in listed
    assert ("GET", "/api/webhooks/telegram/health") in listed


def test_every_scope_is_one_of_the_four(routes) -> None:
    counts = Counter(scope_of(method, path) for method, path in routes)
    assert set(counts) <= set(ROUTE_SCOPES)
    # Each class holds routes: a table that put everything in one would pass
    # the gate above and say nothing.
    assert all(counts[scope] > 0 for scope in ROUTE_SCOPES), counts


def test_admin_only_routes_are_the_installs_or_say_why(routes) -> None:
    problems = admin_only_problems(routes)
    assert not problems, "\n  ".join(["", *problems])
    assert all(len(reason) > 30 for reason in ADMIN_BUT_NOT_INSTALL.values())


def test_open_in_the_table_is_open_at_sign_in(routes) -> None:
    problems = open_problems(routes)
    assert not problems, "\n  ".join(["", *problems])
    assert all(len(reason) > 30 for reason in OPEN_BY_DESIGN.values())


def test_no_rule_is_stale(routes) -> None:
    problems = stale_rules(routes)
    assert not problems, "\n  ".join(["", *problems])


def test_the_routes_the_plan_names_are_classed() -> None:
    """S1-05 names the routes added since the plan's first draft."""
    assert scope_of("POST", "/api/x/oauth2/start") == "space"
    assert scope_of("GET", "/api/x/oauth2/callback") == "space"
    assert scope_of("POST", "/api/x/api-access") == "space"
    assert scope_of("POST", "/api/a2a") == "space"
    assert scope_of("GET", "/api/a2a/status") == "install"
    assert scope_of("POST", "/api/support/report") == "install"
    assert scope_of("GET", "/api/support/settings") == "install"
    # Mounted only with its switch on, and classed all the same.
    assert scope_of("GET", "/api/chaos/experiments") == "install"


def test_a_specific_rule_stands_before_its_area() -> None:
    assert scope_of("GET", "/api/settings/account/sessions") == "account"
    assert scope_of("GET", "/api/settings/system/logs") == "install"
    assert scope_of("GET", "/api/memory/v2/queue") == "install"
    assert scope_of("GET", "/api/memory/v2/beliefs") == "space"
    assert scope_of("GET", "/api/documents/ops/readiness") == "install"
    assert scope_of("GET", "/api/documents/{document_id}") == "space"
    assert scope_of("GET", "/api/swarm/workers/{name}/logs") == "space"
    assert scope_of("GET", "/api/swarm/workers/{name}/metrics") == "install"
    assert scope_of("GET", "/api/github/oauth/callback") == "open"
    assert scope_of("GET", "/api/github/oauth/status") == "space"
    assert scope_of("GET", "/docs/guide/{guide}") == "open"
    assert scope_of("GET", "/docs") == "install"


# ── how a rule matches ───────────────────────────────────────────────────

def test_a_prefix_ends_at_a_path_segment() -> None:
    rules = compile_rules((("/api/x", "space"),))
    assert scope_of("GET", "/api/x", rules) == "space"
    assert scope_of("GET", "/api/x/", rules) == "space"
    assert scope_of("POST", "/api/x/reply/approve", rules) == "space"
    assert scope_of("GET", "/api/x?tab=1", rules) == "space"
    assert scope_of("GET", "/api/xyz", rules) is None
    assert scope_of("GET", "/api/x-studio", rules) is None
    assert scope_of("GET", "/api", rules) is None


def test_an_exact_rule_takes_no_children() -> None:
    rules = compile_rules((("/api/models$", "install"), ("/$", "open")))
    assert scope_of("GET", "/api/models", rules) == "install"
    assert scope_of("GET", "/api/models/options", rules) is None
    assert scope_of("GET", "/", rules) == "open"
    assert scope_of("GET", "/chat", rules) is None


def test_a_parameter_stands_for_one_segment() -> None:
    rules = compile_rules((("/api/workers/{name}/logs$", "space"),))
    assert scope_of("GET", "/api/workers/{name}/logs", rules) == "space"
    assert scope_of("GET", "/api/workers/coder-1/logs", rules) == "space"
    assert scope_of("GET", "/api/workers/logs", rules) is None
    assert scope_of("GET", "/api/workers/a/b/logs", rules) is None


def test_a_method_rule_is_for_its_method() -> None:
    rules = compile_rules(((("PUT", "/api/thing$"), "install"), ("/api/thing", "space")))
    assert scope_of("PUT", "/api/thing", rules) == "install"
    assert scope_of("put", "/api/thing", rules) == "install"
    assert scope_of("GET", "/api/thing", rules) == "space"
    # HEAD is answered by the GET handler.
    head = compile_rules(((("GET", "/api/thing"), "space"),))
    assert scope_of("HEAD", "/api/thing", head) == "space"
    assert scope_of("POST", "/api/thing", head) is None


def test_the_first_rule_that_matches_decides() -> None:
    rules = compile_rules((("/api/area/special", "install"), ("/api/area", "space")))
    assert scope_of("GET", "/api/area/special/x", rules) == "install"
    assert scope_of("GET", "/api/area/other", rules) == "space"
    assert deciding_rule("GET", "/api/area/other", rules).pattern == "/api/area"
    assert deciding_rule("GET", "/elsewhere", rules) is None


def test_sample_requests_are_requests_their_rule_decides() -> None:
    """What a test that drives the table asks for: a real path per rule."""
    for scope in ROUTE_SCOPES:
        samples = sample_requests(scope)
        assert samples, scope
        for method, path in samples:
            assert "$" not in path and "{" not in path, path
            assert scope_of(method, path) == scope, (method, path)
    space = sample_requests("space")
    assert ("GET", "/chat") in space
    assert ("GET", "/api/swarm/workers/x1/logs") in space
    assert len(space) >= 40
    rules = compile_rules(((("PUT", "/api/thing$"), "install"), ("/api/thing", "space")))
    assert sample_requests("install", rules) == [("PUT", "/api/thing")]
    assert sample_requests("space", rules) == [("GET", "/api/thing")]


def test_a_wrong_rule_is_refused() -> None:
    with pytest.raises(ValueError):
        compile_rules((("/api/x", "everyone"),))
    with pytest.raises(ValueError):
        compile_rules((("api/x", "space"),))
    with pytest.raises(ValueError):
        compile_rules((("/", "open"),))  # every path: written "/$" for the root


# ── negative controls ────────────────────────────────────────────────────

def test_a_planted_route_is_reported(routes) -> None:
    assert unclassed([*routes, ("GET", "/api/planted-thing")]) == ["GET /api/planted-thing"]
    assert unclassed([("post", "/planted/page"), ("GET", "/api/chat/sessions")]) == [
        "POST /planted/page"
    ]


def test_a_planted_route_in_a_real_app_is_reported() -> None:
    """Through the same listing the gate uses: a route, a WebSocket and a
    mount inside an included router, with the prefix they are served at."""
    router = APIRouter()

    @router.get("/planted-thing")
    def _thing() -> dict:
        return {}

    @router.websocket("/planted-socket")
    async def _socket(websocket: WebSocket) -> None:
        await websocket.accept()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router, prefix="/api")
    app.mount("/planted-files", StaticFiles(check_dir=False, directory="."))
    found = served_routes(app)
    assert set(found) == {
        ("GET", "/api/planted-thing"),
        ("WEBSOCKET", "/api/planted-socket"),
        ("MOUNT", "/planted-files"),
    }
    assert unclassed(found) == [
        "GET /api/planted-thing",
        "MOUNT /planted-files",
        "WEBSOCKET /api/planted-socket",
    ]


def test_a_planted_stale_rule_is_reported(routes) -> None:
    gone = compile_rules((*ROUTE_SCOPE_RULES, ("/api/removed-long-ago", "install")))
    assert stale_rules(routes, gone) == [
        "rule decides no route: '/api/removed-long-ago' -> install"
    ]
    # A rule an earlier one always answers first decides nothing either.
    shadowed = compile_rules((*ROUTE_SCOPE_RULES, ("/api/chat/stream$", "space")))
    assert stale_rules(routes, shadowed) == [
        "rule decides no route: '/api/chat/stream$' -> space"
    ]
    assert stale_rules(routes, switched={**MOUNTED_BY_A_SWITCH, "/api/nothing": "X"}) == [
        "MOUNTED_BY_A_SWITCH names no rule: /api/nothing"
    ]


def test_a_planted_open_class_on_a_signed_in_route_is_reported(routes) -> None:
    planted = compile_rules((("/api/chat/sessions$", "open"), *ROUTE_SCOPE_RULES))
    problems = open_problems(routes, planted)
    assert problems and all("/api/chat/sessions " in p for p in problems)
    assert all("classed open and sign-in's open lists do not hold it" in p for p in problems)
    # Naming it in OPEN_BY_DESIGN does not help: sign-in is asked for there.
    excused = open_problems(
        routes, planted, {**OPEN_BY_DESIGN, "/api/chat/sessions": "planted"}
    )
    assert excused and all("sign-in is asked for there" in p for p in excused)


def test_a_route_sign_in_lets_through_must_be_open(routes) -> None:
    planted = compile_rules((("/login$", "account"), *ROUTE_SCOPE_RULES))
    assert open_problems(routes, planted) == [
        "GET /login is answered before sign-in and is classed account"
    ]
    assert open_problems(routes, by_design={**OPEN_BY_DESIGN, "/api/gone": "planted"}) == [
        "OPEN_BY_DESIGN entry excuses nothing now: /api/gone"
    ]


def test_an_admin_only_route_classed_elsewhere_is_reported(routes) -> None:
    planted = compile_rules((("/api/settings/system/logs$", "space"), *ROUTE_SCOPE_RULES))
    problems = admin_only_problems(routes, planted)
    assert len(problems) == 1 and problems[0].startswith(
        "GET /api/settings/system/logs is the admin's alone today and is classed space"
    )
    # Without its reasons, the table's own exceptions are findings.
    bare = admin_only_problems(routes, exceptions={})
    assert any(p.startswith("GET /api/email/status ") for p in bare)
    assert any(p.startswith("GET /api/settings/account/sessions ") for p in bare)
    assert admin_only_problems(
        routes, exceptions={**ADMIN_BUT_NOT_INSTALL, "/api/chat": "planted"}
    ) == ["ADMIN_BUT_NOT_INSTALL entry excuses nothing now: /api/chat"]
