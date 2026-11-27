"""The checkpointer and the second lock, on a real Postgres (Spaces S4-01 step 4).

LangGraph's saver is called directly, the manager's own check bypassed: what
keeps two spaces apart is the database. On a private database with the rules
in place (``row_security.apply``), these prove:

* Spaces off and nothing armed, the checkpointer sends what it sent before:
  no space, no role (recorded on the wire); Spaces enforced, the control,
  each space's checkout opens its transaction in the space;
* enforced and not armed, a checkpoint is stamped with its thread's space
  (review M7: armed or not); Spaces off, the control, it is NULL (the owner's);
* armed, in space A, a read, a listing, a put, its writes and a delete touch
  A's threads only; not armed, the control, A reads B's thread;
* the backfill gives an older build's rows their thread's space and a wrong
  stamp back to the owner; before it, the control, A cannot see its own old
  thread once armed;
* ``aprune`` keeping the latest is refused in a space's checkout, Spaces
  enforced (review L1); Spaces off, the control, it prunes as before.

Needs a throwaway server and a superuser DSN (``KAZMA_DATABASE_URL`` +
``KAZMA_TEST_ALLOW_REAL_DB=1``); it makes ``kazma_app`` and ``kazma_space``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests._as_request import lock_armed, not_armed
from tests.test_pg_app_role import SCRIPT, _every_store_set_up, _with

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]


def _sql(dsn: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(statement, params or None)
        return list(cur.fetchall()) if cur.description else []


@pytest.fixture(scope="module")
def db() -> Iterator[dict[str, str]]:
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with psycopg.connect(base, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("making roles needs a superuser DSN")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_app'").fetchone():
            pytest.skip("kazma_app already exists on this server: not a throwaway one")
        had_space_role = bool(admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space'").fetchone())
        name = f"kazma_ckpt_{uuid.uuid4().hex[:10]}"
        admin.execute(f'CREATE DATABASE "{name}"')
    su = _with(base, db=name)
    password = secrets.token_urlsafe(24)
    try:
        _every_store_set_up(su)
        _sql(su, SCRIPT.read_text(encoding="utf-8"))
        _sql(su, f"ALTER ROLE kazma_app PASSWORD '{password}'")
        app = _with(base, user="kazma_app", password=password, db=name)
        from kazma_core.db import row_security

        was = row_security.in_place()
        done = row_security.apply(app, vector_table="")
        row_security._mark_in_place(was)  # this module's apply is no process's
        assert done.complete, (done.refused, done.left, done.unresolved)
        yield {"base": base, "su": su, "app": app}
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            admin.execute("DROP ROLE IF EXISTS kazma_app")
            if not had_space_role:
                admin.execute("DROP ROLE IF EXISTS kazma_space")


def _run(work: Callable[[], Any]) -> Any:
    """*work* (a coroutine function) on a loop psycopg's async driver takes."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(work())


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


async def _put(saver: Any, thread_id: str) -> dict[str, Any]:
    from langgraph.checkpoint.base import empty_checkpoint

    written = await saver.aput(_config(thread_id), empty_checkpoint(), {"source": "input", "step": 0}, {})
    await saver.aput_writes(written, [("note", f"from {thread_id}")], "task-1")
    return written


def _stamps(db: dict[str, str], thread_id: str) -> set[Any]:
    """The spaces a thread's rows are stamped with, read as the owner."""
    rows = _sql(db["app"], "SELECT space_id FROM checkpoints WHERE thread_id = %s UNION ALL "
                           "SELECT space_id FROM checkpoint_writes WHERE thread_id = %s", (thread_id, thread_id))
    return {r[0] for r in rows}


