"""Every space route and every agent tool is in the space matrix (S1-29).

Spaces puts several people on one Kazma, each in a space. What holds the
spaces apart is tested one way everywhere: a route or a tool is driven as a
member of one space with another space's ids; nothing of the other space is
shown or changed, and the caller's own rows are. Memory has had this test
since 2026-09-27 (``tests/test_memory_routes_tenant_scope.py``). The matrix
makes it the rule for every part.

This is the harness, and it starts empty. ``tests/_space_matrix.py`` holds
the tables; each later item of stage 1 adds its cases there. The gate:

* every route of the built app that the route table classes ``space`` has a
  case, or is pending under the plan item that will write it;
* every agent tool has a case, a pending item, or a reason it touches no
  space's data;
* nothing is stale: a case, a pending tool or a reason names a route or tool
  that exists, a pending rule still decides a route with no case, and a tool
  is in one table only;
* every item named is an item of the plan's stage 1;
* the number of pending targets only goes down (``PENDING_BASELINE``), so a
  new space route or tool comes with its case;
* every registered case runs, with a fresh ``Matrix``: two people, each the
  one member of a space, made through the real identity store.

Each check is a pure function that returns problems, and each is run on a
planted mistake (the negative controls at the end).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests import _space_matrix
from tests._space_matrix import (
    CASES,
    NOT_IN_THE_MATRIX,
    PENDING_ROUTE_RULES,
    PENDING_TOOLS,
    Case,
    Matrix,
    Tables,
    case,
    is_tool,
    pending_item,
    pending_item_for_route,
    space_routes,
    tables,
    tool_targets,
)
from tests.test_route_scopes import served_routes

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs" / "plans" / "SPACES_PLAN.md"

#: The targets that still wait for their case: space routes and tools. Each
#: item of stage 1 writes the cases of its part and takes them out of the
#: pending tables; this number may only go down, and lowering it means
#: lowering the baseline in the same change.
PENDING_BASELINE = 147


# ── what exists ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def routes() -> list[str]:
    """The ``space`` routes of the built app, as targets."""
    from kazma_ui.app import create_app

    return space_routes(served_routes(create_app()))


@pytest.fixture(scope="module")
def tools() -> list[str]:
    return tool_targets()


@pytest.fixture(scope="module")
def live() -> Tables:
    return tables()


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


def plan_items(text: str | None = None) -> set[str]:
    """The item ids of the plan's stage 1, read from its table."""
    text = PLAN.read_text(encoding="utf-8") if text is None else text
    return set(re.findall(r"^\|\s*(S1-\d\d)\s*\|", text, flags=re.MULTILINE))


# ── the checks ───────────────────────────────────────────────────────────

def missing(routes: list[str], tools: list[str], t: Tables) -> list[str]:
    """Space routes and tools that are in no table."""
    problems = [
        f"{target}: a space route with no case and no pending item"
        for target in routes
        if target not in t.cases and t.pending_item(target) is None
    ]
    problems += [
        f"{target}: a tool with no case, no pending item and no reason"
        for target in tools
        if target not in t.cases and target not in t.tools and target not in t.not_in
    ]
    return problems


def stale(routes: list[str], tools: list[str], t: Tables) -> list[str]:
    """Entries that name nothing now, and targets that are in two tables."""
    known_routes, known_tools = set(routes), set(tools)
    problems: list[str] = []
    for target in sorted(t.cases):
        if is_tool(target):
            if target not in known_tools:
                problems.append(f"case names no registered tool: {target}")
        elif target not in known_routes:
            problems.append(f"case names no space route of the app: {target}")
    for name, table in (("PENDING_TOOLS", t.tools), ("NOT_IN_THE_MATRIX", t.not_in)):
        for target in sorted(table):
            if target not in known_tools:
                problems.append(f"{name} names no registered tool: {target}")
            if target in t.cases:
                problems.append(f"{target} has a case and is still in {name}")
    problems += [
        f"{target} is pending and in NOT_IN_THE_MATRIX"
        for target in sorted(set(t.tools) & set(t.not_in))
    ]
    # A route rule stays while a route it decides still lacks a case.
    deciding = {
        t.route_rule(*target.split(" ", 1)) for target in routes if target not in t.cases
    }
    problems += [
        f"pending route rule decides no space route without a case: {pattern!r} -> {item}"
        for pattern, item in t.route_rules
        if (pattern, item) not in deciding
    ]
    return problems


