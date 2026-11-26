"""Every connection to Kazma's own Postgres goes through a counted door
(Spaces S4-01, step 1: report mode).

Report mode is the proof that every caller says its door before the lock is
armed, and it proves only what it sees. This gate reads the product's
source, so nothing reaches the database around the counter:

* every place a connection or a pool is made is declared (:data:`MADE`): a
  door's own machinery, or one of the install's own sign-ins (backups,
  restore, setup, upgrade, the outage prober: path 5 of the design), with
  why. A planted ``psycopg.connect`` is found;
* every function that counts a checkout (``space_doors.checkout``) is a door
  the counter knows (``space_doors.DOOR_FUNCTIONS``), names a pool of
  :data:`kazma_core.db.space_doors.POOLS`, and every door made is one; a door
  that stops counting is found;
* every function that checks a connection out of a door is declared
  (:data:`CHECKOUTS`, both ways): the denominator of the report, so
  ``scripts/space_doors_report.py`` can say which were never seen on a live
  install, and which seen there are not here (a spelling this reading
  misses);
* Kazma's own work known to go through no door today is declared
  (:data:`KNOWN_OPEN`), each a real function, with the step that gives it
  one: the report names every other finding new.

The lists are in ``tests/_space_door_lists.py``.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest
from kazma_core.db import space_doors

from tests._space_door_lists import CHECKOUTS, KNOWN_OPEN, MADE

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills")
_FOLDERS = {"kazma_core": "kazma-core", "kazma_ui": "kazma-ui", "kazma_gateway": "kazma-gateway",
            "kazma_cli": "kazma-cli", "kazma_tui": "kazma-tui", "kazma_skills": "kazma-skills"}

#: What makes a connection: ``<driver>...connect`` (``psycopg.connect``,
#: ``psycopg.AsyncConnection.connect``, ``psycopg2.connect``), however the file
#: imported it; and a pool: any ``psycopg_pool`` class ending in ``Pool``.
_DRIVERS = ("psycopg", "psycopg2")
_POOL_MODULE = "psycopg_pool"
#: Pool classes by their own name, for a file that did not import them.
_POOL_CLASSES = {"ConnectionPool", "AsyncConnectionPool", "NullConnectionPool", "AsyncNullConnectionPool"}
#: Kazma's own: answers a pool class (``checkpoints_pg``).
_OWN_POOL_MAKERS = {"_reported_pool_class"}
#: A receiver that is a pool (``pool.execute``, ``self._pool.connection``,
#: ``get_pool().execute``), or a name a pool is kept under in the file.
_POOLISH = re.compile(r"pool|^_?pg$", re.IGNORECASE)
_POOL_GETTERS = {"get_pool", "get_postgres_pool", "_pg_pool", "_get_pool"}


def as_source_name(door: str) -> str:
    """``kazma_core.db.postgres_pool:PostgresPool.connection`` ->
    ``kazma-core/kazma_core/db/postgres_pool.py::PostgresPool.connection``."""
    module, _, qualname = door.partition(":")
    parts = module.split(".")
    return f"{_FOLDERS[parts[0]]}/{'/'.join(parts)}.py::{qualname}"


DOORS = {as_source_name(d) for d in space_doors.DOOR_FUNCTIONS}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _walk(source: str, rel: str) -> Iterable[tuple[str, ast.Call]]:
    """Every call in *source*, with the function it is in (``file::Class.f``)."""
    out: list[tuple[str, ast.Call]] = []

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = [*stack, child.name] if isinstance(
                child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else stack
            if isinstance(child, ast.Call):
                out.append((f"{rel}::{'.'.join(inner)}", child))
            visit(child, inner)

    visit(ast.parse(source), [])
    return out


def _imports(tree: ast.AST) -> dict[str, str]:
    """Each name the file's imports bind (a function's own included) -> the
    dotted name it stands for."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                out[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _dotted(node: ast.AST, imports: dict[str, str]) -> str:
    """``pg.AsyncConnection.connect`` -> ``psycopg.AsyncConnection.connect``
    after ``import psycopg as pg``; ``""`` for anything but a dotted name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(imports.get(node.id, node.id))
    return ".".join(reversed(parts))


def _makes(dotted: str) -> bool:
    head, last = dotted.partition(".")[0], dotted.rpartition(".")[2]
    return (head in _DRIVERS and last == "connect") or (head == _POOL_MODULE and last.endswith("Pool"))


def made_in(source: str, rel: str) -> set[str]:
    """Functions that make a connection or a pool of their own, however the
    file imported what makes it (review M1)."""
    imports = _imports(ast.parse(source))
    found: set[str] = set()
    for where, call in _walk(source, rel):
        func = call.func
        if _makes(_dotted(func, imports)) or _name(func) in _POOL_CLASSES | _OWN_POOL_MAKERS:
            found.add(where)
        elif isinstance(func, ast.Call) and _name(func.func) in _OWN_POOL_MAKERS:
            found.add(where)
        elif isinstance(func, ast.Attribute) and func.attr == "getconn":
            found.add(where)
    return found


def doors_in(source: str, rel: str) -> dict[str, set[str]]:
    """Functions that count a checkout -> the pools they name."""
    found: dict[str, set[str]] = {}
    for where, call in _walk(source, rel):
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "checkout" and _name(func.value) == "space_doors":
            named = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else "?"
            found.setdefault(where, set()).add(str(named))
    return found


def _is_pool(node: ast.AST, kept: set[str] = frozenset()) -> bool:
    if isinstance(node, ast.Call):
        return _name(node.func) in _POOL_GETTERS
    return bool(_POOLISH.search(_name(node))) or _name(node) in kept


def _kept_pools(source: str) -> set[str]:
    """The names (a variable, an attribute) the file keeps a pool under:
    assigned from a pool getter or a ``PostgresPool`` (``self._db =
    get_postgres_pool()``, review M1)."""
    kept: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        value = getattr(node, "value", None)
        if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(value, ast.Call) and \
                _name(value.func) in _POOL_GETTERS | {"PostgresPool"}:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            kept |= {_name(t) for t in targets}
    return kept - {""}


def checkouts_in(source: str, rel: str, doors: set[str] = frozenset(), machinery: set[str] = frozenset()) -> set[str]:
    """Functions that check a connection out of a door: a pool's
    ``connection()`` (any receiver: the gateway and the approvals take the
    checkpointer's pool as ``conn``), a pool's ``execute``/``execute_one``,
    or a door or pass-through of *doors* called in its own file. The doors
    themselves, and their *machinery*, are not."""
    own = {d.rpartition("::")[2].rpartition(".")[2] for d in doors if d.startswith(f"{rel}::")}
    kept = _kept_pools(source)
    found: set[str] = set()
    for where, call in _walk(source, rel):
        if where in doors or where in machinery:
            continue
        func = call.func
        name = _name(func)
        if isinstance(func, ast.Attribute) and name == "connection":
            found.add(where)
        elif isinstance(func, ast.Attribute) and name in ("execute", "execute_one") and _is_pool(func.value, kept):
            found.add(where)
        elif name in own and (isinstance(func, ast.Name) or _name(getattr(func, "value", None)) == "self"):
            found.add(where)
    return found


def _product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *[f"{p}/*.py" for p in PACKAGES]],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if "/tests/" not in line and "_tests/" not in line]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def _machinery() -> set[str]:
    return {where for where, said in MADE.items() if said.startswith("door: ")}


def _all_made() -> set[str]:
    found: set[str] = set()
    for rel in _product_files():
        text = _read(rel)
        if "connect" in text or "Pool" in text or "getconn" in text:
            found |= made_in(text, rel)
    return found


def _all_doors() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for rel in _product_files():
        text = _read(rel)
        if "space_doors" in text:
            found |= doors_in(text, rel)
    return found


def _all_checkouts() -> set[str]:
    found: set[str] = set()
    machinery = _machinery()
    for rel in _product_files():
        text = _read(rel)
        if any(word in text for word in (".connection(", ".execute", "_pg_execute", "_connect(")):
            found |= checkouts_in(text, rel, DOORS, machinery)
    return found


def _declared_checkouts() -> set[str]:
    return {f"{rel}::{function}" for rel, functions in CHECKOUTS.items() for function in functions}


# ── connections and pools ────────────────────────────────────────────────


def test_every_connection_kazma_makes_is_a_door_or_its_own_sign_in() -> None:
    made = _all_made()
    assert made - set(MADE) == set(), "a connection made here: a door's (count it) or an own sign-in (say why)"
    assert set(MADE) - made == set(), "declared, and no longer made there"
    for where, said in MADE.items():
        assert said.startswith(("door: ", "own sign-in: ")), where
        if said.startswith("door: "):
            rel = where.partition("::")[0]
            assert f"{rel}::{said[len('door: '):]}" in DOORS, f"{where}: the door it serves is not a door"
        else:
            assert len(said.split()) >= 8, f"{where}: say whose sign-in it is and why it is outside"


def test_every_door_counts_its_checkouts_and_the_counter_knows_it() -> None:
    doors = _all_doors()
    counting = set(doors)
    passing = DOORS - counting
    assert counting <= DOORS, "a function that counts a checkout must be in space_doors.DOOR_FUNCTIONS"
    for where, pools in doors.items():
        assert pools <= set(space_doors.POOLS), f"{where} names a pool the report does not know: {pools}"
    assert {p for pools in doors.values() for p in pools} == set(space_doors.POOLS), "a pool no door counts"
    for where in passing:
        rel = where.partition("::")[0]
        assert where in checkouts_in(_read(rel), rel, DOORS - {where}), f"{where} passes nothing on to a door"


def test_every_door_made_is_counted() -> None:
    """A pool or connection that is a door's: the door named counts."""
    doors = _all_doors()
    for where, said in MADE.items():
        if said.startswith("door: "):
            rel = where.partition("::")[0]
            assert f"{rel}::{said[len('door: '):]}" in doors, f"{where}: its door counts nothing"


# ── who checks a connection out ──────────────────────────────────────────


def test_every_function_that_checks_a_connection_out_is_declared() -> None:
    found, declared = _all_checkouts(), _declared_checkouts()
    assert found - declared == set(), "declare it in CHECKOUTS (tests/_space_door_lists.py)"
    assert declared - found == set(), "declared, and no longer checks a connection out"


def test_every_caller_known_to_go_through_no_door_says_which_step_gives_it_one() -> None:
    declared = _declared_checkouts()
    for where, (why, step, firsts) in KNOWN_OPEN.items():
        rel, _, function = where.partition("::")
        assert function.split(".") in _defined(_read(rel)), f"{where}: no such function"
        assert len(why.split()) >= 8 and step.startswith(("0b", "step ")), where
        assert firsts and set(firsts) <= declared, f"{where}: its first callers are functions that check out"


def _defined(source: str) -> list[list[str]]:
    """Every function of *source*, by its qualified name's parts."""
    found: list[list[str]] = []

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = [*stack, child.name]
                if not isinstance(child, ast.ClassDef):
                    found.append(inner)
                visit(child, inner)
            else:
                visit(child, stack)

    visit(ast.parse(source), [])
    return found


def test_a_known_open_entry_for_no_function_is_found() -> None:
    source = _read("kazma-core/kazma_core/memory/worker_bootstrap.py")
    assert ["_sync_memory_mirror"] in _defined(source)
    assert ["_sync_the_mirror_renamed"] not in _defined(source)


# ── negative controls ────────────────────────────────────────────────────


@pytest.mark.parametrize("source", [
    "def f(dsn):\n    import psycopg\n    with psycopg.connect(dsn) as c:\n        c.execute('select 1')\n",
    "def f(dsn):\n    return psycopg2.connect(dsn)\n",
    "class S:\n    def open(self):\n        self.pool = ConnectionPool(conninfo='x')\n",
    "async def f():\n    pool = AsyncConnectionPool('x', open=False)\n",
    "def f(p):\n    return p.getconn()\n",
    # The review's (M1): every spelling psycopg allows.
    "async def f(dsn):\n    import psycopg\n    return await psycopg.AsyncConnection.connect(dsn)\n",
    "def f(dsn):\n    import psycopg\n    return psycopg.Connection.connect(dsn)\n",
    "from psycopg import connect\ndef f(dsn):\n    return connect(dsn)\n",
    "import psycopg as pg\ndef f(dsn):\n    return pg.connect(dsn)\n",
    "from psycopg import AsyncConnection as Conn\nasync def f(dsn):\n    return await Conn.connect(dsn)\n",
    "from psycopg_pool import NullConnectionPool\ndef f(dsn):\n    return NullConnectionPool(dsn)\n",
    "def f(dsn):\n    from psycopg_pool import AsyncNullConnectionPool\n    return AsyncNullConnectionPool(dsn)\n",
    "from psycopg_pool import ConnectionPool as Pool\ndef f(dsn):\n    return Pool(dsn)\n",
    "import psycopg_pool as pools\ndef f(dsn):\n    return pools.ConnectionPool(dsn)\n",
])
def test_a_planted_connection_is_found(source: str) -> None:
    assert made_in(source, "x.py") == {"x.py::f"} or made_in(source, "x.py") == {"x.py::S.open"}, source


@pytest.mark.parametrize("source", [
    "import sqlite3\ndef f(path):\n    return sqlite3.connect(path)\n",
    "from psycopg.conninfo import conninfo_to_dict\ndef f(dsn):\n    return conninfo_to_dict(dsn)\n",
    "from psycopg_pool import ConnectionPool\ndef f(conn):\n    return ConnectionPool.check_connection\n",
])
def test_what_makes_no_connection_is_not_found(source: str) -> None:
    assert made_in(source, "x.py") == set(), source


def test_a_door_that_stops_counting_is_found() -> None:
    rel = "kazma-core/kazma_core/db/postgres_pool.py"
    real = _read(rel)
    without = real.replace('space_doors.checkout("shared")', "pass")
    assert without != real
    assert f"{rel}::PostgresPool.connection" in doors_in(real, rel)
    assert f"{rel}::PostgresPool.connection" not in doors_in(without, rel)


def test_a_door_naming_an_unknown_pool_is_found() -> None:
    found = doors_in("def f():\n    space_doors.checkout('a pool of its own')\n", "x.py")
    assert not found["x.py::f"] <= set(space_doors.POOLS)


@pytest.mark.parametrize(("source", "found"), [
    ("def f(self):\n    with self._pool.connection() as c:\n        c.execute('x')\n", True),
    ("def f():\n    return get_pool().execute('x')\n", True),
    ("def f(pool):\n    return pool.execute_one('x')\n", True),
    ("async def f(conn):\n    async with conn.connection() as c:\n        await c.execute('x')\n", True),
    ("def f(conn):\n    return conn.execute('x')\n", False),  # a connection already held (SQLite too)
    ("def f(self):\n    return self.conn.execute('x')\n", False),
])
def test_a_planted_checkout_is_found(source: str, found: bool) -> None:
    assert (checkouts_in(source, "x.py") == {"x.py::f"}) is found


def test_a_pool_kept_under_another_name_is_found() -> None:
    """Review M1: ``self._db.execute`` was missed, the name looks like no pool."""
    source = ("class S:\n    def __init__(self):\n        self._db = get_postgres_pool()\n"
              "    def read(self):\n        return self._db.execute_one('x')\n"
              "def other(store):\n    return store.execute_one('x')\n")
    assert checkouts_in(source, "x.py") == {"x.py::S.read"}


def test_a_pass_through_hides_no_caller() -> None:
    """A store's own helper passes the statement on: its callers are the
    checkouts (the report names them, not the helper)."""
    rel = "kazma-core/kazma_core/swarm/task_store.py"
    source = ("def _pg_execute(sql):\n    return get_pool().execute(sql)\n"
              "class TaskStore:\n    def save(self):\n        _pg_execute('x')\n")
    found = checkouts_in(source, rel, DOORS)
    assert found == {f"{rel}::TaskStore.save"}
    assert checkouts_in(source, rel) == {f"{rel}::_pg_execute"}, "without the declaration: the helper"


# ── the server keeps the report ──────────────────────────────────────────


def test_the_server_reads_the_report_at_its_start_and_keeps_it_at_its_stop() -> None:
    tree = ast.parse(_read("kazma-ui/kazma_ui/app.py"))
    bodies = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}

    def hands_off(function: str, call: str) -> bool:
        return any(isinstance(n, ast.Call) and _name(n.func) == "to_thread" and n.args
                   and _name(n.args[0]) == call for n in ast.walk(bodies[function]))

    assert hands_off("_on_startup", "start")
    assert hands_off("_shutdown_services", "flush")
