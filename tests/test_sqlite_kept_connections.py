"""A SQLite connection kept across calls cannot be left holding the write lock.

In Python's default transaction mode every INSERT, UPDATE, DELETE and REPLACE
opens a transaction that lasts until a commit -- also when the statement
changes nothing, and also when it raises. On a connection that lives on an
object, a path that misses the commit keeps the database's write lock for as
long as the connection stays idle. The cron store did exactly that from each
boot until the next reminder fired, and the live-data cleanup stopped half
way with "database is locked" (2026-09-27; tests/test_cron_store_write_lock.py).

In autocommit mode (``isolation_level=None``) a statement is its own
transaction, and a longer one is an explicit BEGIN that a reader sees. Every
connection product code keeps -- ``sqlite3.connect`` or ``aiosqlite.connect``
assigned to an attribute or a module global -- is found from the source. One
in the default mode must be declared in ``DEFAULT_MODE_KEPT`` with its reason,
and every write made on it must be committed on every normal path: a commit
after it in the same or an enclosing block, or a ``with conn:`` around it (the
connection's own context manager commits, and rolls back on an error). The
declared list only shrinks.
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

_ONE_WRITE_EACH = (
    "Default mode; each write on it is committed on every normal path "
    "(test_every_write_on_a_default_mode_connection_is_committed)."
)
#: (module, the attribute or global holding the connection) -> why it may stay
#: in the default mode.
DEFAULT_MODE_KEPT: dict[tuple[str, str], str] = {
    ("kazma-core/kazma_core/agent_runner.py", "self._checkpoint_conn"): (
        "LangGraph's AsyncSqliteSaver runs its own statements on this connection "
        "and commits each write itself; the agent only opens and closes it."
    ),
    ("kazma-core/kazma_core/audit_logger.py", "self._db"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/hub/registry.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/memory/dual_write.py", "self._primary"): (
        "The memory writer: an episode's rows, index entries and vector are "
        "written together and committed together. " + _ONE_WRITE_EACH
    ),
    ("kazma-core/kazma_core/memory/dual_write.py", "self._ops"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/observability/llm_ledger.py", "_conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/rbac.py", "self._db"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/security/audit_trail.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/security/certification.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/security/dependency_scanner.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/security/disclosure.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/swarm/memory/pipeline_logger.py", "_conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/swarm/semantic_cache.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/swarm/task_store.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-core/kazma_core/time_travel.py", "self._conn"): _ONE_WRITE_EACH,
    ("kazma-gateway/kazma_gateway/stores/sqlite.py", "self._db"): _ONE_WRITE_EACH,
    ("kazma-ui/kazma_ui/session_manager.py", "self._conn"): (
        "Every write runs inside `with self._conn:`, which commits, or rolls "
        "back on an error."
    ),
    ("kazma-ui/kazma_ui/session_spool.py", "self._conn"): (
        "Every write runs inside `with self._conn:`, which commits, or rolls "
        "back on an error."
    ),
}

_CONNECT = re.compile(r"\b(?:sqlite3|aiosqlite)\.connect$")
_WRITE_SQL = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def _product_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for base in PRODUCT_DIRS:
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts or "tests" in p.parts:
                continue
            out[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8")
    return out


def _connect_call(value: ast.expr | None) -> ast.Call | None:
    node = value.value if isinstance(value, ast.Await) else value
    if isinstance(node, ast.Call) and _CONNECT.search(ast.unparse(node.func)):
        return node
    return None


def kept_connections(src: str) -> list[tuple[str, int, bool]]:
    """``(holder, line, autocommit)`` for each connection the module keeps:
    one assigned to an attribute, a module global, or a name a function
    declares ``global``."""
    tree = ast.parse(src)
    top = {id(n) for n in tree.body}
    globals_of: dict[int, set[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = {n for g in ast.walk(fn) if isinstance(g, ast.Global) for n in g.names}
            for inner in ast.walk(fn):
                globals_of[id(inner)] = names
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        call = _connect_call(node.value)
        if call is None:
            continue
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
            ) or bool(re.search(rf"{re.escape(holder)}\.isolation_level\s*=\s*None\b", src))
            out.append((holder, node.lineno, autocommit))
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


def _is_commit(node: ast.AST) -> bool:
    node = node.value if isinstance(node, ast.Await) else node
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "commit"


def _commits(stmt: ast.stmt) -> bool:
    """Does running ``stmt`` normally always call ``.commit()``?"""
    if isinstance(stmt, ast.Expr):
        return _is_commit(stmt.value)
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return stmt.value is not None and _is_commit(stmt.value)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return any(_commits(s) for s in stmt.body)
    if isinstance(stmt, ast.Try):
        return any(_commits(s) for s in stmt.body) or any(_commits(s) for s in stmt.finalbody)
    return False


def _may_leave(stmt: ast.stmt) -> bool:
    """Can ``stmt`` return, or leave its loop, before what follows it runs?"""
    if isinstance(stmt, (ast.Return, ast.Continue, ast.Break)):
        return True
    for node in ast.walk(stmt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Return, ast.Continue, ast.Break)):
            return True
    return False


_OPENER_CALL = re.compile(r"\b\w*(?:conn|db)\w*\(", re.I)


def _module_holders(src: str, holders: set[str]) -> set[str]:
    """The holders plus each attribute the module copies one into
    (``self._conn = _get_conn()`` in a class that shares a global)."""
    out = set(holders)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
            rhs = ast.unparse(node.value)
            if any(re.search(rf"(?<![\w.]){re.escape(h)}\b", rhs) for h in holders) or _OPENER_CALL.search(rhs):
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


def uncommitted_writes(src: str, holders: set[str]) -> list[str]:
    """``function:line`` of each write on a kept connection after which a
    normal path leaves the function without a commit."""
    tree = ast.parse(src)
    holders = _module_holders(src, holders)
    sql_names = _sql_names(tree)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    found = []
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
                continue  # the caller's connection, the caller's transaction
            if receiver not in names and root not in names and not any(
                receiver.startswith(n + ".") for n in names
            ):
                continue
            if not _committed_after(call, fn, parents, names):
                found.append(f"{fn.name}:{call.lineno}")
    return found


def _committed_after(call: ast.AST, fn: ast.AST, parents: dict, names: set[str]) -> bool:
    child, node = call, parents.get(call)
    while node is not None and node is not fn:
        if isinstance(node, ast.With) and any(ast.unparse(i.context_expr) in names for i in node.items):
            return True  # sqlite3's `with conn:` commits, or rolls back on an error
        if _commit_follows(node, child):
            return True
        child, node = node, parents.get(node)
    return node is fn and _commit_follows(fn, child)


def _commit_follows(block_owner: ast.AST, child: ast.AST) -> bool:
    for field in ("body", "orelse", "finalbody"):
        block = getattr(block_owner, field, None)
        if isinstance(block, list) and child in block:
            for later in block[block.index(child) + 1:]:
                if _commits(later):
                    return True
                if _may_leave(later):
                    return False
            return False
    return False


def test_every_kept_connection_is_autocommit_or_declared():
    found: set[tuple[str, str]] = set()
    undeclared = []
    for rel, src in _product_sources().items():
        for holder, line, autocommit in kept_connections(src):
            if autocommit:
                continue
            found.add((rel, holder))
            if (rel, holder) not in DEFAULT_MODE_KEPT:
                undeclared.append(f"{rel}:{line} {holder}")
    assert undeclared == [], (
        "a SQLite connection kept across calls opens in autocommit mode "
        "(isolation_level=None; a longer transaction is an explicit BEGIN IMMEDIATE ... COMMIT):\n  "
        + "\n  ".join(undeclared)
    )
    stale = sorted(set(DEFAULT_MODE_KEPT) - found)
    assert stale == [], f"declared but no longer a default-mode kept connection; remove: {stale}"


def test_every_write_on_a_default_mode_connection_is_committed():
    sources = _product_sources()
    holders: dict[str, set[str]] = {}
    for rel, holder in DEFAULT_MODE_KEPT:
        holders.setdefault(rel, set()).add(holder)
    left_open = {
        rel: bad for rel, hs in holders.items() if (bad := uncommitted_writes(sources[rel], hs))
    }
    assert left_open == {}, f"a write on a kept default-mode connection without a commit after it: {left_open}"


# The cron store's purge as it was until 2026-09-27, in its class as it opened
# its connection then.
_OLD_CRON_STORE = '''
class SQLiteCronStore:
    async def init(self):
        self._db = await aiosqlite.connect(self._db_path)

    async def purge_terminal_jobs(self, *, older_than_days=14, keep_last=500):
        if self._db is None:
            return 0
        cutoff = "x"
        deleted = 0
        try:
            cursor = await self._db.execute(
                "DELETE FROM cron_jobs WHERE status IN ('done', 'completed', 'failed', 'cancelled') "
                "AND created_at < ?",
                (cutoff,),
            )
            deleted += cursor.rowcount or 0
            cursor2 = await self._db.execute(
                "DELETE FROM cron_jobs WHERE job_id IN (SELECT job_id FROM cron_jobs LIMIT -1 OFFSET ?)",
                (keep_last,),
            )
            deleted += cursor2.rowcount or 0
            if deleted > 0:
                await self._db.commit()
        except Exception:
            pass
        return deleted

    async def update_status(self, job_id, status):
        await self._db.execute("UPDATE cron_jobs SET status = ? WHERE job_id = ?", (status, job_id))
        await self._db.commit()
'''


def test_negative_control_the_old_cron_store_is_caught():
    assert kept_connections(_OLD_CRON_STORE) == [("self._db", 4, False)]
    assert uncommitted_writes(_OLD_CRON_STORE, {"self._db"}) == [
        "purge_terminal_jobs:12", "purge_terminal_jobs:18",
    ]
    fixed = _OLD_CRON_STORE.replace("connect(self._db_path)", "connect(self._db_path, isolation_level=None)")
    assert kept_connections(fixed) == [("self._db", 4, True)]


def test_negative_control_the_commit_rules():
    src = '''
import sqlite3
_CONN = sqlite3.connect("x.db")

def written_then_committed():
    _CONN.execute("INSERT INTO t VALUES (1)")
    _CONN.commit()

def committed_after_the_branch(flag):
    if flag:
        _CONN.execute("DELETE FROM t")
    _CONN.commit()

def returns_before_the_commit(flag):
    _CONN.execute("UPDATE t SET a = 1")
    if flag:
        return
    _CONN.commit()

def inside_the_connection_context():
    with _CONN:
        _CONN.execute("INSERT INTO t VALUES (2)")

def through_an_alias():
    conn = _CONN
    cur = conn.cursor()
    cur.execute("REPLACE INTO t VALUES (3)")

def the_callers_connection(conn):
    conn.execute("INSERT INTO t VALUES (4)")

def keeps_one_in_a_global():
    global _OTHER
    _OTHER = sqlite3.connect("y.db", isolation_level=None)
'''
    assert kept_connections(src) == [("_CONN", 3, False), ("_OTHER", 34, True)]
    assert uncommitted_writes(src, {"_CONN"}) == ["returns_before_the_commit:15", "through_an_alias:27"]
    shared = '''
_conn = None

def _get_conn():
    global _conn
    _conn = sqlite3.connect("z.db")
    return _conn

def record():
    conn = _get_conn()
    conn.execute("INSERT INTO t VALUES (5)")

class Logger:
    def __init__(self):
        self._conn = _get_conn()

    def log(self):
        self._conn.execute("INSERT INTO t VALUES (6)")
'''
    assert kept_connections(shared) == [("_conn", 6, False)]
    assert uncommitted_writes(shared, {"_conn"}) == ["record:11", "log:18"]


def test_negative_control_sql_held_in_a_name():
    """The gateway's session store passes its SQL as module constants; a
    gate that read only literals could not see those writes."""
    src = '''
_UPSERT = "INSERT INTO sessions (thread_id, context) VALUES (?, ?) ON CONFLICT DO UPDATE SET context = excluded.context"
_READ = "SELECT context FROM sessions WHERE thread_id = ?"

class Store:
    async def _ensure_db(self):
        self._db = await aiosqlite.connect("s.db")
        return self._db

    async def put(self, thread_id, context):
        db = await self._ensure_db()
        await db.execute(_UPSERT, (thread_id, context))

    async def put_committed(self, thread_id, context):
        db = await self._ensure_db()
        await db.execute(_UPSERT, (thread_id, context))
        await db.commit()

    async def get(self, thread_id):
        db = await self._ensure_db()
        await db.execute(_READ, (thread_id,))
'''
    assert kept_connections(src) == [("self._db", 7, False)]
    assert uncommitted_writes(src, {"self._db"}) == ["put:12"]
