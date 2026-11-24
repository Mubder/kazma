"""Two spaces' worth of data in every store (Spaces S1-27, S1-28).

Every ``row`` store of ``store_registry.SPACE_RULES`` is made by its own code
in this run's data folder, and a word is planted for space A, and another for
space B, into every content column the registry names, of every table, with
the space found the way the store finds it (a space column, a thread of the
space, a field of a JSON column, a row it hangs from); and into the space's
settings, secrets and files. The ``world`` fixture builds it;
``tests/test_space_export_erase.py`` and ``tests/test_backup_restore_spaces.py``
use it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, scope, space_data, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
from kazma_core.store_registry import SPACE_RULES

#: Ids as Kazma makes them (``scope.new_space_id``): a bundle of any other
#: is refused.
A, B = "se0a5e00000a0", "se0a5e00000b0"


def _word(space: str) -> str:
    return f"plant{space}{uuid.uuid4().hex[:6]}"


# ── every row store, made by its own code ───────────────────────────────


def _make_stores() -> list[str]:
    """Make every row store's tables in this run's data folder, each by its
    own code (the product's list, ``spaces.row_stores``: a moved space lands
    in the same stores). Returns the stores made."""
    from kazma_core.safety.bus_bridge import record_watcher
    from kazma_core.spaces import row_stores

    made = row_stores.make_row_stores()
    # The approval bus's watcher table, made by its own code the first time a
    # process watches for approvals: on every install that ran one, never by
    # ensure_gate_schema (S1-27's walk refused live's hitl_gates.db for it).
    record_watcher(kind="ui", detail="the space world")
    # The table of the chats taken out, made by its own code the first time a
    # chat is (S2-12): in thread_scopes.db, which the list above made.
    from kazma_core.spaces import chat_moves

    chat_moves._connection().close()
    return made


# ── planting ────────────────────────────────────────────────────────────

#: Parent tables planted first, with the id the tables hanging from them use.
_PARENTS = {
    "memory_summaries": ("id", "summaries"),
    "episodes": ("id", "memory_rows"),
    "beliefs": ("id", "memory_rows"),
    "commitments": ("commitment_id", "commitments"),
    "cron_jobs": ("job_id", "jobs"),
    "swarm_tasks": ("id", "swarm_tasks"),
    "knowledge_libraries": ("id", "libraries"),
}


def _filler(ctype: str, name: str) -> Any:
    kind = (ctype or "").upper()
    if "INT" in kind:
        return 1
    if "REAL" in kind or "FLOA" in kind or "DOUB" in kind:
        return 1.0
    if "BLOB" in kind:
        return b"x"
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _plant_table(conn: sqlite3.Connection, store: str, table: str, rule: space_data.TableRule,
                 space: str, word: str, parents: dict[str, str], thread: str) -> None:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    content = SPACE_RULES[store].content.get(table, ()) if store in SPACE_RULES else ()
    sql = (conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone() or [""])[0] or ""
    # A CHECK (col IN ('a', 'b')) takes one of its own words.
    allowed = {m.group(1): m.group(2) for m in re.finditer(r"\b(\w+)\s+IN\s*\(\s*'([^']*)'", sql)}
    row: dict[str, Any] = {}
    rowid_alias = sum(1 for r in info if r[5]) == 1
    for _cid, name, ctype, notnull, default, pk in info:
        if name in allowed and name not in content:
            row[name] = allowed[name]
        elif name in content:
            kind = (ctype or "").upper()
            row[name] = word.encode() if "BLOB" in kind else (
                json.dumps({"w": word}) if name.endswith(("_json", "metadata", "manifest", "record", "detail"))
                else word)
        elif pk and rowid_alias and "INT" in (ctype or "").upper():
            continue
        elif notnull and default is None or pk:
            row[name] = _filler(ctype, name)
    if rule.how == "column":
        row[rule.column] = space
    elif rule.how == "thread":
        row["thread_id"] = thread
    elif rule.how == "json":
        row[rule.column] = json.dumps({"tenant_id": space, "w": word})
    elif rule.how == "ids":
        row[rule.column] = parents[rule.ids]
    if table in ("checkpoints", "writes"):
        row["type"] = "bytes"  # how LangGraph says a value is kept as it is
    if table in _PARENTS:
        column, key = _PARENTS[table]
        row[column] = parents[key] if key != "memory_rows" else parents[f"{key}:{table}"]
    columns = ", ".join(f'"{c}"' for c in row)
    # OR REPLACE: a store kept between tests (one row per space, as About me)
    # takes this run's word over the last one's.
    conn.execute(f'INSERT OR REPLACE INTO "{table}" ({columns}) VALUES ({", ".join("?" * len(row))})',
                 tuple(row.values()))


def _plant(space: str, word: str) -> list[str]:
    """Plant *word* for *space* in every table with a rule, its settings,
    its secrets and its files. Returns ``store:table`` planted."""
    thread = f"thread-{space}-{uuid.uuid4().hex[:6]}"
    threads.record_thread(thread, space, OWNER_ACCOUNT_ID)
    parents = {key: f"{key}-{space}-{uuid.uuid4().hex[:6]}" for key in
               ("summaries", "commitments", "jobs", "swarm_tasks", "libraries")}
    parents["memory_rows:episodes"] = f"ep-{space}-{uuid.uuid4().hex[:6]}"
    parents["memory_rows:beliefs"] = f"b-{space}-{uuid.uuid4().hex[:6]}"
    parents["memory_rows"] = parents["memory_rows:episodes"]
    planted: list[str] = []
    for store, path in space_data.store_paths():
        with closing(sqlite3.connect(str(path))) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = space_data._tables(conn)
            ordered = sorted(tables, key=lambda item: item[0] not in _PARENTS)
            for table, virtual in ordered:
                rule = space_data._table_rule(store, table, space_data._columns(conn, table), virtual_fts=virtual)
                if rule is None or rule.how in ("keep", "follows"):
                    continue
                if rule.how == "column" and rule.column == "space_id" and table == "thread_scopes":
                    continue  # recorded above, through threads.record_thread
                _plant_table(conn, store, table, rule, space, word, parents, thread)
                planted.append(f"{store}:{table}")
            conn.commit()
    store = get_config_store()
    store.set(f"tenant.{space}.connectors.x.handle", word, category="x")
    store.set(f"long_task.{thread}", {"goal": word}, category="runtime")
    from kazma_core.security.vault import get_vault

    get_vault().for_space(space).store(f"space:{space}:{word}", word)
    from kazma_core.spaces import files as space_files

    folder = space_files.space_dir(space) / "files"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "note.txt").write_text(word, encoding="utf-8")
    return planted


# ── reading everything ──────────────────────────────────────────────────


#: The erase's own map of the stores, taken before a control replaces it:
#: the scan must read every store whatever the erase was told.
_EVERY_STORE = space_data.store_paths


def _databases() -> list[Path]:
    from kazma_core.documents.config import get_document_storage_root

    roots = {paths.data_dir(), Path(get_document_storage_root())}
    # Some stores live outside the data folder (under pytest the memory
    # databases are in the test's own folder): read them by where they are.
    found: set[Path] = {Path(path) for _name, path in _EVERY_STORE()}
    found |= {Path(paths.primary_memory_db()), Path(paths.memory_ops_db())}
    found = {path for path in found if path.is_file()}
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (".db", ".sqlite3") and "-wal" not in path.name:
                found.add(path)
    return sorted(found)


def _where_word(word: str) -> dict[str, int]:
    """``file:table:column`` -> rows holding *word*, over every database, and
    ``file:<path>`` for every other file holding it."""
    out: dict[str, int] = {}
    for db in _databases():
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                    for column in [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]:
                        try:
                            n = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{column}" AS TEXT) '
                                             "LIKE ?", (f"%{word}%",)).fetchone()[0]
                        except sqlite3.OperationalError:
                            continue
                        if n:
                            out[f"{db.name}:{table}:{column}"] = n
        except sqlite3.DatabaseError:
            continue
    for path in paths.data_dir().rglob("*"):
        if path.is_file() and path.suffix not in (".db", ".sqlite3", ".db-wal", ".db-shm"):
            try:
                if word.encode() in path.read_bytes():
                    out[f"file:{path.name}"] = 1
            except OSError:
                continue
    return out


# ── the world ───────────────────────────────────────────────────────────


#: The module singletons and caches the world's stores keep: reset for the
#: world's own data folder, and put back after (monkeypatch). A store added
#: to ``_make_stores`` that keeps one goes here, or its rows reach the
#: session's folder and every later test in the process
#: (``tests/test_space_world_leaves_nothing.py`` fails).
_SINGLETONS: tuple[tuple[str, str, Any], ...] = (
    ("kazma_core.agent.artifacts", "_store", None),
    ("kazma_core.agent.task_ledger", "_store", None),
    ("kazma_ui.session_manager", "_session_manager", None),
    ("kazma_core.swarm.memory.pipeline_logger", "_conn", None),
    ("kazma_core.swarm.memory.pipeline_logger", "_pipeline_logger", None),
    ("kazma_core.x_api.publication_store", "_store", None),
    # tests/conftest.py binds a store of its own for each test: an install
    # of the test's own keeps its X publications in its own data folder.
    ("kazma_core.x_api.publication_store", "_test_store", None),
    ("kazma_core.x_api.reply_store", "_store", None),
    ("kazma_core.x_api.schedule", "_store", None),
    ("kazma_core.runs.incidents", "_store", None),
    ("kazma_core.stores.knowledge", "_knowledge_store", None),
    ("kazma_core.observability.llm_ledger", "_conn", None),
    ("kazma_core.safety.hitl_gates", "_db_path_override", None),
    ("kazma_core.safety.hitl_gates", "_schema_ready", False),
    ("kazma_core.time_travel_audit", "_override", None),
)


def _own_data_folder(monkeypatch, folder: Path) -> Path:
    """Point this test at a data folder of its own: every store the world
    makes, and every walk that reads them, finds it through
    ``paths.data_dir()``; the singletons are made afresh there."""
    import importlib

    data = folder / "kazma-data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    for module, attr, fresh in _SINGLETONS:
        monkeypatch.setattr(importlib.import_module(module), attr, fresh)
    monkeypatch.setattr(threads, "_spaces", {})
    # Two stores bound their default path at import: opened here on purpose.
    from kazma_core.observability import llm_ledger
    from kazma_core.swarm.memory import pipeline_logger

    llm_ledger._get_conn(str(data / "llm_calls.db"))
    pipeline_logger._get_conn(str(data / "pipeline_logs.db"))
    return data


def _close_singletons() -> None:
    """Close what the world opened in its folder, before monkeypatch puts the
    session's singletons back."""
    import importlib

    for module, attr, _fresh in _SINGLETONS:
        made = getattr(importlib.import_module(module), attr, None)
        close = getattr(made, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 -- a test folder's handle; the folder goes anyway
                pass


@contextmanager
def own_install(monkeypatch, folder: Path, *, vault_key: str = "a" * 64,
                vault_name: str = "") -> Iterator[Path]:
    """An install of the test's own in *folder*, Spaces enforced: its data
    folder, settings store and vault (opened with *vault_key*), and the
    module singletons made afresh there; closed and put back after. Yields
    the data folder. Nothing is left in the session's
    (``tests/test_space_world_leaves_nothing.py``). *vault_name* opens the
    vault file of that name in the data folder (a copy of an install keeps
    its vault); else a new one."""
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.security import vault as vault_mod
    from kazma_core.security.vault import reset_posture_cache

    data = _own_data_folder(monkeypatch, folder)
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(data / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(data / "memory_ops.db"))
    settings = ConfigStore(db_path=str(data / "settings.db"), yaml_path=str(folder / "kazma.yaml"))
    # monkeypatch puts the store the test had back afterwards.
    from kazma_core import config_store as config_store_module

    monkeypatch.setattr(config_store_module, "_config_store", config_store_module._config_store)
    set_config_store(settings)
    # A vault of this test's own, in the data folder the scan reads.
    monkeypatch.setenv("KAZMA_VAULT_KEY", vault_key)
    vault_path = data / (vault_name or f"vault-{uuid.uuid4().hex[:8]}.db")
    own_vault = vault_mod.SecretVault(db_path=str(vault_path))
    monkeypatch.setattr(vault_mod, "_vault", own_vault)
    monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
    monkeypatch.setattr(paths, "vault_db_path", lambda: str(vault_path))
    monkeypatch.setattr(scope, "_enforced", True)
    try:
        yield data
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        own_vault.close()
        reset_posture_cache()
        _close_singletons()
        settings.close()


def plant_world(monkeypatch, folder: Path):
    """Build the two spaces and plant them, in an install of the test's own
    (*folder*, :func:`own_install`). A test module's ``world`` fixture
    yields from this.

    The memory and settings databases are in that folder too, as on an
    install: a backup of the folder copies them.
    """
    with own_install(monkeypatch, folder) as data:
        for space in (A, B):
            if space not in identity.read_registry()["spaces"]:
                identity.create_space(space, created_by=OWNER_ACCOUNT_ID, space_id=space)
        made = _make_stores()
        words = {A: _word(A), B: _word(B)}
        planted = {space: _plant(space, words[space]) for space in (A, B)}
        yield {"words": words, "planted": planted, "made": made, "data": data}