def unknown_items(items: set[str], t: Tables) -> list[str]:
    """Item ids the plan's stage 1 does not have."""
    named = [(c.item, f"the case of {target}") for target, c in t.cases.items()]
    named += [(item, f"the pending rule {pattern!r}") for pattern, item in t.route_rules]
    named += [(item, f"the pending tool {target}") for target, item in t.tools.items()]
    return sorted(
        f"{where} names {item}, which is not an item of stage 1"
        for item, where in named
        if item not in items
    )


def pending_targets(routes: list[str], tools: list[str], t: Tables) -> list[str]:
    """The space routes and tools that wait for their case."""
    return [target for target in (*routes, *tools) if t.pending_item(target) is not None]


def ratchet_problems(count: int, baseline: int) -> list[str]:
    if count > baseline:
        return [
            f"{count} targets wait for their case, above the baseline of {baseline}: "
            "a new space route or tool comes with its case (tests/_space_matrix.py)"
        ]
    if count < baseline:
        return [
            f"{count} targets wait for their case, below the baseline of {baseline}: "
            "lower PENDING_BASELINE in this change so the gain is kept"
        ]
    return []


def run_case(registered: Case, matrix: Matrix) -> None:
    """Run one case. Its failure is the test's."""
    registered.run(matrix)


# ── the gate ─────────────────────────────────────────────────────────────

def test_the_app_and_the_registry_were_listed(routes, tools) -> None:
    assert len(routes) > 250, "the app's space routes were not listed"
    assert len(tools) > 150, "the tool registry did not load"
    assert "POST /api/chat/stream" in routes
    assert "WEBSOCKET /ws/chat/{session_id}" in routes
    assert "tool:memory_search" in tools and "tool:shell_exec" in tools
    # The one tool a swarm worker offers its model itself is a tool too.
    assert "tool:handoff_to_worker" in tools


def test_every_space_route_and_tool_is_in_the_matrix(routes, tools, live) -> None:
    problems = missing(routes, tools, live)
    assert not problems, (
        "Each of these needs a case in the space matrix (tests/_space_matrix.py): "
        "act as a member of one space with another space's ids. If its part of "
        "stage 1 is not built yet, name the plan item that will write the case in "
        "PENDING_ROUTE_RULES or PENDING_TOOLS. A tool that touches no space's "
        "data says why in NOT_IN_THE_MATRIX.\n  " + "\n  ".join(problems)
    )


def test_nothing_in_the_matrix_is_stale(routes, tools, live) -> None:
    problems = stale(routes, tools, live)
    assert not problems, "\n  ".join(["", *problems])
    assert all(len(reason) > 30 for reason in NOT_IN_THE_MATRIX.values())


def test_every_item_named_is_an_item_of_stage_one(live) -> None:
    items = plan_items()
    assert {"S1-01", "S1-29", "S1-34"} <= items, "the plan's stage 1 table was not read"
    assert unknown_items(items, live) == []


def test_pending_targets_only_go_down(routes, tools, live) -> None:
    waiting = pending_targets(routes, tools, live)
    by_item = Counter(live.pending_item(target) for target in waiting)
    problems = ratchet_problems(len(waiting), PENDING_BASELINE)
    assert not problems, f"{problems[0]}\n  by item: {dict(sorted(by_item.items()))}"


def test_a_target_is_looked_up_one_way() -> None:
    # A target with a case is not pending (S1-12 wrote the documents' cases).
    assert pending_item("GET /api/documents/{document_id}") is None
    assert pending_item("POST /api/documents/search") is None
    assert pending_item_for_route("get", "/api/ide/jobs") == "S1-20"
    assert pending_item("tool:git_status") == "S1-20"
    assert pending_item("tool:email_list") is None  # S1-17 (mail, X, GitHub) wrote its cases
    assert pending_item("tool:github_list_issues") is None
    assert pending_item("tool:memory_search") is None  # its item wrote its case
    assert pending_item("tool:current_datetime") is None  # it has a reason instead
    assert pending_item("tool:no_such_tool") is None
    assert pending_item("GET /api/settings/system/logs") is None  # no rule: not a space route
    # A specific rule stands before its area.
    assert pending_item("POST /api/chat/upload") == "S1-19"
    assert pending_item("GET /api/chat/files/{file_id}") == "S1-19"
    # An area whose item wrote its cases has no pending rule left.
    assert pending_item("POST /api/chat/stream") is None
    assert pending_item("GET /api/x/status") is None
    assert pending_item("GET /api/github/repos") is None
    assert pending_item("POST /api/github/repos/clone") == "S1-20"  # a specific rule, still pending
    # A prefix ends at a path segment.
    assert pending_item("GET /api/idex/files") is None
    assert pending_item("GET /api/ide/files") == "S1-20"


