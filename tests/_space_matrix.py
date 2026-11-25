"""The space matrix: its cases, what is still pending, and its fixture (S1-29).

Spaces puts several people on one Kazma, each in a space. The matrix is the
test that holds the spaces apart: every route that reads or writes a space's
data, and every agent tool, is driven as a member of one space with another
space's ids. Nothing of the other space is shown or changed, and the
caller's own rows are. ``tests/test_space_matrix.py`` is the gate; this
module holds what it reads.

Three tables, and every target is in exactly one place:

``CASES``
    A target with its case. A case is a function that takes a
    :class:`Matrix` and makes the check. An item of stage 1 registers its
    cases with :func:`case`, in a module it adds to :data:`CASE_MODULES`.
``PENDING_ROUTE_RULES`` and ``PENDING_TOOLS``
    A target whose case is not written yet, with the plan item that will
    write it (``docs/plans/SPACES_PLAN.md``, section 5). The harness starts
    empty, so today every target is here. An item that adds a case takes the
    target out of here in the same change.
``NOT_IN_THE_MATRIX``
    A tool that reads and writes no space's data, with why.

A target is ``"METHOD /path"`` for a route, with the path as the router
holds it (``GET /api/documents/{document_id}``), or ``"tool:<name>"`` for an
agent tool.

Routes are pending by ordered prefix rules, in the grammar of
``kazma_ui.route_scopes``; the first rule that matches decides. A rule stays
while any route it decides still lacks a case. Tools are named one by one.
"""

from __future__ import annotations

import functools
import importlib
import re
import secrets
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity, scope
from kazma_ui import auth, space_binding
from kazma_ui.route_scopes import SPACE, Pattern, Rule, compile_rules, deciding_rule, scope_of

__all__ = [
    "CASES",
    "CASE_MODULES",
    "NOT_IN_THE_MATRIX",
    "PENDING_ROUTE_RULES",
    "PENDING_TOOLS",
    "Case",
    "Matrix",
    "Planted",
    "Tables",
    "case",
    "load_cases",
    "pending_item",
    "pending_item_for_route",
    "space_routes",
    "tables",
    "tool_targets",
]

TOOL_PREFIX = "tool:"

_ROUTE_TARGET = re.compile(r"[A-Z]+ /\S*")
_TOOL_TARGET = re.compile(r"tool:[A-Za-z0-9_]+")
_ITEM = re.compile(r"S\d-\d\d")


def is_tool(target: str) -> bool:
    return target.startswith(TOOL_PREFIX)


def _valid_target(target: object) -> bool:
    return isinstance(target, str) and bool(
        _ROUTE_TARGET.fullmatch(target) or _TOOL_TARGET.fullmatch(target)
    )


