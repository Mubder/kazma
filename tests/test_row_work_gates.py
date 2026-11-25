"""Every place that says whose statements it runs, and how (S4-01, step 0a).

This gate reads ``spaces.row_work``'s forms from the product's source:

* each use of ``install_statements`` (and of ``install_loops``) is declared in
  :data:`INSTALL`, with why no one space is the answer, and nothing is declared
  that is not used. Step 1 of S4-01 takes its install door from this list;
* each use of a form that names a space is declared in :data:`ROWS`, with
  where that space comes from: never an id a request brought (H6);
* the forms that fall back to a space's own account are Kazma's own work only;
  a person's own act (a move) binds that person or refuses (M1);
* a form is entered, never only called or passed about: in a ``with``, or
  decorating a plain function (a coroutine or a generator runs its body after
  the block ended). What a resolver answers is entered where it was asked;
* none is held across a ``yield`` (M5): in a generator the variable is in
  force in the consumer between rows.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills")
ROW_WORK = "kazma-core/kazma_core/spaces/row_work.py"

#: The install's own statements: ``file::function`` -> why no one space is the
#: answer. A name is a function, and everything it calls is the install's.
INSTALL: dict[str, str] = {
    "kazma-core/kazma_core/checkpoints_pg.py::open_postgres_checkpointer":
        "the checkpoint tables are made once, for every space",
    "kazma-core/kazma_core/db/pg_backup.py::verify_required_pg_tables":
        "which tables the database has, no row of any space",
    "kazma-core/kazma_core/documents/audit.py::DocumentAuditStore._ensure_pg_guard":
        "the audit's trigger, one for every space's rows",
    "kazma-core/kazma_core/documents/admission.py::IntakeAdmissionStore._transaction":
        "one queue, one mutex and one set of budgets, counted over every space",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._storage_bytes":
        "the install's totals, of every space together",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.run_maintenance":
        "the collection's audit row is the install's, under no space",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.start_workers":
        "loops that serve every space, whoever's turn started them",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository._ensure_schema":
        "the job tables are made once, for every space",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.claim_next":
        "the fair claim reads every space's queue; the job's stages then run in its own",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.interrupted_intake_jobs":
        "lists every space's jobs; each is then recovered in its own",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.queue_stats":
        "the one queue's depth, which an upload's admission counts over every space",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.recover_expired_leases":
        "leases that ran out, whoever's jobs they hold",
    "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.release_leases":
        "a stopping worker's leases, whoever's jobs they hold",
    "kazma-core/kazma_core/documents/repository_pg.py::PostgresDocumentRepository._ensure_schema":
        "the document tables are made once, for every space",
    "kazma-core/kazma_core/documents/repository_pg.py::PostgresDocumentRepository.active_index_entries":
        "lists what every space must find; each entry is then put back in its own",
    "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector.collect":
        "what any space refers to decides what may go",
    "kazma-core/kazma_core/memory/health.py::build_memory_health":
        "a ping only: the probes round it read the owner's memory as before",
    "kazma-core/kazma_core/runs/adapters/documents.py::_DocumentAdapter._read":
        "the supervisor's list of every space's jobs, with nothing bound only",
    "kazma-core/kazma_core/security/service_exposure.py::_role_answer":
        "the role that signed in, no row of any space",
    "kazma-ui/kazma_ui/health.py::check_database":
        "a ping: whether the database answers, no row of any space",
}

#: A form that names a space: ``file::function`` -> where the space comes from.
#: ``statements:`` names the row, ``Kazma's own work:`` may act for the space's
#: own account, ``a person's act:`` is that person's or refused.
ROWS: dict[str, str] = {
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._move_locked":
        "a person's act: the space of theirs they move into, asked again under the lock",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._put_back_in_the_index":
        "Kazma's own work: the entry the documents store lists, as its owner",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._recover_stranded_intakes":
        "statements: the reservation's own tenant",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._settle_one_move":
        "Kazma's own work: the journal's two spaces, the copy's taking back in its own",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._settle_unrecorded":
        "Kazma's own work: the entry the library holds",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService._still_there":
        "statements: a move's journal, or its own two spaces",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.move_document":
        "statements: the audit row of each of the move's two spaces, whose rule was asked",
    "kazma-core/kazma_core/documents/ingestion.py::DocumentIngestionService.recover_interrupted_intake":
        "Kazma's own work: the job's own tenant and person",
    "kazma-core/kazma_core/documents/telemetry.py::tenant_quota_snapshot":
        "statements: one space's own total, which the caller's route allows (a number, no row)",
    "kazma-core/kazma_core/documents/worker.py::DocumentWorker._not_worked_on":
        "statements: the claimed job's own tenant",
    "kazma-core/kazma_core/documents/worker.py::DocumentWorker._process_claimed":
        "Kazma's own work: the claimed job's own tenant and person",
    "kazma-core/kazma_core/runs/adapters/documents.py::_DocumentAdapter._read":
        "statements: the listed job's own tenant, with nothing bound only",
    "kazma-core/kazma_core/spaces/people.py::_after_leaving":
        "Kazma's own work: the space whose membership change just took effect",
    "kazma-core/kazma_core/spaces/space_limits.py::_documents_used":
        "statements: a space the admin's page, or the space's own, counts (a number, no row)",
    "kazma-ui/kazma_ui/health.py::_check_memory_recall":
        "statements: the owner's space, the canary's",
}

#: Who asks for the account a space's own work acts for: ``file::function`` ->
#: why it is never a person's own act.
ACTS_FOR_A_SPACE: dict[str, str] = {
    "kazma-core/kazma_core/cron/scheduler.py::_acting_account": "whom a job that names nobody acts for",
    "kazma-core/kazma_core/memory/task_queue.py::_persons_binding": "a queued memory task of a person who left",
    "kazma-core/kazma_core/memory/task_queue.py::_task_binding": "a queued memory task that names nobody",
    "kazma-core/kazma_core/spaces/identity.py::work_scope": "queued work that names nobody (a person who left is refused)",
    "kazma-core/kazma_core/spaces/row_work.py::resolve_work_of": "Kazma's own work for a row, declared in ROWS",
    "kazma-core/kazma_core/swarm/single_operator.py::_acts_for": "who may stop a task of a person who left",
    "kazma-ui/kazma_ui/hitl_answerers.py::whose_turn": "whose card a thread that names nobody shows",
    "kazma-ui/kazma_ui/runs_chat_turn.py::_acting": "whose turn a thread that names nobody ran",
}

#: The forms, by the name they are called by.
INSTALL_FORMS = {"install_statements", "install_loops"}
#: What answers something to enter: a call inside the ``with``, or kept for one.
RESOLVERS = {"resolve_work_of", "resolve_act_of", "work_of_async"}
#: The forms that may act for a space's own account.
FALLS_BACK = {"work_of", "resolve_work_of", "work_of_async"}
ROW_FORMS_DECLARED = FALLS_BACK | {"statements_of", "resolve_act_of"}
FORMS = INSTALL_FORMS | {"statements_of", "work_of"}


@dataclass(frozen=True)
class Use:
    """One use of a form in one function."""

    where: str
    form: str
    how: str  # "with", "decorator", "kept", "call" or "passed"
    function: str  # "plain", "coroutine", "generator", "context manager", "module"
    yields: bool
    line: int


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _own(node: ast.AST) -> list[ast.AST]:
    """The nodes of *node*'s own body, not of a function inside it."""
    out: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(node))
    while todo:
        child = todo.pop()
        out.append(child)
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            todo.extend(ast.iter_child_nodes(child))
    return out