@pytest.fixture()
def lock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Spaces off and nothing armed, as a fresh process has it; the test sets
    what it needs."""
    from kazma_core.db import row_security
    from kazma_core.spaces import scope

    not_armed(monkeypatch)
    monkeypatch.setattr(scope, "_enforced", False)
    monkeypatch.setattr(row_security, "_in_place", row_security.in_place())  # given back after
    return row_security


def _saver_on(dsn: str, work: Callable[[Any], Any]) -> Any:
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer

    async def _go() -> Any:
        saver = await open_postgres_checkpointer(dsn, max_size=3)
        try:
            return await work(saver)
        finally:
            await close_postgres_checkpointer(saver)

    return _run(_go)


# ── Spaces off: as before (recorded on the wire) ─────────────────────────


@pytest.mark.parametrize("enforced", [False, True])
def test_spaces_off_the_checkpointer_sends_what_it_did(db: dict[str, str], lock: Any,
                                                      monkeypatch: pytest.MonkeyPatch, enforced: bool) -> None:
    from urllib.parse import urlsplit

    from kazma_core.spaces import scope
    from kazma_core.spaces.row_work import statements_of

    from tests.test_pg_space_pool import _via, _Wire

    monkeypatch.setattr(scope, "_enforced", enforced)
    address = urlsplit(db["app"])
    wire = _Wire(address.hostname or "127.0.0.1", address.port or 5432)
    thread = f"t-{uuid.uuid4().hex[:8]}"
    marks: dict[str, int] = {}

    async def _work(saver: Any) -> None:
        marks["since"], marks["signed"] = len(wire.sent), len(wire.startups)
        with statements_of("sa"):
            await _put(saver, thread)
            assert (await saver.aget_tuple(_config(thread))) is not None

    try:
        _saver_on(_via(db["app"], wire.port), _work)
    finally:
        wire.close()
    sent = wire.sent[marks["since"]:]
    spaced = [s for s in sent if "kazma.space" in s]
    assert not any("kazma_space" in p.get("options", "") for p in wire.startups[marks["signed"]:])
    if not enforced:
        assert spaced == [], spaced
        assert not any(s.strip() in ("BEGIN", "COMMIT") for s in sent), "nothing more than LangGraph's own"
    else:  # the control: each checkout opens its transaction in the space
        assert spaced and all("set_config('kazma.space', 'sa', true)" in s for s in spaced), spaced
        assert "COMMIT" in [s.strip() for s in sent]


# ── stamped with the thread's space, armed or not (review M7) ────────────


@pytest.mark.parametrize("enforced", [True, False])
def test_a_checkpoint_is_stamped_with_its_space(db: dict[str, str], lock: Any, monkeypatch: pytest.MonkeyPatch,
                                                enforced: bool) -> None:
    from kazma_core.spaces import scope
    from kazma_core.spaces.row_work import statements_of

    monkeypatch.setattr(scope, "_enforced", enforced)
    theirs, owners = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"

    async def _work(saver: Any) -> None:
        with statements_of("sa"):
            await _put(saver, theirs)
        with statements_of(scope.OWNER_SPACE_ID):
            await _put(saver, owners)

    _saver_on(db["app"], _work)
    if enforced:
        assert _stamps(db, theirs) == {"sa"} and _stamps(db, owners) == {"default"}
    else:  # the control: Spaces off, nothing is stamped (the owner's, N19)
        assert _stamps(db, theirs) == {None} and _stamps(db, owners) == {None}


# ── armed: a space's own threads only ────────────────────────────────────


@pytest.mark.parametrize("armed", [True, False])
def test_a_space_reads_and_changes_its_own_threads_only(db: dict[str, str], lock: Any,
                                                       monkeypatch: pytest.MonkeyPatch, armed: bool) -> None:
    from kazma_core.spaces import scope
    from kazma_core.spaces.row_work import statements_of

    monkeypatch.setattr(scope, "_enforced", True)
    a, b = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    seen: dict[str, Any] = {}

    async def _work(saver: Any) -> None:
        with statements_of("sa"):
            await _put(saver, a)
        with statements_of("sb"):
            await _put(saver, b)
        lock_armed(monkeypatch, armed)
        with statements_of("sa"):
            seen["own"] = await saver.aget_tuple(_config(a))
            seen["theirs"] = await saver.aget_tuple(_config(b))
            seen["listed"] = {c.config["configurable"]["thread_id"] async for c in saver.alist(None)}
            await saver.adelete_thread(b)  # B's, asked from A
            seen["own_written"] = await _put(saver, a)
        not_armed(monkeypatch)

    _saver_on(db["app"], _work)
    assert seen["own"] is not None and seen["own_written"]
    if armed:
        assert seen["theirs"] is None, "B's thread is not there for A"
        assert b not in seen["listed"] and a in seen["listed"]
        assert _stamps(db, b) == {"sb"}, "A's delete reached none of B's rows"
    else:  # the control: not armed, A reads B's thread
        assert seen["theirs"] is not None and b in seen["listed"]
        assert _stamps(db, b) == set(), "not armed, A's delete removed B's thread"


def test_armed_nothing_said_is_refused_and_the_install_reads_every_space(db: dict[str, str], lock: Any,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.spaces import scope
    from kazma_core.spaces.row_work import install_statements, statements_of
    from kazma_core.spaces.scope import NoSpaceBound

    monkeypatch.setattr(scope, "_enforced", True)
    a = f"t-{uuid.uuid4().hex[:8]}"
    seen: dict[str, Any] = {}

    async def _work(saver: Any) -> None:
        with statements_of("sa"):
            await _put(saver, a)
        lock_armed(monkeypatch, True)
        try:
            with pytest.raises(NoSpaceBound):
                await saver.aget_tuple(_config(a))
            with install_statements("a test: the install's listing of every thread"):
                seen["all"] = {c.config["configurable"]["thread_id"] async for c in saver.alist(None)}
        finally:
            not_armed(monkeypatch)

    _saver_on(db["app"], _work)
    assert a in seen["all"]


# ── the backfill (design 7.3) ────────────────────────────────────────────


def test_the_backfill_gives_old_rows_their_threads_space(db: dict[str, str], lock: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.spaces import scope, threads
    from kazma_core.spaces.row_work import statements_of

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])  # this install's own database (step 4 review M2)
    old, moved = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    threads.record_thread(old, "sa", scope.OWNER_ACCOUNT_ID)

    async def _older_build(saver: Any) -> None:  # Spaces off: NULL, as an older build writes
        await _put(saver, old)
        await _put(saver, moved)

    _saver_on(db["app"], _older_build)
    _sql(db["app"], "UPDATE checkpoints SET space_id = 'sb' WHERE thread_id = %s", (moved,))  # a wrong stamp
    assert _stamps(db, old) == {None}

    monkeypatch.setattr(scope, "_enforced", True)
    seen: dict[str, Any] = {}

    async def _read(saver: Any) -> None:
        lock_armed(monkeypatch, True)
        try:
            with statements_of("sa"):
                seen["tuple"] = await saver.aget_tuple(_config(old))
        finally:
            not_armed(monkeypatch)

    _saver_on(db["app"], _read)
    assert seen["tuple"] is None, "the control: before the backfill A cannot see its own old thread"
    done = lock.apply(db["app"], vector_table="")
    assert done.complete and done.backfilled >= 3, done
    assert _stamps(db, old) == {"sa"} and _stamps(db, moved) == {None}
    _saver_on(db["app"], _read)
    assert seen["tuple"] is not None, "after it, A reads its old thread"
    assert lock.apply(db["app"], vector_table="").backfilled == 0, "a second pass has nothing to do"


# ── aprune keeping the latest (review L1) ────────────────────────────────


@pytest.mark.parametrize(("enforced", "armed"), [(True, True), (True, False), (False, False)])
def test_pruning_to_the_latest_is_refused_in_a_space(db: dict[str, str], lock: Any, monkeypatch: pytest.MonkeyPatch,
                                                    enforced: bool, armed: bool) -> None:
    from kazma_core.spaces import scope
    from kazma_core.spaces.row_work import statements_of

    monkeypatch.setattr(scope, "_enforced", enforced)
    a = f"t-{uuid.uuid4().hex[:8]}"

    async def _work(saver: Any) -> None:
        with statements_of("sa"):
            await _put(saver, a)
            await _put(saver, a)
            lock_armed(monkeypatch, armed)
            try:
                if enforced:
                    with pytest.raises(lock._KeepLatestRefused):
                        await saver.aprune([a])
                    await saver.aprune([a], strategy="delete")  # the strategy that works in a space
                else:  # the control: Spaces off, as before
                    await saver.aprune([a])
            finally:
                not_armed(monkeypatch)

    _saver_on(db["app"], _work)
    count = _sql(db["app"], "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (a,))[0][0]
    assert count == (0 if enforced else 1)


# ── the step 4 review: what the backfill trusts (M1, M2) ─────────────────


def _stamped_row(db: dict[str, str], space: str) -> str:
    """A checkpoint row stamped with *space*, written as the owner."""
    thread = f"t-{uuid.uuid4().hex[:8]}"
    _sql(db["app"], "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata, space_id) "
                    "VALUES (%s, '', 'k1', '{}', '{}', %s)", (thread, space))
    return thread


def test_a_missing_record_store_is_no_list_of_no_threads(db: dict[str, str], lock: Any, tmp_path: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: sqlite3.connect made the missing store, empty; the backfill read
    "no threads" and gave every space's rows to the owner, complete."""
    from kazma_core.spaces import threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    theirs = _stamped_row(db, "sa")
    gone = tmp_path / "thread_scopes.db"
    monkeypatch.setattr(threads, "thread_scopes_db", lambda: str(gone))
    done = lock.apply(db["app"], vector_table="")
    assert not done.complete and any("not there" in u for u in done.unresolved), done
    assert _stamps(db, theirs) == {"sa"}, "nothing was reset"
    assert not gone.exists(), "the store is not made by reading it"