# ── Cases ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """One check of the matrix.

    ``run`` acts as a member of space A against space B's ids (through
    :class:`Matrix`) and asserts that nothing of B is shown or changed and
    that A's own rows are. ``item`` is the plan item that added the case.
    """

    target: str
    run: Callable[[Matrix], None]
    item: str

    def __post_init__(self) -> None:
        if not _valid_target(self.target):
            raise ValueError(
                f"a target is 'METHOD /path' or 'tool:<name>', not {self.target!r}"
            )
        if not _ITEM.fullmatch(self.item or ""):
            raise ValueError(f"a case names its plan item (S1-08), not {self.item!r}")
        if not callable(self.run):
            raise ValueError(f"the case of {self.target} has nothing to run")


#: target -> its case. Filled by :func:`case`; empty until the first item of
#: stage 1 after the harness adds one.
CASES: dict[str, Case] = {}

#: The modules that register cases, as import names (``tests._chat_space_cases``).
#: An item adds its module here; :func:`load_cases` imports each.
CASE_MODULES: tuple[str, ...] = (
    "tests._chat_space_cases", "tests._memory_space_cases", "tests._lesson_space_cases",
    "tests._knowledge_space_cases", "tests._scheduled_space_cases", "tests._swarm_space_cases",
    "tests._research_space_cases", "tests._mail_space_cases", "tests._x_space_cases",
    "tests._github_space_cases", "tests._document_space_cases",  # S1-12
    "tests._vault_space_cases", "tests._files_space_cases",
    "tests._host_tools_space_cases", "tests._runs_space_cases", "tests._traces_space_cases",
    "tests._activity_space_cases", "tests._chat_move_space_cases", "tests._file_move_space_cases",
)


def case(target: str, item: str) -> Callable[[Callable[[Matrix], None]], Callable[[Matrix], None]]:
    """Register the decorated function as the case of *target*::

        @case("GET /api/chat/sessions", "S1-08")
        def chat_list(matrix: Matrix) -> None:
            ...
    """

    def register(run: Callable[[Matrix], None]) -> Callable[[Matrix], None]:
        if target in CASES:
            raise ValueError(f"{target} has a case already")
        CASES[target] = Case(target=target, run=run, item=item)
        return run

    return register


def load_cases() -> dict[str, Case]:
    """Import every module of :data:`CASE_MODULES` and return :data:`CASES`."""
    for name in CASE_MODULES:
        importlib.import_module(name)
    return CASES


# ── Pending: routes ──────────────────────────────────────────────────────

#: A route pattern (``kazma_ui.route_scopes``' grammar: a prefix that ends at
#: a path segment, ``$`` for that path alone, ``{name}`` for any one segment)
#: and the plan item that will write the cases of the routes it decides. The
#: first rule that matches decides, so a specific rule stands before its area.
PENDING_ROUTE_RULES: tuple[tuple[Pattern, str], ...] = (
    # (S1-19 -- a chat's uploads and shared files, and the research files of
    # the space's folder -- wrote its cases: tests/_files_space_cases.py.)
    # (S1-08 -- chats, checkpoints, Time Travel, drafts, speech and A2A --
    # wrote its cases: tests/_chat_space_cases.py.)
    # (S1-09 -- memory, and a history brought in -- wrote its cases:
    # tests/_memory_space_cases.py.)
    # (S1-10, lessons: written. Its cases are in tests/_lesson_space_cases.py.)
    # (S1-12, documents: written. Its cases are in
    # tests/_document_space_cases.py.)
    # (S1-13, the Knowledge Library: written. Its cases are in
    # tests/_knowledge_space_cases.py.)
    # (S1-14, Scheduled: written. Its cases are in
    # tests/_scheduled_space_cases.py; an X post booked from the page is the
    # X account's: S1-17's second part, tests/_x_space_cases.py.)
    # (S1-15, the swarm -- its tasks, their approvals, a worker's log --
    # wrote its cases: tests/_swarm_space_cases.py.)
    # (S1-16, research sessions and tasks: written. Its cases are in
    # tests/_research_space_cases.py.) A research report, a paper and the
    # acquisition journal are files and runs of the workspace folder, which
    # becomes the space's with S1-19:
    # (S1-20 -- the Development routes: the workspace, its files, git, the
    # IDE and a clone -- wrote its cases: tests/_host_tools_space_cases.py.)
    # ── S1-17: connected accounts ─────────────────────────────────────────
    # (Mail and calendar, X, then GitHub: written. Their cases are in
    # tests/_mail_space_cases.py, tests/_x_space_cases.py and
    # tests/_github_space_cases.py; a clone is S1-20's, above.)
    # (S1-21 -- approvals and running work -- wrote its cases:
    # tests/_runs_space_cases.py.)
    # ── The Dashboard ──────────────────────────────────────────────────────
    # The page is per person with S1-32. (S1-22 -- the trace feed: the Agents
    # page and API and the Dashboard's socket -- wrote its cases:
    # tests/_traces_space_cases.py.)
    ("/dashboard$", "S1-32"),
)


# ── Pending: tools ───────────────────────────────────────────────────────

#: The plan item, then the tools whose cases it will write.
_PENDING_TOOLS_BY_ITEM: dict[str, tuple[str, ...]] = {
    # (S1-08 -- drafts, the task ledger, the scratchpad, the chat itself --
    # wrote its cases: tests/_chat_space_cases.py.)
    # (S1-09 -- the memory tools -- wrote its cases: tests/_memory_space_cases.py.)
    # (S1-12 -- the document tools -- wrote its cases: tests/_document_space_cases.py.)
    # (S1-14 -- the scheduling tools -- wrote its cases:
    # tests/_scheduled_space_cases.py.)
    # (S1-15 -- swarm tasks, the worker's hand-off and sub-agents -- wrote
    # its cases: tests/_swarm_space_cases.py.)
    # Research sessions, reports and saved research files. The web tools are
    # here too: a chat's search is kept as a research session, a crawl has a
    # journal row, and fetched pages share one cache. S1-16 decides for each
    # whether it needs a case or holds nothing of a space.
    # (S1-16 -- research sessions and the web search a chat keeps -- wrote
    # its cases: tests/_research_space_cases.py. The research files went to
    # S1-19, the space folder.)
    # Connected accounts: mail, calendar, X and GitHub.
    # (S1-17's three parts -- mail and calendar, X, GitHub -- wrote their
    # cases: tests/_mail_space_cases.py, tests/_x_space_cases.py,
    # tests/_github_space_cases.py.)
    # (S1-18 -- the vault tools -- wrote its cases: tests/_vault_space_cases.py.)
    # (S1-19 -- the space folder: every tool that takes a path or writes a
    # file, the browser and the research files -- wrote its cases:
    # tests/_files_space_cases.py.)
    # (S1-20 -- host tools: the shell, git, the code index, MCP servers,
    # skills and packages, settings, the machine, the chat apps' senders --
    # wrote its cases: tests/_host_tools_space_cases.py.)
    # (S1-21 -- the agent's run tools -- wrote its cases:
    # tests/_runs_space_cases.py.)
}


def _flatten(groups: Mapping[str, Iterable[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item, names in groups.items():
        for name in names:
            target = TOOL_PREFIX + name
            if target in out:
                raise ValueError(f"{name} is pending under {out[target]} and {item}")
            out[target] = item
    return out


#: ``tool:<name>`` -> the plan item that will write its case.
PENDING_TOOLS: dict[str, str] = _flatten(_PENDING_TOOLS_BY_ITEM)

#: Tools that read and write no space's data: the target, then why.
NOT_IN_THE_MATRIX: dict[str, str] = {
    "tool:plan_research_queries": "a model call on the topic it is given; it reads and keeps nothing",
    "tool:critique_synthesis_gaps": "a model call on the text it is given; it reads and keeps nothing",
    "tool:research_readiness": "the install's search and fetch health; it shows no space's research",
    "tool:read_url": (
        "fetches a public page; its cache holds public pages by address and policy, no space's words "
        "(saving a page is read_url_to_file, S1-19)"
    ),
    "tool:crawl_page": "fetches one public page through read_url and returns it; it keeps nothing of a space",
    "tool:current_datetime": "answers the clock and the install's time zone; it reads no stored data",
    "tool:hijri_convert": "converts the date it is given; it reads and writes nothing",
    "tool:arabic_translate": (
        "translates the text it is given with the configured model; it reads "
        "and writes no stored data"
    ),
    "tool:insert_diacritics": (
        "adds vowel marks to the text it is given; it reads and writes no stored data"
    ),
}


# ── The tables together ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Tables:
    """The matrix's tables, as the gate reads them. The gate's own negative
    controls pass a copy with a planted mistake (``dataclasses.replace``)."""

    cases: Mapping[str, Case]
    route_rules: tuple[tuple[Pattern, str], ...]
    tools: Mapping[str, str]
    not_in: Mapping[str, str]

    @functools.cached_property
    def compiled(self) -> tuple[tuple[Rule, str], ...]:
        return tuple(
            (compile_rules(((pattern, SPACE),))[0], item)
            for pattern, item in self.route_rules
        )

    def route_rule(self, method: str, path: str) -> tuple[Pattern, str] | None:
        """The first pending rule that matches the route, as (pattern, item)."""
        for rule, item in self.compiled:
            if deciding_rule(method, path, (rule,)) is not None:
                return rule.pattern, item
        return None

    def pending_item(self, target: str) -> str | None:
        """The plan item a target is pending under, or None. A target with a
        case is not pending."""
        if target in self.cases:
            return None
        if is_tool(target):
            return self.tools.get(target)
        method, _, path = target.partition(" ")
        decided = self.route_rule(method, path)
        return decided[1] if decided else None


def tables() -> Tables:
    """The live tables, with every case module loaded."""
    return Tables(
        cases=load_cases(),
        route_rules=PENDING_ROUTE_RULES,
        tools=PENDING_TOOLS,
        not_in=NOT_IN_THE_MATRIX,
    )


def pending_item_for_route(method: str, path: str) -> str | None:
    """The plan item that will write the case of ``method path``, or None."""
    return tables().pending_item(f"{method.upper()} {path}")


def pending_item(target: str) -> str | None:
    """The plan item a route or tool target is pending under, or None."""
    return tables().pending_item(target)


# ── What exists: the space routes and the tools ──────────────────────────


def space_routes(app_routes: Iterable[tuple[str, str]]) -> list[str]:
    """The ``"METHOD /path"`` targets among *app_routes* (``(method, path)``
    pairs, as ``tests.test_route_scopes.served_routes`` lists them) that the
    route table classes ``space``."""
    return sorted(
        {
            f"{method.upper()} {path}"
            for method, path in app_routes
            if scope_of(method, path) == SPACE
        }
    )


@functools.lru_cache(maxsize=1)
def _tool_names() -> tuple[str, ...]:
    """Every agent tool: the registry a running Kazma builds (built-ins and
    native skills, as ``tests/test_static_gates.py`` builds it), and the one
    tool a swarm worker offers its model itself."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.swarm.handoff import HANDOFF_TOOL_NAME

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return tuple(sorted({*registry._tools, HANDOFF_TOOL_NAME}))