def _yields(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Yield | ast.YieldFrom) for n in _own(node))


def _kind(function: ast.AST | None) -> str:
    if function is None:
        return "module"
    names = {_name(d) for d in function.decorator_list}
    if names & {"contextmanager"}:
        return "context manager"  # entered and left in the caller's own context
    if _yields(function):
        return "generator"
    return "coroutine" if isinstance(function, ast.AsyncFunctionDef) else "plain"


def _kept_and_entered(call: ast.Call, function: ast.AST | None) -> bool:
    """Whether what a resolver answered is returned, or kept under a name a
    ``with`` of the same function enters."""
    if function is None:
        return False
    own = _own(function)
    if any(isinstance(n, ast.Return) and n.value is not None and call in ast.walk(n.value) for n in own):
        return True
    names = {t.id for n in own if isinstance(n, ast.Assign) and call in ast.walk(n.value)
             for t in n.targets if isinstance(t, ast.Name)}
    return any(isinstance(c, ast.Call) and _name(c) in names
               for n in own if isinstance(n, ast.With | ast.AsyncWith)
               for item in n.items for c in ast.walk(item.context_expr))


def uses_in(source: str, rel: str) -> list[Use]:
    """Every use of a form in *source*."""
    found: list[Use] = []
    every = FORMS | RESOLVERS
    tree = ast.parse(source)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    def visit(node: ast.AST, stack: list[str], function: ast.AST | None) -> None:
        taken: set[int] = set()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            stack = [*stack, node.name]
            if not isinstance(node, ast.ClassDef):
                function = node
                for mark in node.decorator_list:
                    if isinstance(mark, ast.Call) and _name(mark) in FORMS:
                        taken.add(id(mark))
                        found.append(Use(f"{rel}::{'.'.join(stack)}", _name(mark), "decorator",
                                         _kind(node), _yields(node), mark.lineno))
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                for call in ast.walk(item.context_expr):
                    if isinstance(call, ast.Call) and _name(call) in FORMS | RESOLVERS:
                        taken.add(id(call))
                        found.append(Use(f"{rel}::{'.'.join(stack)}", _name(call), "with",
                                         _kind(function), _yields(node), node.lineno))
        here = f"{rel}::{'.'.join(stack)}"
        if isinstance(node, ast.Call) and _name(node) in every and id(node) not in seen:
            kept = _name(node) in RESOLVERS and _kept_and_entered(node, function)
            found.append(Use(here, _name(node), "kept" if kept else "call", _kind(function),
                             function is not None and _yields(function), node.lineno))
        if isinstance(node, ast.Name | ast.Attribute) and _name(node) in every and id(node) not in called \
                and isinstance(node.ctx, ast.Load):
            found.append(Use(here, _name(node), "passed", _kind(function), False, node.lineno))
        if isinstance(node, ast.ImportFrom):
            found.extend(Use(here, a.name, "passed", _kind(function), False, node.lineno)
                         for a in node.names if a.name in every and a.asname not in (None, a.name))
        seen.update(taken)
        for child in ast.iter_child_nodes(node):
            visit(child, stack, function)

    seen: set[int] = set()
    visit(tree, [], None)
    return found


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *[f"{p}/*.py" for p in PACKAGES]],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if "/tests/" not in line and "_tests/" not in line]


