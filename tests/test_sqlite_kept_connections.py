"""A SQLite connection kept across calls cannot be left holding the write lock.

In Python's default transaction mode every INSERT, UPDATE, DELETE and REPLACE
opens a transaction that lasts until a commit -- also when the statement
changes nothing, and also when it raises. On a connection that lives on an
object, a path that misses the commit keeps the database's write lock for as
long as the connection sits idle. The cron store did exactly that from each
boot until the next reminder fired, and the live-data cleanup stopped half
way with "database is locked" (2026-09-27; tests/test_cron_store_write_lock.py).
The same shape was on the error path of fourteen more stores -- the memory
writer, the LLM ledger, the semantic cache among them: a write that raised
left its transaction open until the next commit on that connection, and a
store that wrote two rows could commit the first alone at its next write.

Every connection product code keeps -- ``sqlite3.connect`` or
``aiosqlite.connect`` assigned to an attribute, a module global, or a name a
function declares ``global`` -- is found from the source, and it is one of:

- in autocommit mode (``isolation_level=None``): a statement is its own
  transaction, and a group of writes is an explicit ``BEGIN IMMEDIATE`` whose
  error path rolls back (``test_every_explicit_begin_rolls_back_on_error``;
  ``db.sqlite_session.write_transaction`` for aiosqlite);
- a ``sqlite3`` connection every write on which runs inside ``with conn:``,
  whose exit commits, or rolls back when the block raises;
- LangGraph's saver connection, on which Kazma runs no write of its own.

An aiosqlite connection has no such context manager (``async with`` closes
it), so one kept in the default mode is refused.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRODUCT_DIRS = (
    REPO / "kazma-core" / "kazma_core",
    REPO / "kazma-ui" / "kazma_ui",
    REPO / "kazma-gateway" / "kazma_gateway",
    REPO / "kazma-cli" / "kazma_cli",
    REPO / "kazma-tui" / "kazma_tui",
    REPO / "kazma-skills" / "kazma_skills",
)

#: (module, holder) -> why a default-mode aiosqlite connection may be kept.
THIRD_PARTY_KEPT: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/agent_runner.py", "self._checkpoint_conn"): (
        "LangGraph's AsyncSqliteSaver runs its statements on this connection, "
        "under its own lock, and commits each write; Kazma runs no write of "
        "its own on it (the two that bypassed the saver's lock were deleted "
        "2026-09-27)."
    ),
}

_CONNECT = re.compile(r"\b(sqlite3|aiosqlite)\.connect$")
_WRITE_SQL = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
_BEGIN_SQL = re.compile(r"^\s*BEGIN\b", re.I)
_ROLLBACK_SQL = re.compile(r"^\s*ROLLBACK\b", re.I)
#: A call that hands back a store's kept connection: ``_get_conn()``,
#: ``self._get_db()``. Not ``sqlite3.connect(...)``, which opens a new one.
_OPENER_CALL = re.compile(r"\b(?!connect\()\w*(?:conn|db)\w*\(", re.I)


def _product_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for base in PRODUCT_DIRS:
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts or "tests" in p.parts:
                continue
            out[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8")
    return out


def _connect_call(value: ast.expr | None) -> tuple[ast.Call, str] | None:
    node = value.value if isinstance(value, ast.Await) else value
    if isinstance(node, ast.Call):
        m = _CONNECT.search(ast.unparse(node.func))
        if m:
            return node, m.group(1)
    return None


def kept_connections(src: str) -> list[tuple[str, int, bool, str]]:
    """``(holder, line, autocommit, driver)`` for each connection the module
    keeps: one assigned to an attribute, a module global, or a name a
    function declares ``global``."""
    tree = ast.parse(src)
    top = {id(n) for n in tree.body}
    globals_of: dict[int, set[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {n for g in ast.walk(fn) if isinstance(g, ast.Global) for n in g.names}
            for inner in ast.walk(fn):
                globals_of[id(inner)] = names
    # A connection opened into a local first (``db = await aiosqlite.connect(...)``
    # ... ``self._db = db``) is kept by the later assignment.
    opened_into: dict[int, dict[str, tuple[ast.Call, str]]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            locals_ = {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    found = _connect_call(n.value)
                    if found is not None:
                        locals_[n.targets[0].id] = found
            for n in ast.walk(fn):
                opened_into[id(n)] = locals_
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        found = _connect_call(node.value)
        via = None
        if found is None and isinstance(node.value, ast.Name):
            found = opened_into.get(id(node), {}).get(node.value.id)
            via = node.value.id
        if found is None:
            continue
        call, driver = found
        for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
            holder = ast.unparse(target)
            kept = isinstance(target, ast.Attribute) or (
                isinstance(target, ast.Name)
                and (id(node) in top or holder in globals_of.get(id(node), set()))
            )
            if not kept:
                continue
            autocommit = any(
                k.arg == "isolation_level" and isinstance(k.value, ast.Constant) and k.value.value is None
                for k in call.keywords
            ) or any(
                re.search(rf"(?<![\w.]){re.escape(name)}\.isolation_level\s*=\s*None\b", src)
                for name in (holder, via) if name
            )
            out.append((holder, node.lineno, autocommit, driver))
    return out


def _sql(node: ast.expr, names: dict[str, str] | None = None) -> str | None:
    """The start of the SQL an execute call runs: a literal, an f-string, a
    concatenation, or a name bound to one (``_UPSERT = "INSERT ..."``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sql(node.left, names)
    if isinstance(node, ast.Name) and names:
        return names.get(node.id)
    return None


def _sql_names(tree: ast.AST) -> dict[str, str]:
    """Every name the module binds to SQL text, by the start of that text."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            text = _sql(node.value)
            for target in targets:
                if isinstance(target, ast.Name) and text is not None:
                    out.setdefault(target.id, text)
    return out


def _module_holders(src: str, holders: set[str]) -> set[str]:
    """The holders plus each attribute the module copies one into
    (``self._conn = _get_conn()`` in a class that shares a global)."""
    out = set(holders)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
            rhs = ast.unparse(node.value)
            if _connect_call(node.value) is None and (
                any(re.search(rf"(?<![\w.]){re.escape(h)}\b", rhs) for h in holders) or _OPENER_CALL.search(rhs)
            ):
                out.add(ast.unparse(node.targets[0]))
    return out


def _connection_names(fn: ast.AST, holders: set[str]) -> set[str]:
    """The holders plus every local name bound to one of them in ``fn``: an
    alias, a cursor, a ``with ... as`` name, or the result of an opener such
    as ``_get_conn()`` or ``self._get_db()``."""
    names = set(holders)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(fn):
            pairs: list[tuple[str, str]] = []
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                pairs.append((node.targets[0].id, ast.unparse(node.value)))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs += [
                    (item.optional_vars.id, ast.unparse(item.context_expr))
                    for item in node.items
                    if isinstance(item.optional_vars, ast.Name)
                ]
            for name, rhs in pairs:
                if name in names:
                    continue
                if any(re.search(rf"(?<![\w.]){re.escape(h)}\b", rhs) for h in names) or _OPENER_CALL.search(rhs):
                    names.add(name)
                    changed = True
    return names


def _writes_on(src: str, holders: set[str]) -> list[tuple[ast.AST, ast.Call, bool]]:
    """``(function, call, inside with conn:)`` for each data-changing
    statement run on a kept connection -- the holder, an alias, a cursor of
    it. A connection a function is handed is its caller's, judged there."""
    tree = ast.parse(src)
    holders = _module_holders(src, holders)
    sql_names = _sql_names(tree)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = _connection_names(fn, holders)
        params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)} - {"self", "cls"}
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("execute", "executemany") and call.args):
                continue
            sql = _sql(call.args[0], sql_names)
            if sql is None or not _WRITE_SQL.match(sql):
                continue
            owner = parents.get(call)
            while not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                owner = parents.get(owner)
            if owner is not fn:
                continue  # judged in its own function
            receiver = ast.unparse(call.func.value)
            root = re.split(r"[.(\[]", receiver, maxsplit=1)[0]
            if root in params:
                continue
            if receiver in names or root in names or any(receiver.startswith(n + ".") for n in names):
                out.append((fn, call, _inside_connection_context(call, fn, parents, names)))
    return out


