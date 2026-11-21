"""Count, export and erase one space, store by store (Spaces S1-27).

Every store, family and data-folder entry already says how it keeps a space
apart (``store_registry.SPACE_RULES``). This module acts on it:

* every table of every ``row`` store says how its rows are found for one
  space (:data:`TABLE_RULES`, else a space column, else its thread): by a
  space column (``tenant_id``, ``tenant``, ``space``, ``space_id``), by the
  space's threads (``thread_scopes.db``), by a field of a JSON column, by the
  ids of rows found before it (a summary's sources, a job's events), or kept
  with the reason it holds no space's data. A table that says none of these
  is refused (:class:`SpaceDataRefused` ``unknown_table``): an erase never
  skips a table it cannot read, and ``tests/test_space_export_erase.py`` fails
  on one;
* the settings (``tenant.<id>.`` keys, the mappings kept by space, a chat's
  state keyed by its thread) and the vault's secrets (the space's rows and
  names) through their own stores, so a cache never serves a deleted value;
* the space's folders (``spaces.files``) and its documents' files;
* its Knowledge libraries through ``knowledge_lifecycle.delete_library``,
  which also drops their vectors and fetched pages;
* last, the space and its memberships leave the identity store, recorded.

:func:`count_space` answers how many rows and files each store holds for the
space (never a word of them); :func:`export_space` writes them to a zip;
:func:`erase_space` removes them. Who may call which is the routes' rule
(``kazma_ui.spaces_api``): the admin counts, the space's owner exports and
erases, the admin erases the closed space of a deleted person (plan 4.10).

**Not yet:** the shared-state tables of a Postgres install (chats,
checkpoints, settings, swarm tasks, documents, the memory mirror;
``db.pg_backup.KAZMA_PG_TABLES``), and memory's Postgres copy, which never
deletes a row only it holds. On such an install every call refuses
(``postgres_not_yet``) rather than act on half of a space. The owner's
personal space is never erased (it holds the install's own data, and rows
written before Spaces with no space at all).

Blocking: call it off the event loop.
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import sqlite3
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, valid_space_id

__all__ = [
    "SpaceDataRefused",
    "TABLE_RULES",
    "count_space",
    "erase_space",
    "export_space",
]

logger = logging.getLogger(__name__)

#: The columns that name a row's space, in the order they are looked for.
_SPACE_COLUMNS = ("tenant_id", "tenant", "space", "space_id")
#: FTS5's own tables beside a virtual table; purged, never read.
_FTS_SHADOWS = ("_data", "_idx", "_content", "_docsize", "_config")
_CHUNK = 400


class SpaceDataRefused(ValueError):
    """Nothing was counted, exported or erased; ``code`` says why."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class TableRule:
    """How one table's rows are found for a space.

    ``how``: ``column`` (*column* = the space id), ``thread`` (``thread_id``
    among the space's threads), ``json`` (*path* of the JSON in *column* =
    the space id), ``ids`` (*column* among the ids :func:`_context` found
    under *ids*), ``follows`` (an FTS5 index kept by its table's triggers:
    nothing to find, its words are purged after), ``keep`` (holds no space's
    data; *why* says so).
    """

    how: str
    column: str = ""
    path: str = ""
    ids: str = ""
    why: str = ""


def _keep(why: str) -> TableRule:
    return TableRule("keep", why=why)


def _ids(column: str, ids: str) -> TableRule:
    return TableRule("ids", column=column, ids=ids)


def _json(column: str, path: str) -> TableRule:
    return TableRule("json", column=column, path=path)