def _uses() -> list[Use]:
    found: list[Use] = []
    for rel in _product_files():
        if rel == ROW_WORK:
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if any(form in text for form in FORMS | RESOLVERS):
            found += uses_in(text, rel)
    return found


def _problems(uses: list[Use]) -> list[str]:
    bad: list[str] = []
    for use in uses:
        at = f"{use.where}:{use.line}: {use.form}"
        if use.how == "call":
            bad.append(f"{at} is called and never entered")
        if use.how == "passed":
            bad.append(f"{at} is named without being called, or under another name")
        if use.how == "decorator" and use.function != "plain":
            bad.append(f"{at} decorates a {use.function}: its body runs after the block ended")
        if use.how in ("with", "kept") and use.yields and use.function == "generator":
            bad.append(f"{at} is held across a yield")
    return bad


def test_every_form_is_entered_and_none_is_held_across_a_yield() -> None:
    assert not _problems(_uses()), "\n".join(_problems(_uses()))


def test_the_installs_statements_are_the_declared_ones() -> None:
    used = {u.where for u in _uses() if u.form in INSTALL_FORMS}
    assert used - set(INSTALL) == set(), "declare these in INSTALL with why no one space is the answer"
    assert set(INSTALL) - used == set(), "declared, and no longer the install's"
    assert all(len(reason.split()) >= 4 for reason in INSTALL.values())


def _reason_problems(uses: list[Use], rows: dict[str, str]) -> list[str]:
    """A form used for another reason than its function declares."""
    bad: list[str] = []
    for use in uses:
        reason = rows.get(use.where, "")
        if use.form in FALLS_BACK and not reason.startswith("Kazma's own work:"):
            bad.append(f"{use.where}: {use.form} may act for the space's own account")
        if use.form == "resolve_act_of" and not reason.startswith("a person's act:"):
            bad.append(f"{use.where}: {use.form} is a person's own act")
        name = use.where.rsplit(".", 1)[-1]
        if use.form in FALLS_BACK and "move" in name and "settle" not in name:
            bad.append(f"{use.where}: a move is a person's own act, whatever it is declared as")
    return bad