def test_an_empty_record_store_with_stamped_rows_changes_nothing(db: dict[str, str], lock: Any, tmp_path: Any,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from kazma_core.spaces import threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    theirs = _stamped_row(db, "sb")
    empty = tmp_path / "thread_scopes.db"
    with sqlite3.connect(empty) as made:
        made.executescript(threads._SCHEMA)
    monkeypatch.setattr(threads, "thread_scopes_db", lambda: str(empty))
    done = lock.apply(db["app"], vector_table="")
    assert not done.complete and any("list no thread" in u for u in done.unresolved), done
    assert _stamps(db, theirs) == {"sb"}
    _sql(db["app"], "DELETE FROM checkpoints WHERE thread_id = %s", (theirs,))


def test_another_databases_stamps_are_not_this_installs_to_change(db: dict[str, str], lock: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: on a replica, or a database --url-from named, this install's
    records reset the stamps of threads it never knew."""
    theirs = _stamped_row(db, "sc")  # a thread another install recorded
    monkeypatch.setenv("KAZMA_DATABASE_URL", db["su"].rsplit("/", 1)[0] + "/postgres")  # Kazma's is elsewhere
    done = lock.apply(db["app"], vector_table="")
    assert _stamps(db, theirs) == {"sc"}, "kept"  # first: what the review measures (delta review N4)
    assert done.complete and any("not the database this install's own address names" in n for n in done.notes), done
    assert done.not_backfilled, "said, for the script to say loudly (delta review N3)"
    # The control: as this install's own database, the record store answers for it.
    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    done = lock.apply(db["app"], vector_table="")
    assert done.backfilled >= 1 and not done.not_backfilled
    assert _stamps(db, theirs) == {None}


def _another_name_for(dsn: str) -> str:
    """The same database through another host name: ``hostaddr`` carries the
    address, ``host`` a name no resolver is asked for."""
    import socket

    conninfo = psycopg.conninfo  # the module's guarded import: no bare one in a test
    host = str(conninfo.conninfo_to_dict(dsn).get("host") or "localhost")
    return conninfo.make_conninfo(dsn, host="kazma-db.invalid", hostaddr=socket.gethostbyname(host))


def test_this_installs_own_database_by_another_name_is_backfilled(db: dict[str, str], lock: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Second delta review L2: reached by another host name (``hostaddr``),
    this install's own database counted as another -- nothing backfilled --
    and the script said the rules were in place. It is told by its server
    identity now. Another database on the same server stays another (the M2
    test above)."""
    from kazma_core.db import availability
    from kazma_core.spaces import scope, threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])  # this install's own address
    alias = _another_name_for(db["app"])
    assert not availability._same_as_kazmas(alias, availability._database_of), "by its address alone, another"
    threads.record_thread(f"t-{uuid.uuid4().hex[:8]}", "sa", scope.OWNER_ACCOUNT_ID)
    stray = _stamped_row(db, "sd")  # a space no record names: a whole pass gives it back to the owner
    done = lock.apply(alias, vector_table="")
    assert _stamps(db, stray) == {None} and not done.not_backfilled, done


