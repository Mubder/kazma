"""Every place that says whose statements it runs, and how (S4-01, step 0a).

``spaces.row_work`` has three forms. This gate reads them from the product's
source:

* each use of ``install_statements`` (and of ``install_loops``) is declared in
  :data:`INSTALL`, with why no one space is the answer, and nothing is declared
  that is not used. Step 1 of S4-01 takes its install door from this list;
* a form is entered, never only called: in a ``with``, or decorating a plain
  function (a coroutine or a generator runs its body after the block ended);
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

#: The forms, by the name they are called by.
INSTALL_FORMS = {"install_statements", "install_loops"}
FORMS = INSTALL_FORMS | {"statements_of", "work_of"}
#: What answers something to enter: a call inside the ``with``, or kept for one.
RESOLVERS = {"resolve_work_of", "work_of_async"}


@dataclass(frozen=True)
class Use:
    """One use of a form in one function."""

    where: str
    form: str
    how: str  # "with", "decorator" or "call"
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
    if names & {"contextmanager", "asynccontextmanager"}:
        return "context manager"
    if _yields(function):
        return "generator"
    return "coroutine" if isinstance(function, ast.AsyncFunctionDef) else "plain"


def uses_in(source: str, rel: str) -> list[Use]:
    """Every use of a form in *source*."""
    found: list[Use] = []

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
        if isinstance(node, ast.Call) and _name(node) in FORMS and id(node) not in seen:
            found.append(Use(f"{rel}::{'.'.join(stack)}", _name(node), "call", _kind(function), False, node.lineno))
        seen.update(taken)
        for child in ast.iter_child_nodes(node):
            visit(child, stack, function)

    seen: set[int] = set()
    visit(ast.parse(source), [], None)
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
        if use.how == "decorator" and use.function != "plain":
            bad.append(f"{at} decorates a {use.function}: its body runs after the block ended")
        if use.how == "with" and use.yields and use.function == "generator":
            bad.append(f"{at} is held across a yield")
    return bad


def test_every_form_is_entered_and_none_is_held_across_a_yield() -> None:
    assert not _problems(_uses()), "\n".join(_problems(_uses()))


def test_the_installs_statements_are_the_declared_ones() -> None:
    used = {u.where for u in _uses() if u.form in INSTALL_FORMS}
    assert used - set(INSTALL) == set(), "declare these in INSTALL with why no one space is the answer"
    assert set(INSTALL) - used == set(), "declared, and no longer the install's"
    assert all(len(reason.split()) >= 4 for reason in INSTALL.values())


@pytest.mark.parametrize(("source", "said"), [
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
ROW_FORMS = {"work_of", "statements_of", "bind_scope"}


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