def test_every_form_that_names_a_space_is_declared_with_where_it_comes_from() -> None:
    used = [u for u in _uses() if u.form in ROW_FORMS_DECLARED]
    where = {u.where for u in used}
    assert where - set(ROWS) == set(), "declare these in ROWS with where the space comes from"
    assert set(ROWS) - where == set(), "declared, and no longer there"
    kinds = ("statements:", "Kazma's own work:", "a person's act:")
    assert all(r.startswith(kinds) and len(r.split()) >= 5 for r in ROWS.values())
    assert _reason_problems(used, ROWS) == []


@pytest.mark.parametrize(("form", "reason"), [
    ("work_of", "statements: the id the request brought"),
    ("resolve_work_of", "a person's act: the space they move into"),
    ("resolve_act_of", "Kazma's own work: the space they move into"),
    ("resolve_work_of", "Kazma's own work: a move that says it is not one"),
])
def test_a_planted_persons_act_that_falls_back_is_found(form: str, reason: str) -> None:
    uses = uses_in(f"def move_page(a):\n    with {form}(a, 'omar')():\n        go()\n", "x.py")
    assert _reason_problems(uses, {"x.py::move_page": reason})


def test_a_planted_route_with_a_requests_id_is_seen() -> None:
    """The review's control: a route that took its space from the request."""
    uses = uses_in("async def route(request):\n    with statements_of(request.query_params['space']):\n"
                   "        return await read()\n", "x.py")
    assert [u.where for u in uses] == ["x.py::route"] and uses[0].where not in ROWS


def _callers_of(name: str) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.Call) and _name(child) == name:
                found.add(f"{rel}::{'.'.join(inner)}")
            visit(child, inner, rel)

    for rel in _product_files():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if name in text:
            visit(ast.parse(text), [], rel)
    return found


def test_who_acts_for_a_space_is_declared() -> None:
    """The other fallback to a space's own account: each caller is declared."""
    callers = _callers_of("acting_account")
    assert callers - set(ACTS_FOR_A_SPACE) == set(), "whose act is it? a person's own is theirs or refused"
    assert set(ACTS_FOR_A_SPACE) - callers == set(), "declared, and no longer there"


@pytest.mark.parametrize(("source", "said"), [
    ("async def f():\n    enter = resolve_work_of('a')\n    with enter():\n        yield 1\n", "held across a yield"),
    ("def f():\n    enter = resolve_work_of('a')\n    go()\n", "never entered"),
    ("async def f():\n    await work_of_async('a')\n    go()\n", "never entered"),
    ("def f():\n    cm = statements_of('a')\n    with cm:\n        yield 1\n", "never entered"),
    ("@asynccontextmanager\nasync def f():\n    with statements_of('a'):\n        yield\n", "held across a yield"),
    ("from kazma_core.spaces.row_work import statements_of as of\n", "another name"),
    ("def f():\n    form = row_work.statements_of\n    with form('a'):\n        go()\n", "without being called"),
    ("def f():\n    run(functools.partial(work_of, 'a'))\n", "without being called"),
    ("def f():\n    install_statements('a ping')\n    ping()\n", "never entered"),
    ("def f():\n    g(statements_of(space))\n", "never entered"),
    ("@install_statements('x')\nasync def f():\n    await g()\n", "decorates a coroutine"),
    ("@row_work.install_statements('x')\ndef f():\n    yield 1\n", "decorates a generator"),
    ("async def f():\n    with statements_of('a'):\n        async for x in g():\n            yield x\n",
     "held across a yield"),
    ("def f():\n    with work_of('a'):\n        yield from g()\n", "held across a yield"),
    ("async def f():\n    with (await work_of_async('a'))():\n        yield 1\n", "held across a yield"),
])
def test_a_planted_wrong_use_is_found(source: str, said: str) -> None:
    problems = _problems(uses_in(source, "x.py"))
    assert len(problems) == 1 and said in problems[0], problems