def _inside_connection_context(call: ast.AST, fn: ast.AST, parents: dict, names: set[str]) -> bool:
    """Is ``call`` inside ``with conn:`` for one of the connection's names?
    (``async with`` on aiosqlite closes the connection; it is not this.)"""
    node = parents.get(call)
    while node is not None and node is not fn:
        if isinstance(node, ast.With) and any(ast.unparse(i.context_expr) in names for i in node.items):
            return True
        node = parents.get(node)
    return False


def module_problems(rel: str, src: str) -> list[str]:
    """What breaks the rule in one module (empty when it keeps to it)."""
    problems = []
    for holder, line, autocommit, driver in kept_connections(src):
        if autocommit:
            continue
        writes = _writes_on(src, {holder})
        if driver == "aiosqlite":
            if (rel, holder) not in THIRD_PARTY_KEPT:
                problems.append(
                    f"{rel}:{line} {holder}: an aiosqlite connection kept in the default mode -- "
                    "open it with isolation_level=None (a group of writes: db.sqlite_session.write_transaction)"
                )
            elif writes:
                problems += [
                    f"{rel}:{call.lineno} {fn.name}: a write of Kazma's own on {holder}, which only its "
                    "third-party owner may write through"
                    for fn, call, _guarded in writes
                ]
            continue
        problems += [
            f"{rel}:{call.lineno} {fn.name}: a write on {holder} outside `with conn:` -- if it raises, "
            "its transaction stays open with the write lock"
            for fn, call, guarded in writes
            if not guarded
        ]
    return problems