def test_the_route_filter_keeps_space_routes_only() -> None:
    listed = [
        ("GET", "/api/kb/libraries"),
        ("get", "/api/kb/libraries"),
        ("GET", "/api/settings/system/logs"),  # install
        ("GET", "/api/account/spaces"),  # account
        ("GET", "/health/live"),  # open
        ("WEBSOCKET", "/ws/voice"),
    ]
    assert space_routes(listed) == ["GET /api/kb/libraries", "WEBSOCKET /ws/voice"]


# ── the cases ────────────────────────────────────────────────────────────

_REGISTERED = sorted(tables().cases)


@pytest.mark.parametrize(
    "target",
    _REGISTERED
    or [pytest.param(None, marks=pytest.mark.skip(reason="no case is registered yet"))],
    ids=_REGISTERED or ["none"],
)
def test_case(target, matrix) -> None:
    run_case(CASES[target], matrix)


# ── the fixture ──────────────────────────────────────────────────────────

def test_the_matrix_fixture_keeps_two_people_apart(matrix) -> None:
    a, b = matrix.account_a, matrix.account_b
    assert a and b and a != b and OWNER_ACCOUNT_ID not in (a, b)
    assert identity.is_member(a, Matrix.SPACE_A) and not identity.is_member(a, Matrix.SPACE_B)
    assert identity.is_member(b, Matrix.SPACE_B) and not identity.is_member(b, Matrix.SPACE_A)
    assert not identity.is_member(OWNER_ACCOUNT_ID, Matrix.SPACE_A), "the admin is in neither"
    assert not identity.is_member(OWNER_ACCOUNT_ID, Matrix.SPACE_B)

    # In-process work runs as the person, in their space.
    with matrix.as_a():
        assert scope.current_scope() == scope.Scope(space_id="matrix-a", asker=a)
        assert scope.require_space_id() == "matrix-a"
    with matrix.as_b():
        assert scope.current_scope() == scope.Scope(space_id="matrix-b", asker=b)
    assert scope.current_scope() is None, "the scope ends with its block"

    # At the request boundary, through the real tenant middleware: a person
    # runs in the space they name only when they are a member of it.
    with matrix.requests_as("a") as client:
        own = client.get("/api/chat/sessions", headers=matrix.headers_a)
        other = client.get("/api/chat/sessions", headers=matrix.headers_b)
        reached = list(client.reached)
    assert own.status_code == 200 and own.json() == {"space": "matrix-a", "asker": a}
    assert other.status_code == 403 and other.json()["code"] == "not_a_member"
    assert reached == ["/api/chat/sessions"], "the refused request never reached its handler"
    with matrix.requests_as("b") as client:
        own = client.post("/api/documents/search", headers=matrix.headers_b)
        other = client.post("/api/documents/search", headers=matrix.headers_a)
    assert own.json() == {"space": "matrix-b", "asker": b}
    assert other.status_code == 403 and other.json()["code"] == "not_a_member"


def test_markers_are_one_per_space_and_never_repeat(matrix) -> None:
    first, second = matrix.plant("fact"), matrix.plant("fact")
    assert len({first.a, first.b, second.a, second.b}) == 4
    assert first.a.startswith("mxa") and first.b.startswith("mxb")
    assert first.a.endswith("fact") and first.a[3:] == first.b[3:]
    # One word wherever it is stored: an id, a title, a search index.
    assert all(re.fullmatch(r"[a-z0-9]+", mark) for mark in (*first, *matrix.plant("A b-c!")))
    assert Matrix().plant("fact").a != first.a, "another run's markers are its own"