@pytest.mark.parametrize("source", [
    "def f():\n    with statements_of('a'):\n        return g()\n",
    "def f():\n    with (bind_scope('a', 'b') if on() else statements_of('a')):\n        return g()\n",
    "def f():\n    enter = resolve_act_of('a', 'b')\n    with open(x) as s, enter():\n        return g(s)\n",
    "async def f():\n    enter = await work_of_async('a')\n    with enter():\n        await g()\n",
    "def f():\n    return resolve_work_of('a')\n",
    "@install_statements('x')\ndef f():\n    return 1\n",
    "async def f():\n    with statements_of('a'):\n        await g()\n",
    "@contextmanager\ndef f():\n    with statements_of('a'):\n        yield\n",
    # the yield is another function's
    "def f():\n    with statements_of('a'):\n        def g():\n            yield 1\n        return list(g())\n",
])
def test_a_right_use_is_not_a_finding(source: str) -> None:
    uses = uses_in(source, "x.py")
    assert len(uses) == 1 and not _problems(uses)
    assert uses[0].where == "x.py::f"


def test_an_undeclared_install_statement_is_seen() -> None:
    uses = uses_in("class A:\n    @install_statements('x')\n    def f(self):\n        return 1\n", "x.py")
    assert [u.where for u in uses] == ["x.py::A.f"]


# ── a loop over rows of several spaces enters one per row ────────────────

#: What answers rows of every space: the store method, or the function.
LISTINGS = {"interrupted_intake_jobs", "recent_jobs", "unpublished", "fenced_recent", "active_index_entries",
            "document_index_entries", "_moves_under_way"}

#: A loop over such rows that runs nothing for a row: ``file::function`` ->
#: what it does instead.
NO_ROW_WORK: dict[str, str] = {}

#: What a row's work is entered by.
ROW_FORMS = ROW_FORMS_DECLARED | {"bind_scope"}


def loops_without_a_space(source: str, rel: str) -> set[str]:
    """Functions of *source* that loop over a listing and enter no row's space
    in the loop's body, nor in a method of theirs the body calls."""
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    found: set[str] = set()

    def enters(nodes: list[ast.stmt], seen: frozenset[str] = frozenset()) -> bool:
        for node in (n for stmt in nodes for n in ast.walk(stmt)):
            if isinstance(node, ast.Call):
                name = _name(node)
                if name in ROW_FORMS:
                    return True
                if name in functions and name not in seen and enters(functions[name].body, seen | {name}):
                    return True
        return False

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                own = _own(child)
                listed = {t.id for n in own if isinstance(n, ast.Assign) and _lists(n.value)
                          for t in n.targets if isinstance(t, ast.Name)}
                for loop in own:
                    if isinstance(loop, ast.For | ast.AsyncFor) and (
                            _lists(loop.iter) or listed & {n.id for n in ast.walk(loop.iter) if isinstance(n, ast.Name)}):
                        if not enters(loop.body):
                            found.add(f"{rel}::{'.'.join(inner)}")
            visit(child, inner)

    visit(tree, [])
    return found


def _lists(expr: ast.AST) -> bool:
    """Whether *expr* calls a listing, or reads one by its name (``getattr``)."""
    return any((isinstance(n, ast.Call) and _name(n) in LISTINGS)
               or (isinstance(n, ast.Constant) and n.value in LISTINGS) for n in ast.walk(expr))


def test_a_loop_over_every_spaces_rows_enters_one_per_row() -> None:
    found: set[str] = set()
    for rel in _product_files():
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if any(name in text for name in LISTINGS):
            found |= loops_without_a_space(text, rel)
    assert found - set(NO_ROW_WORK) == set(), "enter work_of or statements_of for each row"
    assert set(NO_ROW_WORK) - found == set(), "declared, and no longer so"


@pytest.mark.parametrize(("source", "found"), [
    ("def f(s):\n    for j in s.interrupted_intake_jobs():\n        s.get(j)\n", True),
    ("def f(s):\n    rows = s.unpublished()\n    for r in sorted(rows):\n        s.claim(r)\n", True),
    ("def f(s):\n    rows = getattr(s, 'unpublished', None)\n    for r in rows():\n        s.claim(r)\n", True),
    ("def f(s):\n    with work_of('a'):\n        for r in s.unpublished():\n            s.claim(r)\n", True),
    ("def f(s):\n    for r in s.unpublished():\n        with statements_of(r.t):\n            s.claim(r)\n", False),
    ("def f(s):\n    for r in s.unpublished():\n        one(r)\ndef one(r):\n    with work_of(r.t):\n        go()\n", False),
    ("def f(s):\n    for r in s.mine():\n        s.claim(r)\n", False),
])
def test_a_planted_loop_is_found(source: str, found: bool) -> None:
    assert loops_without_a_space(source, "x.py") == ({"x.py::f"} if found else set())