def _returns_committed_and_closed(tree: ast.AST) -> set[str]:
    """Names of the module's functions that hand back ``committed_and_closed``."""
    out = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Call) and ast.unparse(
                    n.value.func
                ).endswith("committed_and_closed"):
                    out.add(fn.name)
    return out


def _rolls_back(statements: list[ast.stmt]) -> bool:
    for stmt in statements:
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if "rollback" in name.lower():
                return True
            if name == "execute" and n.args and (sql := _sql(n.args[0])) and _ROLLBACK_SQL.match(sql):
                return True
    return False


def _sets_a_flag(stmt: ast.stmt) -> bool:
    """``name = <constant>``: set-up that runs nothing."""
    return isinstance(stmt, (ast.Assign, ast.AnnAssign)) and isinstance(stmt.value, ast.Constant)


def _try_rolls_back(node: ast.AST) -> bool:
    return isinstance(node, ast.Try) and (
        any(_rolls_back(h.body) for h in node.handlers) or _rolls_back(node.finalbody)
    )


def begins_without_rollback(src: str) -> list[str]:
    """``function:line`` of each explicit BEGIN whose error path does not end
    the transaction: it runs in the body of a ``try`` that rolls back, or the
    statement right after it is one, or it runs inside a context that ends
    the transaction (``with conn:``, ``committed_and_closed``,
    ``write_transaction``)."""
    tree = ast.parse(src)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    closers = _returns_committed_and_closed(tree)
    closer_call = re.compile(
        r"(?:committed_and_closed|write_transaction)\(|\b(?:" + "|".join(map(re.escape, closers or {"-"})) + r")\("
    )
    found = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute" and call.args):
            continue
        sql = _sql(call.args[0])
        if sql is None or not _BEGIN_SQL.match(sql):
            continue
        receiver = ast.unparse(call.func.value)
        guarded = False
        child, node = call, parents.get(call)
        while node is not None and not guarded:
            if _try_rolls_back(node) and child in node.body:
                guarded = True
            elif isinstance(node, (ast.With, ast.AsyncWith)) and any(
                closer_call.search(ast.unparse(i.context_expr))
                or (isinstance(node, ast.With) and ast.unparse(i.context_expr) == receiver)
                for i in node.items
            ):
                guarded = True
            else:
                for field in ("body", "orelse", "finalbody"):
                    block = getattr(node, field, None)
                    if isinstance(block, list) and child in block:
                        # The next statement past any flag set-up (`committed = False`).
                        after = [s for s in block[block.index(child) + 1:] if not _sets_a_flag(s)][:1]
                        guarded = bool(after) and _try_rolls_back(after[0])
                        break
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                break
            child, node = node, parents.get(node)
        if not guarded:
            fn = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            found.append(f"{fn}:{call.lineno}")
    return found


def test_every_kept_connection_ends_its_transactions():
    sources = _product_sources()
    problems = [p for rel, src in sources.items() for p in module_problems(rel, src)]
    assert problems == [], "\n".join(problems)


def test_the_exemptions_are_still_there():
    sources = _product_sources()
    for rel, holder in THIRD_PARTY_KEPT:
        kept = {(h, driver, autocommit) for h, _line, autocommit, driver in kept_connections(sources[rel])}
        assert (holder, "aiosqlite", False) in kept, f"{rel} {holder}: gone or changed; remove the exemption"


def test_the_gate_reads_the_stores_it_judges():
    """Not blind: the default-mode sqlite3 stores' writes are found, and each
    one of them is judged."""
    sources = _product_sources()
    kept = {
        rel: {h for h, _line, autocommit, driver in kept_connections(src) if not autocommit and driver == "sqlite3"}
        for rel, src in sources.items()
    }
    counted = {rel: len(_writes_on(sources[rel], hs)) for rel, hs in kept.items() if hs}
    assert sum(counted.values()) >= 15, counted
    for rel in ("kazma-core/kazma_core/memory/dual_write.py", "kazma-core/kazma_core/security/disclosure.py",
                "kazma-ui/kazma_ui/session_manager.py"):
        assert counted.get(rel), f"{rel}: no write found on its kept connection -- the gate went blind"


def test_every_explicit_begin_rolls_back_on_error():
    found = {rel: bad for rel, src in _product_sources().items() if "BEGIN" in src and (bad := begins_without_rollback(src))}
    assert found == {}, f"an explicit BEGIN whose error path leaves the transaction open: {found}"