#: The tables a space column or a thread id does not answer. ``*`` for every
#: store that holds the table.
TABLE_RULES: dict[tuple[str, str], TableRule] = {
    # memory: the queue names its tenant inside the payload; derived tables
    # follow the rows they were made from.
    ("*", "memory_task_queue"): _json("payload_json", "$.tenant_id"),
    ("*", "memory_summary_sources"): _ids("summary_id", "summaries"),
    # The copies' queues hold ids only; the sync needs what the erase's own
    # deletes queue to take the space's rows out of the graph copy.
    ("*", "memory_mirror_outbox"): _keep("ids only: what the Postgres copy is sent next"),
    ("*", "memory_graph_outbox"): _keep("ids only: what the Neo4j copy is sent next (the erased facts' edges go)"),
    ("*", "memory_forget_applied"): _keep("the install's forget journal marks: sequence numbers only"),
    ("*", "memory_reconcile_state"): _keep("the install's reconcile cursors, by chat store row: no words"),
    ("*", "commitment_events"): _ids("commitment_id", "commitments"),
    # scheduling
    ("cron.db", "cron_events"): _ids("job_id", "jobs"),
    ("cron.db", "cron_alerts"): _ids("job_id", "jobs"),
    # the swarm: a task names its space inside its metadata (S1-15)
    ("swarm_tasks.db", "swarm_tasks"): _json("metadata", "$.tenant_id"),
    ("swarm_tasks.db", "kazma_swarm_tasks"): _json("metadata", "$.tenant_id"),
    # The approval bus makes this table the first time a process watches for
    # approvals (safety.bus_bridge): it is on every install that ran one.
    ("hitl_gates.db", "hitl_watchers"): _keep("which Kazma processes watch for approval requests: "
                                           "a heartbeat per process, no space's content"),
    ("swarm_tasks.db", "swarm_worker_metrics"): _keep("counts per worker; workers are the install's"),
    ("swarm_tasks.db", "kazma_swarm_worker_metrics"): _keep("counts per worker; workers are the install's"),
    ("pipeline_logs.db", "pipeline_logs"): _ids("correlation_id", "swarm_tasks"),
    # Knowledge: a library carries its space; what it holds follows it
    ("knowledge.db", "knowledge_chunks"): _ids("library_id", "libraries"),
    ("knowledge.db", "knowledge_chunks_fts"): _ids("library_id", "libraries"),
    ("knowledge.db", "knowledge_pages_gone"): _ids("library_id", "libraries"),
    ("knowledge.db", "knowledge_vector_status"): _ids("library_id", "libraries"),
    ("knowledge.db", "knowledge_meta"): _keep("the store's own version marks"),
    # The space columns added after the first CREATE (S1-13, S1-14).
    ("knowledge.db", "knowledge_libraries"): TableRule("column", column="tenant_id"),
    ("cron.db", "cron_jobs"): TableRule("column", column="tenant_id"),
    # X: the reply bot answers the owner's account (the admin's)
    ("x_replies.db", "x_closed_threads"): _keep("the reply bot's closed conversations; the bot is the owner's"),
    ("x_replies.db", "x_mentions_cursor"): _keep("the reply bot's place in the mentions; the bot is the owner's"),
    ("x_posts.db", "x_posts"): _keep(
        "the X post ledger, by account handle: no space column. A space's posts are public on X; "
        "their first words stay here (S1-27 left this)"),
    ("x_audit.db", "x_audit_log"): _keep(
        "the X API's call record, by endpoint: no space column (S1-27 left this)"),
    ("documents.db", "document_audit_gc_control"): _keep("the audit prune's own switch"),
    ("*", "document_intake_mutex"): _keep("the intake admission's one lock row: no words"),
    # the chat apps are the owner's until they are linked to spaces (stage 3)
    ("chat_inbox.db", "inbox"): _keep("messages to the chat apps' bots, the owner's until stage 3"),
    ("chat_outbox.db", "outbox"): _keep("replies through the chat apps' bots, the owner's until stage 3"),
    ("chat_outbox.db", "owed_notices"): _keep("the chat apps' chats owed a late-delivery notice: the owner's until stage 3"),
    ("callbacks.db", "callbacks"): _keep("button tokens of the chat apps, the owner's until stage 3"),
    ("vault.db", "vault_meta"): _keep("the vault's own salt and version"),
}

#: Stores this module reaches through their own code, not by table.
_OWN_CODE = ("settings.db", "vault.db")


def _table_rule(store: str, table: str, columns: set[str], *, virtual_fts: bool = False) -> TableRule | None:
    """The rule of *table* in *store*, or None when it says none."""
    explicit = TABLE_RULES.get((store, table)) or TABLE_RULES.get(("*", table))
    if explicit is not None:
        return explicit
    for column in _SPACE_COLUMNS:
        if column in columns:
            return TableRule("column", column=column)
    if virtual_fts:
        return TableRule("follows")
    if "thread_id" in columns:
        return TableRule("thread")
    return None