def test_the_matrix_leaves_no_account_behind(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        during = identity.read_registry()
        assert {Matrix.SPACE_A, Matrix.SPACE_B} <= set(during["spaces"])
        assert {made.account_a, made.account_b} <= set(during["accounts"])
    after = identity.read_registry()
    assert set(after["spaces"]) == {OWNER_SPACE_ID}, "only the owner's space is left"
    assert set(after["accounts"]) == {OWNER_ACCOUNT_ID}, "only the owner is left"


# ── a case and its registration ──────────────────────────────────────────

def test_a_registered_case_is_run_with_the_matrix(matrix, monkeypatch) -> None:
    monkeypatch.setattr(_space_matrix, "CASES", {})
    seen: list[Matrix] = []

    @case("tool:planted_tool", "S1-09")
    def planted(given: Matrix) -> None:
        seen.append(given)

    registered = _space_matrix.CASES["tool:planted_tool"]
    assert registered.item == "S1-09"
    run_case(registered, matrix)
    assert seen == [matrix]
    with pytest.raises(ValueError, match="has a case already"):
        case("tool:planted_tool", "S1-09")(planted)

    def crossing(given: Matrix) -> None:
        raise AssertionError("space B's row was shown to A")

    with pytest.raises(AssertionError, match="space B's row"):
        run_case(Case(target="GET /api/planted", run=crossing, item="S1-08"), matrix)


def test_a_malformed_case_is_refused() -> None:
    def nothing(given: Matrix) -> None:
        return None

    for target in ("memory_search", "/api/chat", "get /api/chat", "tool:", "GET api/chat", ""):
        with pytest.raises(ValueError):
            Case(target=target, run=nothing, item="S1-08")
    with pytest.raises(ValueError):
        Case(target="tool:memory_search", run=nothing, item="memory")
    with pytest.raises(ValueError):
        Case(target="tool:memory_search", run=None, item="S1-09")  # type: ignore[arg-type]


# ── negative controls ────────────────────────────────────────────────────

def _planted_case(target: str, item: str = "S1-08") -> Case:
    return Case(target=target, run=lambda given: None, item=item)


def test_a_planted_space_route_with_nothing_is_reported(routes, tools, live) -> None:
    planted = space_routes([("POST", "/api/ide/planted-thing"), ("GET", "/ide")])
    assert planted == ["GET /ide", "POST /api/ide/planted-thing"]
    # Without the rules of its area, the route is in no table.
    bare = replace(
        live,
        route_rules=tuple(r for r in live.route_rules if r[0] not in ("/api/ide", "/ide$")),
    )
    assert missing(planted, [], bare) == [
        "GET /ide: a space route with no case and no pending item",
        "POST /api/ide/planted-thing: a space route with no case and no pending item",
    ]
    # And every real route of that area is reported with it.
    reported = missing(routes, tools, bare)
    assert any(p.startswith("GET /api/ide/") for p in reported), reported
    assert all("/api/ide" in p or p.startswith("GET /ide:") for p in reported)
    # An area that has its cases: a new route there with none is reported
    # at once, by name (no prefix rule answers for it any more).
    assert missing(["GET /api/chat/planted-thing"], [], live) == [
        "GET /api/chat/planted-thing: a space route with no case and no pending item"
    ]
    # A case answers for its route.
    cased = replace(bare, cases={**bare.cases, "GET /ide": _planted_case("GET /ide", "S1-20")})
    assert missing(["GET /ide"], [], cased) == []


def test_a_new_route_in_a_pending_area_raises_the_count(routes, tools, live) -> None:
    """A prefix rule answers for a new route of its area, so the count is
    what says a route came without its case."""
    grown = sorted([*routes, "GET /api/ide/planted-thing"])
    assert missing(grown, tools, live) == []
    before = len(pending_targets(routes, tools, live))
    after = len(pending_targets(grown, tools, live))
    assert after == before + 1
    assert ratchet_problems(after, before)[0].startswith(f"{after} targets wait for their case, above")
    assert "lower PENDING_BASELINE" in ratchet_problems(before - 1, before)[0]
    assert ratchet_problems(before, before) == []
    # With its case, the count stays where it was.
    cased = replace(live, cases={
        **live.cases, "GET /api/ide/planted-thing": _planted_case("GET /api/ide/planted-thing", "S1-20")})
    assert len(pending_targets(grown, tools, cased)) == before


def test_a_planted_tool_with_nothing_is_reported(routes, tools, live) -> None:
    assert missing(routes, [*tools, "tool:planted_tool"], live) == [
        "tool:planted_tool: a tool with no case, no pending item and no reason"
    ]
    # Each table answers for it.
    for answered in (
        replace(live, tools={**live.tools, "tool:planted_tool": "S1-19"}),
        replace(live, not_in={**live.not_in, "tool:planted_tool": "planted"}),
        replace(live, cases={**live.cases, "tool:planted_tool": _planted_case("tool:planted_tool")}),
    ):
        assert missing(routes, [*tools, "tool:planted_tool"], answered) == []
    # Without its pending entries, a real area's tools are findings.
    bare = replace(live, tools={k: v for k, v in live.tools.items() if v != "S1-21"})
    assert missing(routes, tools, bare) == [
        f"tool:{name}: a tool with no case, no pending item and no reason"
        for name in ("list_runs", "run_status")
    ]


def test_a_stale_entry_is_reported(routes, tools, live) -> None:
    gone = replace(
        live,
        cases={
            "tool:removed_tool": _planted_case("tool:removed_tool"),
            "GET /api/removed-long-ago": _planted_case("GET /api/removed-long-ago"),
            # A real route, and not a space's: the matrix is about space routes.
            "GET /api/settings/system/logs": _planted_case("GET /api/settings/system/logs"),
        },
    )
    assert stale(routes, tools, gone) == [
        "case names no space route of the app: GET /api/removed-long-ago",
        "case names no space route of the app: GET /api/settings/system/logs",
        "case names no registered tool: tool:removed_tool",
    ]
    assert stale(routes, tools, replace(live, tools={**live.tools, "tool:removed_tool": "S1-08"})) == [
        "PENDING_TOOLS names no registered tool: tool:removed_tool"
    ]
    assert stale(routes, tools, replace(live, not_in={**live.not_in, "tool:removed_tool": "x"})) == [
        "NOT_IN_THE_MATRIX names no registered tool: tool:removed_tool"
    ]


def test_a_target_in_two_tables_is_reported(routes, tools, live) -> None:
    planted = _planted_case("tool:git_status", "S1-20")
    both = replace(live, cases={**live.cases, "tool:git_status": planted})
    assert stale(routes, tools, both) == [
        "tool:git_status has a case and is still in PENDING_TOOLS"
    ]
    reasoned = replace(live, cases={**live.cases, "tool:hijri_convert": _planted_case("tool:hijri_convert")})
    assert stale(routes, tools, reasoned) == [
        "tool:hijri_convert has a case and is still in NOT_IN_THE_MATRIX"
    ]
    twice = replace(live, not_in={**live.not_in, "tool:git_status": "planted"})
    assert stale(routes, tools, twice) == ["tool:git_status is pending and in NOT_IN_THE_MATRIX"]


def test_a_stale_route_rule_is_reported(routes, tools, live) -> None:
    gone = replace(live, route_rules=(*live.route_rules, ("/api/removed-long-ago", "S1-08")))
    assert stale(routes, tools, gone) == [
        "pending route rule decides no space route without a case: "
        "'/api/removed-long-ago' -> S1-08"
    ]
    # A rule an earlier one always answers first decides nothing either.
    shadowed = replace(live, route_rules=(*live.route_rules, ("/api/chat/stream$", "S1-08")))
    assert stale(routes, tools, shadowed) == [
        "pending route rule decides no space route without a case: '/api/chat/stream$' -> S1-08"
    ]
    # Once every route a rule decides has its case, the rule goes.
    done = replace(
        live,
        cases={
            **live.cases,
            **{
                target: _planted_case(target, "S1-32")
                for target in routes
                if live.pending_item(target) == "S1-32"
            },
        },
    )
    assert len(done.cases) > len(live.cases), "the Dashboard page's route was not found"
    assert stale(routes, tools, done) == [
        "pending route rule decides no space route without a case: '/dashboard$' -> S1-32"
    ]


def test_an_item_the_plan_does_not_have_is_reported(live) -> None:
    items = plan_items()
    assert unknown_items(items, replace(live, tools={**live.tools, "tool:memory_search": "S1-99"})) == [
        "the pending tool tool:memory_search names S1-99, which is not an item of stage 1"
    ]
    assert unknown_items(
        items, replace(live, route_rules=(*live.route_rules, ("/api/planted", "S2-05")))
    ) == ["the pending rule '/api/planted' names S2-05, which is not an item of stage 1"]
    assert unknown_items(
        items, replace(live, cases={**live.cases, "tool:memory_search": _planted_case("tool:memory_search", "S4-01")})
    ) == ["the case of tool:memory_search names S4-01, which is not an item of stage 1"]
    # The ids come from the plan's own table.
    assert plan_items("| S1-01 | x |\n| S1-02 | y |\ntext S1-77 in a sentence\n| S2-01 | z |") == {
        "S1-01", "S1-02",
    }


def test_the_tables_are_the_ones_the_gate_reads(live) -> None:
    assert live.route_rules is PENDING_ROUTE_RULES
    assert live.tools is PENDING_TOOLS and live.not_in is NOT_IN_THE_MATRIX
    assert live.cases is CASES