def tool_targets() -> list[str]:
    """``"tool:<name>"`` for every agent tool."""
    return [TOOL_PREFIX + name for name in _tool_names()]


# ── The fixture ──────────────────────────────────────────────────────────


class Planted(NamedTuple):
    """One marker for each space. A case plants ``a`` in space A and ``b`` in
    space B, then looks for them in what each person is shown."""

    a: str
    b: str


class Matrix:
    """Two people, each the one member of a space of their own.

    Made through the real identity store: two accounts (local users) and the
    spaces ``matrix-a`` and ``matrix-b``. Use it as a context manager; the
    identity registry is removed when the block ends::

        with Matrix() as matrix:
            with matrix.as_a():
                ...  # in-process calls run as person A in matrix-a
            marks = matrix.plant("fact")

    What a case of a later item needs beyond this (rows planted in a store, a
    client of the built app) is added here by that item.
    """

    #: Ids as Kazma makes them (a moved space has one); the prose of the case
    #: modules calls the two spaces ``matrix-a`` and ``matrix-b``.
    SPACE_A = "s0a0a0a0a0a0a"
    SPACE_B = "s0b0b0b0b0b0b"

    def __init__(self) -> None:
        self.account_a = ""
        self.account_b = ""
        self.principal_a: dict[str, Any] = {}
        self.principal_b: dict[str, Any] = {}
        self._run = secrets.token_hex(4)
        self._planted = 0

    # ── set-up and clean-up ──────────────────────────────────────────────

    def __enter__(self) -> Matrix:
        get_config_store().delete(identity.REGISTRY_KEY)
        try:
            self.principal_a = self._person("matrix-person-a")
            self.principal_b = self._person("matrix-person-b")
            self.account_a = self._account(self.principal_a)
            self.account_b = self._account(self.principal_b)
            identity.create_space("Matrix A", created_by=self.account_a, space_id=self.SPACE_A)
            identity.create_space("Matrix B", created_by=self.account_b, space_id=self.SPACE_B)
        except BaseException:
            get_config_store().delete(identity.REGISTRY_KEY)
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        get_config_store().delete(identity.REGISTRY_KEY)
        # Its people made the install multi-user, and the vault caches that
        # (sticky for the process: its default rung stays closed). A test
        # after this one sees an install of one again.
        from kazma_core.security.vault import reset_posture_cache

        reset_posture_cache()

    @staticmethod
    def _person(name: str) -> dict[str, Any]:
        """A local user, and the principal sign-in gives their session
        (``kazma_ui.auth.get_request_principal``'s shape)."""
        user = platform_rbac.create_local_user(name, secrets.token_urlsafe(16))
        return {
            "username": name,
            "role": "operator",
            "user_id": user.user_id,
            "actor": "login",
            "source": "session",
        }

    @staticmethod
    def _account(principal: dict[str, Any]) -> str:
        account = space_binding.account_of(principal)
        if not account:
            raise RuntimeError(f"{principal['username']} has no account")
        return account

    # ── who is who ───────────────────────────────────────────────────────

    def principal(self, who: str) -> dict[str, Any]:
        return {"a": self.principal_a, "b": self.principal_b}[who]

    @property
    def headers_a(self) -> dict[str, str]:
        """What a page of person A's space sends on every call."""
        return {space_binding.SPACE_HEADER: self.SPACE_A}

    @property
    def headers_b(self) -> dict[str, str]:
        return {space_binding.SPACE_HEADER: self.SPACE_B}

    def as_a(self) -> AbstractContextManager[scope.Scope]:
        """Run the block as person A in space A (for in-process tool calls)."""
        return scope.bind_scope(self.SPACE_A, self.account_a)

    def as_b(self) -> AbstractContextManager[scope.Scope]:
        """Run the block as person B in space B."""
        return scope.bind_scope(self.SPACE_B, self.account_b)

    # ── markers ──────────────────────────────────────────────────────────

    def plant(self, word: str = "") -> Planted:
        """A marker for each space, unique to this call: lower-case letters
        and digits only, so it stays one word in an id, a title or a search
        index. *word* says what the marker stands for (``"fact"``)."""
        self._planted += 1
        tail = f"{self._run}n{self._planted}{re.sub(r'[^a-z0-9]', '', word.lower())}"
        return Planted(a=f"mxa{tail}", b=f"mxb{tail}")

    # ── the request boundary ─────────────────────────────────────────────

    @staticmethod
    def role_checked(app: FastAPI) -> list[tuple[str, str]]:
        """Put the auth middleware's role check in front of *app*
        (``auth._role_refusal``, the one rule it applies), outside every
        middleware added before it: a case's person is the operator a member
        is, never an admin by omission. Returns the list the requests it
        refused go in, ``(method, path)``, so a case can say none was.

        The review of S1-17c: the cases mounted the tenant middleware only,
        so no case met the admin-only routes -- connecting a space's own
        GitHub, mailbox or calendar -- that no member could reach."""
        from kazma_ui.request_path import judged_path

        refused: list[tuple[str, str]] = []

        async def check(request: Any, call_next: Any) -> Any:
            path = judged_path(request)
            # What the auth middleware lets through before any role is read:
            # an open path (a sign-in callback), one that is not sensitive.
            if auth.is_always_open(path) or not auth.is_sensitive_path(path):
                return await call_next(request)
            answer = auth._role_refusal(auth.get_request_principal(request), path, request.method)
            if answer is not None:
                refused.append((request.method, path))
                return answer
            return await call_next(request)

        app.middleware("http")(check)
        return refused

    @contextmanager
    def requests_as(self, who: str) -> Iterator[TestClient]:
        """A client whose requests are signed in as person ``"a"`` or
        ``"b"``, through the real tenant middleware, in front of a handler
        that answers the space and asker it was bound to.

        For checks of the boundary itself. ``client.reached`` lists the paths
        whose handler ran: a refused request is not in it.
        """
        principal = self.principal(who)
        reached: list[str] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(auth, "get_request_principal", lambda request: principal)
            app = FastAPI()
            app.middleware("http")(auth.create_tenant_middleware())

            @app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
            def bound_to(rest: str) -> dict[str, Any]:
                reached.append("/" + rest)
                bound = scope.current_scope()
                return {
                    "space": scope.require_space_id(),
                    "asker": bound.asker if bound else None,
                }

            with TestClient(app) as client:
                client.reached = reached  # type: ignore[attr-defined]
                yield client