# ── where the stores are ────────────────────────────────────────────────


def _store_paths() -> list[tuple[str, Path]]:
    """``(store name, file)`` for every ``row`` store file that exists."""
    from kazma_core import paths
    from kazma_core.store_registry import SPACE_RULES

    located: dict[str, Callable[[], str | Path]] = {
        "memory_state.db": paths.primary_memory_db,
        "memory_ops.db": paths.memory_ops_db,
        "memory_forget_journal.db": paths.memory_forget_journal_db,
        "checkpoints.db": paths.checkpoints_db,
        "knowledge.db": paths.knowledge_db,
        "snapshots.db": paths.snapshots_db,
        "swarm_tasks.db": paths.swarm_tasks_db,
    }
    def _document_store(name: str) -> Callable[[], Path]:
        def _where() -> Path:
            from kazma_core.documents.config import get_document_storage_root

            return Path(get_document_storage_root()) / name

        return _where

    # The document catalog and its job queue live in the document store's
    # root, not the data folder (documents.ingestion).
    located["documents.db"] = _document_store("documents.db")
    located["documents-jobs-fallback.db"] = _document_store("documents-jobs-fallback.db")
    root = paths.data_dir(create=False)
    found: list[tuple[str, Path]] = []
    for name, rule in SPACE_RULES.items():
        if rule.kind != "row" or name in _OWN_CODE:
            continue
        if "*" in name:
            if "/" in name:
                continue
            found.extend((name, p) for p in sorted(root.glob(name)) if p.is_file())
            continue
        where = Path(located[name]()) if name in located else root / name
        if where.is_file():
            found.append((name, where))
    return found


# ── what belongs to the space ───────────────────────────────────────────


@dataclass
class _Context:
    space: str
    ids: dict[str, list[str]] = field(default_factory=dict)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _tables(conn: sqlite3.Connection) -> list[tuple[str, bool]]:
    """``(table, is an FTS5 virtual table)`` of a database, its FTS shadow
    tables left out."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = {name for name, sql in rows if sql and "VIRTUAL TABLE" in sql.upper() and "FTS5" in sql.upper()}
    out = []
    for name, _sql in rows:
        if name.startswith("sqlite_"):
            continue
        if any(name == base + suffix for base in virtual for suffix in _FTS_SHADOWS):
            continue
        out.append((name, name in virtual))
    return out


def _select(conn: sqlite3.Connection, table: str, sql: str, params: tuple[Any, ...]) -> list[str]:
    """The ids *sql* finds; none when *table* is not in this database. A
    query that fails on a table that is there raises: read as "none", the
    rows hanging from it would be left behind by an erase."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
        return []
    return [str(row[0]) for row in conn.execute(sql, params)]


def _context(space: str, stores: list[tuple[str, Path]]) -> _Context:
    """The ids rows of other tables are found by, read before any delete."""
    ctx = _Context(space=space)
    for key in ("threads", "summaries", "memory_rows", "commitments", "jobs", "swarm_tasks", "libraries"):
        ctx.ids[key] = []
    from kazma_core.spaces import threads

    own, listed = threads.space_threads(space)
    ctx.ids["threads"] = list(listed) if own else []
    by_store: dict[str, list[tuple[str, str, str]]] = {
        "memory_state.db": [
            ("summaries", "memory_summaries", "SELECT id FROM memory_summaries WHERE tenant_id = ?"),
            ("memory_rows", "episodes", "SELECT id FROM episodes WHERE tenant_id = ?"),
            ("memory_rows", "beliefs", "SELECT id FROM beliefs WHERE tenant_id = ?"),
        ],
        "memory_ops.db": [
            ("commitments", "commitments", "SELECT commitment_id FROM commitments WHERE tenant_id = ?"),
        ],
        "cron.db": [("jobs", "cron_jobs", "SELECT job_id FROM cron_jobs WHERE tenant_id = ?")],
        "swarm_tasks.db": [
            ("swarm_tasks", "swarm_tasks",
             "SELECT id FROM swarm_tasks WHERE json_extract(metadata, '$.tenant_id') = ?"),
        ],
        "knowledge.db": [
            ("libraries", "knowledge_libraries", "SELECT id FROM knowledge_libraries WHERE tenant_id = ?"),
        ],
    }
    for name, path in stores:
        for key, table, sql in by_store.get(name, []):
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
                ctx.ids[key].extend(_select(conn, table, sql, (space,)))
    return ctx