def test_telling_the_database_apart_leaves_the_apply_outside_a_transaction(db: dict[str, str], lock: Any,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 5 review L1: by another name, the identity was read on the
    apply's own connection outside a transaction block, so it was left in
    one: each table's transaction after it was a savepoint, and the schema
    lock and the rows' locks were held across the whole backfill."""
    from kazma_core.spaces import scope, threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    threads.record_thread(f"t-{uuid.uuid4().hex[:8]}", "sa", scope.OWNER_ACCOUNT_ID)
    stray = _stamped_row(db, "sd")
    seen: list[Any] = []
    real = lock._backfill_table

    def watched(conn: Any, table: str, *args: Any, **kwargs: Any) -> bool:
        seen.append(conn.info.transaction_status)
        return real(conn, table, *args, **kwargs)

    monkeypatch.setattr(lock, "_backfill_table", watched)
    lock.apply(_another_name_for(db["app"]), vector_table="")
    assert seen and set(seen) == {psycopg.pq.TransactionStatus.IDLE}, seen
    assert _stamps(db, stray) == {None}


# ── the step 4 delta review: where the backfill runs, and its timeout (N4) ──


def test_a_tables_rule_at_the_checkpointers_open_backfills_nothing(db: dict[str, str], lock: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The checkpointer's open (``after_checkpointer_setup``, at every model
    switch) puts rules on its tables and no more: the backfill is a whole
    pass's (step 4 review L6).

    It locks what d170f5c5e already did, so it cannot fail there: the second
    delta review checked it by mutation instead (the backfill on every pass,
    ``and tables is None`` dropped, fails it)."""
    from kazma_core.spaces import scope, threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])  # this install's own database
    threads.record_thread(f"t-{uuid.uuid4().hex[:8]}", "sa", scope.OWNER_ACCOUNT_ID)  # records there are
    stray = _stamped_row(db, "sd")  # a space no record names: a whole pass gives it back to the owner
    monkeypatch.setattr(lock, "_in_place", True)
    lock.after_checkpointer_setup(db["app"])
    assert _stamps(db, stray) == {"sd"}, "the open backfilled nothing"
    assert lock.apply(db["app"], vector_table="").backfilled >= 1  # the control: a whole pass does
    assert _stamps(db, stray) == {None}


@pytest.mark.parametrize("timeout_ms", [50, 0], ids=["its statement timeout", "none (the control)"])
def test_a_backfill_statement_past_its_timeout_leaves_its_table(db: dict[str, str], lock: Any,
                                                                monkeypatch: pytest.MonkeyPatch, timeout_ms: int) -> None:
    """A backfill statement waiting on a row another session holds is stopped
    at its statement timeout, and its table is left for the next pass,
    unchanged (step 4 review L6). With none (the control) it waits the row
    out and changes it.

    It locks what d170f5c5e already did, so it cannot fail there: the second
    delta review checked it by mutation instead (no ``SET LOCAL statement_timeout``,
    or 57014 raised instead of left, fails it)."""
    import threading
    import time

    from kazma_core.spaces import scope, threads

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    monkeypatch.setattr(lock, "_BACKFILL_STATEMENT_MS", timeout_ms)
    threads.record_thread(f"t-{uuid.uuid4().hex[:8]}", "sa", scope.OWNER_ACCOUNT_ID)
    stray = _stamped_row(db, "sd")
    held = threading.Event()

    def hold() -> None:  # another session holds the row for 1.5 s
        with psycopg.connect(db["su"]) as conn:
            conn.execute("SELECT 1 FROM checkpoints WHERE thread_id = %s FOR UPDATE", (stray,))
            held.set()
            time.sleep(1.5)
            conn.rollback()

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(10)
    try:
        done = lock.apply(db["app"], vector_table="", lock_timeout_ms=5000)
    finally:
        holder.join(10)
    if timeout_ms:
        assert any(t.startswith("checkpoints") for t in done.left) and not done.complete, done
        assert _stamps(db, stray) == {"sd"}, "left as it was, for the next pass"
    else:
        assert done.complete and done.backfilled >= 1, done
        assert _stamps(db, stray) == {None}
    _sql(db["app"], "DELETE FROM checkpoints WHERE thread_id = %s", (stray,))