# The cron store as it was until 2026-09-27: its connection in the default
# mode, and a purge that committed only when it had deleted something.
_OLD_CRON_STORE = '''
class SQLiteCronStore:
    async def init(self):
        self._db = await aiosqlite.connect(self._db_path)

    async def purge_terminal_jobs(self, *, older_than_days=14, keep_last=500):
        deleted = 0
        cursor = await self._db.execute("DELETE FROM cron_jobs WHERE status = 'done' AND created_at < ?", ("x",))
        deleted += cursor.rowcount or 0
        if deleted > 0:
            await self._db.commit()
        return deleted
'''


def test_negative_control_the_old_cron_store_is_refused():
    assert kept_connections(_OLD_CRON_STORE) == [("self._db", 4, False, "aiosqlite")]
    problems = module_problems("kazma-core/kazma_core/cron/scheduler.py", _OLD_CRON_STORE)
    assert len(problems) == 1 and "aiosqlite connection kept in the default mode" in problems[0]
    fixed = _OLD_CRON_STORE.replace("connect(self._db_path)", "connect(self._db_path, isolation_level=None)")
    assert module_problems("kazma-core/kazma_core/cron/scheduler.py", fixed) == []


def test_negative_control_a_sqlite3_store_writes_inside_its_connection():
    src = '''
import sqlite3
_CONN = sqlite3.connect("x.db")
_UPSERT = "INSERT INTO t VALUES (?)"

def committed_but_not_guarded():
    _CONN.execute("INSERT INTO t VALUES (1)")
    _CONN.commit()

def guarded():
    with _CONN:
        _CONN.execute("DELETE FROM t")

def guarded_with_a_lock(lock):
    with lock, _CONN:
        _CONN.execute("UPDATE t SET a = 1")

def through_an_alias():
    conn = _CONN
    with conn:
        cur = conn.cursor()
        cur.execute("REPLACE INTO t VALUES (3)")

def sql_held_in_a_name():
    _CONN.execute(_UPSERT, (4,))
    _CONN.commit()

def the_callers_connection(conn):
    conn.execute("INSERT INTO t VALUES (5)")

def a_new_connection_is_not_the_kept_one():
    conn = sqlite3.connect("y.db")
    conn.execute("INSERT INTO t VALUES (6)")
    conn.commit()
    conn.close()
'''
    assert kept_connections(src) == [("_CONN", 3, False, "sqlite3")]
    problems = module_problems("kazma-core/kazma_core/x.py", src)
    assert [p.split(" ")[1] for p in problems] == ["committed_but_not_guarded:", "sql_held_in_a_name:"], problems


def test_negative_control_the_third_party_connection_takes_no_write_of_ours():
    src = '''
class Agent:
    async def open(self):
        self._checkpoint_conn = await aiosqlite.connect(path)

    async def summary(self):
        cur = await self._checkpoint_conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
        return await cur.fetchall()
'''
    rel = "kazma-core/kazma_core/agent_runner.py"
    assert module_problems(rel, src) == []
    bypass = src + '''
    async def delete_thread(self, thread_id):
        await self._checkpoint_conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        await self._checkpoint_conn.commit()
'''
    problems = module_problems(rel, bypass)
    assert len(problems) == 1 and "delete_thread" in problems[0]


def test_negative_control_the_begin_rules():
    src = '''
def no_rollback(conn):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.execute("COMMIT")

def rolls_back_in_its_try(conn):
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

def rolls_back_right_after(conn):
    conn.execute("BEGIN")
    try:
        conn.execute("INSERT INTO t VALUES (1)")
    finally:
        conn.rollback()

def a_named_rollback(self):
    with self._lock:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute("COMMIT")
        except Exception:
            self._rollback_if_needed()
            raise

def _connect(self):
    return committed_and_closed(sqlite3.connect("x.db"))

def in_a_closing_context(self):
    with self._lock, self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (1)")

def in_its_connection(conn):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (1)")

def a_try_that_does_not_roll_back(conn):
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO t VALUES (1)")
    except Exception:
        pass
'''
    assert begins_without_rollback(src) == ["no_rollback:3", "a_try_that_does_not_roll_back:47"]


def test_negative_control_a_connection_kept_through_a_local():
    """RBAC opens into a local and publishes it once seeded; the gate follows
    the connection to the attribute that keeps it."""
    src = '''
class Store:
    async def _get_db(self):
        db = await aiosqlite.connect(self.path)
        await db.execute("PRAGMA foreign_keys=ON")
        self._db = db
        return self._db
'''
    rel = "kazma-core/kazma_core/x.py"
    assert kept_connections(src) == [("self._db", 6, False, "aiosqlite")]
    assert len(module_problems(rel, src)) == 1
    fixed = src.replace("connect(self.path)", "connect(self.path, isolation_level=None)")
    assert kept_connections(fixed) == [("self._db", 6, True, "aiosqlite")]
    assert module_problems(rel, fixed) == []