def _where(rule: TableRule, ctx: _Context) -> list[tuple[str, tuple[Any, ...]]]:
    """WHERE clauses (with parameters) that find the space's rows; several
    when a list of ids is long. None of them for ``keep`` and ``follows``."""
    if rule.how == "column":
        return [(f'"{rule.column}" = ?', (ctx.space,))]
    if rule.how == "json":
        return [(f"json_extract(\"{rule.column}\", '{rule.path}') = ?", (ctx.space,))]
    if rule.how in ("thread", "ids"):
        column = "thread_id" if rule.how == "thread" else rule.column
        values = ctx.ids["threads" if rule.how == "thread" else rule.ids]
        return [
            (f'"{column}" IN ({", ".join("?" * len(part))})', tuple(part))
            for part in (values[i:i + _CHUNK] for i in range(0, len(values), _CHUNK))
        ]
    return []


@dataclass
class _Found:
    store: str
    path: Path
    table: str
    rule: TableRule
    rows: int = 0


def _walk(space: str, earlier: _Context | None = None) -> tuple[_Context, list[_Found]]:
    """Every table of every row store, its rule and how many of the space's
    rows it holds. Raises ``unknown_table`` for a table with no rule.

    *earlier*: the ids a walk before a delete found, kept: a deleted library
    is no longer in its table, and the rows that name it must still be found."""
    stores = _store_paths()
    ctx = _context(space, stores)
    if earlier is not None:
        for key, values in earlier.ids.items():
            ctx.ids[key] = sorted(set(ctx.ids.get(key, [])) | set(values))
    found: list[_Found] = []
    for name, path in stores:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            for table, virtual in _tables(conn):
                rule = _table_rule(name, table, _columns(conn, table), virtual_fts=virtual)
                if rule is None:
                    raise SpaceDataRefused(
                        "unknown_table", 500,
                        f"The table {table!r} of {name} does not say how a space's rows are found.")
                item = _Found(name, path, table, rule)
                for clause, params in _where(rule, ctx):
                    item.rows += conn.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE {clause}', params).fetchone()[0]
                found.append(item)
    return ctx, found


# ── settings and secrets ────────────────────────────────────────────────

#: Chat-state keys that name their chat's thread (settings_scope SPACE rules).
_CHAT_STATE_PREFIXES = (
    "active_thread.", "session.", "long_task.", "yolo.", "plan_mode.", "hitl_grant.",
    "task_grant.", "path_grant.", "path_grant_index.", "hitl.last_stale_notice.",
)
#: Settings that hold every space's entry in one mapping.
_BY_SPACE_MAPPINGS = (("email.accounts_by_space", ()), ("self_improvement.space_evolution", ("spaces",)))


def _settings_keys(space: str, threads: list[str]) -> list[str]:
    """The settings keys of the space: its own (``tenant.<id>.``) and its
    chats' state (a key that names one of its threads). Read through the
    settings store's own listing."""
    from kazma_core.config_store import get_config_store

    wanted = set(threads)
    keys: list[str] = []
    for group in get_config_store().get_all().values():
        for key in group:
            if key.startswith(f"tenant.{space}."):
                keys.append(key)
            elif key.startswith(_CHAT_STATE_PREFIXES) and wanted and any(
                    part in wanted for part in key.split(".")):
                keys.append(key)
    return sorted(keys)


def _mapping_entries(space: str) -> list[str]:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    found = []
    for key, inside in _BY_SPACE_MAPPINGS:
        holder = store.get(key)
        for step in inside:
            holder = holder.get(step) if isinstance(holder, dict) else None
        if isinstance(holder, dict) and space in holder:
            found.append(key)
    return found


def _vault_rows(space: str) -> list[tuple[str, str | None]]:
    """``(name, tenant)`` of every secret of the space."""
    from kazma_core import paths

    path = Path(paths.vault_db_path())
    if not path.is_file():
        return []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        first, second = f"space:{space}:", f"cfg:tenant.{space}."
        return [(str(name), tenant) for name, tenant in conn.execute(
            "SELECT name, tenant_id FROM secrets WHERE tenant_id = ? "
            "OR substr(name, 1, ?) = ? OR substr(name, 1, ?) = ?",
            (space, len(first), first, len(second), second))]


def _vault_keys(space: str) -> int:
    """How many keys the vault holds for the space (S1-26): they go with it."""
    from kazma_core import paths

    path = Path(paths.vault_db_path())
    if not path.is_file():
        return 0
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM space_keys WHERE space_id = ?", (space,)).fetchone()[0])
        except sqlite3.OperationalError:  # a vault file from before the table
            return 0


def _workspace_rows(space: str) -> list[dict[str, Any]]:
    """The space's own workspaces (S1-34: a host space's), from the
    ``workspaces`` table of ``settings.db``."""
    from kazma_core import paths

    path = Path(paths.settings_db())
    if not path.is_file():
        return []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if "space_id" not in _columns(conn, "workspaces"):
            return []
        return [dict(r) for r in conn.execute("SELECT * FROM workspaces WHERE space_id = ?", (space,))]


def _erase_workspaces(space: str) -> int:
    from kazma_core import paths

    if not _workspace_rows(space):
        return 0
    with closing(sqlite3.connect(paths.settings_db(), timeout=30)) as conn:
        gone = conn.execute("DELETE FROM workspaces WHERE space_id = ?", (space,)).rowcount
        conn.commit()
    return gone


# ── files ───────────────────────────────────────────────────────────────


def _folders(space: str) -> list[Path]:
    from kazma_core.spaces import files as space_files

    out = [space_files.space_dir(space)]
    try:
        from kazma_core.documents import storage
        from kazma_core.documents.config import get_document_storage_root

        relative = storage.space_folder(space)
        if relative:
            out.append(Path(get_document_storage_root()) / relative)
    except (ImportError, OSError, ValueError):
        logger.warning("[spaces] the document store's folder of a space could not be found", exc_info=True)
    return [p for p in out if p.exists()]


def _file_count(folders: list[Path]) -> int:
    return sum(1 for folder in folders for p in folder.rglob("*") if p.is_file())


# ── the checks ──────────────────────────────────────────────────────────


def _checked(space: str) -> dict[str, Any]:
    """The space's registry entry, after the refusals every call shares."""
    from kazma_core.db.backend import is_postgres
    from kazma_core.spaces import identity

    if not valid_space_id(space):
        raise SpaceDataRefused("no_such_space", 404, "There is no such space.")
    if space == OWNER_SPACE_ID:
        raise SpaceDataRefused(
            "owners_space", 409, "The owner's personal space holds the install's own data; it is backed up "
            "with the install, never exported or erased on its own.")
    from kazma_core.memory.state_backend import mirrors_to_postgres

    # Postgres holds shared state (and, as memory's copy, rows only it keeps:
    # the mirror never deletes those), which this module does not walk yet.
    if is_postgres() or mirrors_to_postgres():
        raise SpaceDataRefused(
            "postgres_not_yet", 409, "Counting, exporting and erasing a space is not done on a Postgres install yet.")
    found = identity.read_registry()["spaces"].get(space)
    if found is None:
        raise SpaceDataRefused("no_such_space", 404, "There is no such space.")
    return found


# ── count ───────────────────────────────────────────────────────────────


def count_space(space: str) -> dict[str, Any]:
    """How much the space holds, store by store: rows, settings, secrets and
    files. Numbers only, never a word of what they hold."""
    _checked(space)
    ctx, found = _walk(space)
    stores: dict[str, int] = {}
    for item in found:
        if item.rows:
            stores[item.store] = stores.get(item.store, 0) + item.rows
    folders = _folders(space)
    return {
        "space": space,
        "stores": stores,
        "rows": sum(stores.values()),
        "settings": len(_settings_keys(space, ctx.ids["threads"])) + len(_mapping_entries(space)),
        "secrets": len(_vault_rows(space)),
        "workspaces": len(_workspace_rows(space)),
        "files": _file_count(folders),
        "libraries": len(ctx.ids["libraries"]),
        "chats": len(ctx.ids["threads"]),
    }


# ── export ──────────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _rows(item: _Found, ctx: _Context) -> Iterator[dict[str, Any]]:
    with closing(sqlite3.connect(f"file:{item.path}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for clause, params in _where(item.rule, ctx):
            for row in conn.execute(f'SELECT * FROM "{item.table}" WHERE {clause}', params):
                yield {key: _jsonable(row[key]) for key in row.keys()}


def export_space(space: str, target: Path, *, password: str | None = None) -> dict[str, Any]:
    """Write the space to the zip *target*: every store's rows of the space
    (``stores/<store>/<table>.jsonl``), its settings (``settings.json``), the
    names of its secrets (``secrets.json``: never a value), its files
    (``files/...``) and ``manifest.json`` with the counts. Returns the
    manifest.

    With *password* the zip is sealed with it (``security.sealed_zip``):
    the plain zip is written beside *target* and removed once sealed."""
    _checked(space)
    if password is not None:
        from kazma_core.security.sealed_zip import seal

        plain = target.with_name(target.name + ".plain")
        try:
            manifest = export_space(space, plain)
            seal(plain, target, password)
        finally:
            plain.unlink(missing_ok=True)
        return manifest
    ctx, found = _walk(space)
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    manifest: dict[str, Any] = {"space": space, "made_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "tables": {}, "files": 0}
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for item in found:
            if not item.rows:
                continue
            lines = [json.dumps(row, ensure_ascii=False, default=str) for row in _rows(item, ctx)]
            folder = item.path.name if "*" in item.store else item.store
            name = f"stores/{folder}/{item.table}.jsonl"
            zipped.writestr(name, "\n".join(lines) + "\n")
            manifest["tables"][f"{folder}:{item.table}"] = len(lines)
        settings = {key: store.get(key) for key in _settings_keys(space, ctx.ids["threads"])}
        for key in _mapping_entries(space):
            holder = store.get(key)
            for step in dict(_BY_SPACE_MAPPINGS)[key]:
                holder = holder.get(step, {})
            settings[key] = {space: holder.get(space)}
        zipped.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=1, default=str))
        zipped.writestr("workspaces.json", json.dumps(_workspace_rows(space), indent=1, default=str))
        zipped.writestr("secrets.json", json.dumps(
            [{"name": name, "space": tenant or ""} for name, tenant in _vault_rows(space)], indent=1))
        for folder in _folders(space):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zipped.write(path, f"files/{folder.name}/{path.relative_to(folder).as_posix()}")
                    manifest["files"] += 1
        zipped.writestr("manifest.json", json.dumps(manifest, indent=1))
    partial.replace(target)
    logger.info("[spaces] space exported space=%s tables=%d files=%d", space,
                len(manifest["tables"]), manifest["files"])
    return manifest


# ── erase ───────────────────────────────────────────────────────────────


def _is_delete_guard(sql: str) -> bool:
    """Whether trigger *sql* refuses deletes: BEFORE DELETE, and it aborts."""
    words = " ".join(sql.upper().split())
    return " BEFORE DELETE ON " in f" {words} " and "RAISE(ABORT" in words.replace(" ", "")


def _lift_delete_guards(conn: sqlite3.Connection, tables: set[str]) -> list[str]:
    """Drop the triggers that refuse every delete from *tables* (an
    append-only table's guard: ``document_job_events_no_delete``), inside the
    erase's own transaction, and return their SQL to put them back before it
    commits. An erase is the one deliberate removal of a space's rows; a
    rollback brings the guards back with the rows. Only a BEFORE DELETE
    trigger that aborts is lifted: the index and outbox triggers that follow
    a delete still run."""
    lifted: list[str] = []
    for name, table, sql in conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall():
        if table in tables and sql and _is_delete_guard(sql):
            conn.execute(f'DROP TRIGGER "{name}"')
            lifted.append(str(sql))
    return lifted


def _erase_rows(found: list[_Found], ctx: _Context) -> int:
    erased = 0
    by_path: dict[Path, list[_Found]] = {}
    for item in found:
        by_path.setdefault(item.path, []).append(item)
    for path, items in by_path.items():
        with closing(sqlite3.connect(str(path), timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                here = 0
                guards = _lift_delete_guards(conn, {item.table for item in items})
                for item in items:
                    for clause, params in _where(item.rule, ctx):
                        here += conn.execute(f'DELETE FROM "{item.table}" WHERE {clause}', params).rowcount
                for sql in guards:
                    conn.execute(sql)
                virtual = [table for table, is_fts in _tables(conn) if is_fts]
                if virtual and any(i.rows for i in items):
                    from kazma_core.db.fts5_purge import purge_deleted_rows

                    for table in virtual:
                        purge_deleted_rows(conn, table)
                conn.execute("COMMIT")
                committed = True
                erased += here
            finally:
                # One file's rows go together or not at all; an erase that
                # stopped is run again (the space stays closed meanwhile).
                if not committed:
                    conn.execute("ROLLBACK")
    return erased


def _erase_settings(space: str, threads: list[str]) -> int:
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    gone = 0
    for key in _settings_keys(space, threads):
        gone += int(bool(store.delete(key)))
    for key in _mapping_entries(space):
        inside = dict(_BY_SPACE_MAPPINGS)[key]

        def _drop(current: Any, inside: tuple[str, ...] = inside) -> Any:
            if not isinstance(current, dict):
                return current
            holder = current
            for step in inside:
                holder = holder.get(step) if isinstance(holder, dict) else None
            if isinstance(holder, dict):
                holder.pop(space, None)
            return current

        store.atomic_update(key, _drop)
        gone += 1
    return gone


def _erase_secrets(space: str) -> int:
    from kazma_core.security.vault import get_vault

    rows = _vault_rows(space)
    if not rows and not _vault_keys(space):
        return 0
    vault = get_vault()
    if vault is None:
        raise SpaceDataRefused("vault_unavailable", 503, "The vault cannot be opened; its secrets were not erased.")
    return vault.erase_space(space)


def erase_space(space: str, *, by: str) -> dict[str, Any]:
    """Erase the space: its libraries (vectors and fetched pages too), every
    store's rows, its settings, its secrets and its files; then it leaves the
    identity store, recorded. A personal space is erased only once its person
    was deleted (``account_deleted_at``). Returns the counts erased."""
    found_space = _checked(space)
    if found_space.get("personal_of") and not found_space.get("account_deleted_at"):
        raise SpaceDataRefused(
            "personal_space_lives", 409,
            "A personal space is erased once its person is deleted (People).")
    if _vault_rows(space) or _vault_keys(space):
        from kazma_core.security.vault import get_vault

        if get_vault() is None:  # refused before anything is changed
            raise SpaceDataRefused("vault_unavailable", 503, "The vault cannot be opened; nothing was erased.")
    from kazma_core.spaces import people

    # Nobody new can start work in it: its members go first.
    people.close_space(space, by=by)
    before, first = _walk(space)
    from kazma_core.stores.knowledge_lifecycle import delete_library

    for library in before.ids["libraries"]:
        delete_library(library)
    ctx, found = _walk(space, before)
    # The rows the library delete took, then the rest.
    by_library = sum(item.rows for item in first) - sum(item.rows for item in found)
    erased = {
        "rows": by_library + _erase_rows(found, ctx),
        "settings": _erase_settings(space, ctx.ids["threads"]),
        "secrets": _erase_secrets(space),
        "workspaces": _erase_workspaces(space),
        "libraries": len(ctx.ids["libraries"]),
        "files": 0,
    }
    for folder in _folders(space):
        erased["files"] += _file_count([folder])
        shutil.rmtree(folder)
    people.forget_space(space, by=by)
    logger.warning("[spaces] space erased space=%s by=%s rows=%d files=%d", space, by,
                   erased["rows"], erased["files"])
    return erased
