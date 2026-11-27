"""The Spaces soak harness works, and sees a crossing (Spaces S4-09).

``scripts/spaces_soak.py`` is the harness for the long run with many spaces
that the outside review (S4-10) reads. A session cannot hold that run, so
this test keeps the harness honest:

* A short soak (the owner, three people, a shared space: five spaces, two
  cycles and three restarts) ends healthy, and it did the work it reports:
  every kind of work in every space, in English and in Arabic, each found in
  the stores themselves -- read here, not taken from the report.
* The report is on disk, whole, while the soak runs.
* Planted crossings are caught, one per kind of store the work writes
  (chats, memory, documents, Knowledge, Scheduled, the swarm), each named
  in the report with its store, row and both spaces: a row of space B
  restamped readable in A, and a row whose space is taken away. A write
  that lands in the wrong space (the harness itself files one write of each
  kind under another space of its writer) is caught in the stores and in
  the reads.
* The checks are not satisfied by an empty store: a run whose work never
  happened (the ledger written, no request sent) fails, and a space with no
  rows of a kind of work fails.
* The row checks' own classes, each with its control: a marker in a store
  the registry calls the install's, in a row that cannot be told a space.

Every soak runs in processes of its own on a throwaway install under the
test's temporary folder: no install's data, no network, no model.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from tests._published_files import read_published_text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import spaces_soak  # noqa: E402

pytestmark = pytest.mark.timeout(600)

#: Each report a reader of the file saw, in order, during the run.
_WRITTEN: list[dict[str, Any]] = []

_PEOPLE, _SHARED, _CYCLES = 3, 1, 2


def _soak(work: Path, report: Path, *extra: str, record: bool = False) -> tuple[int, dict[str, Any]]:
    argv = ["--people", str(_PEOPLE), "--shared", str(_SHARED), "--work-dir", str(work),
            "--report", str(report), *extra]
    if not record:
        code = spaces_soak.main(argv)
    else:
        real_write = spaces_soak._write_report

        def recorded(path: Any, summary: dict[str, Any]) -> bool:
            done = real_write(path, summary)
            if path:
                # What a reader of the file sees at that moment, not what was passed.
                _WRITTEN.append(json.loads(Path(path).read_text(encoding="utf-8")))
            return done

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(spaces_soak, "_write_report", recorded)
            code = spaces_soak.main(argv)
    return code, json.loads(report.read_text(encoding="utf-8"))


def _ledger(work: Path) -> dict[str, Any]:
    return json.loads(read_published_text(work / spaces_soak.LEDGER))


def _store_file(data: Path, name: str) -> Path:
    """The file of the store the registry calls *name*, wherever the data
    folder keeps it (documents.db is in the document store's folder)."""
    from kazma_core import store_registry

    found = [p for p in data.rglob("*.db") if store_registry.store_name_for(p) == name]
    assert len(found) == 1, (name, found)
    return found[0]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    work = tmp_path_factory.mktemp("spaces-soak") / "work"
    report = tmp_path_factory.mktemp("spaces-soak-report") / "report.json"
    code, summary = _soak(work, report, "--cycles", str(_CYCLES), record=True)
    return code, summary, work


# ── a healthy soak ───────────────────────────────────────────────────────


def test_a_short_soak_with_restarts_is_healthy(run):
    code, report, _ = run
    assert code == 0 and report["healthy"], report["violations"]
    assert report["status"] == "passed" and report["violation_count"] == 0
    # Setup, two cycles and the last check: every cycle a process of its own.
    assert report["cycles_done"] == _CYCLES and report["restarts"] == _CYCLES + 1
    assert report["checks_run"] == 2 * _CYCLES + 1


def test_the_run_did_the_work_it_reports(run):
    """A soak that wrote nothing, or wrote it all in one space, passes
    nothing: the spaces, the work and the rows are counted from the stores."""
    _, report, work = run
    ledger = _ledger(work)
    spaces = ledger["spaces"]
    kinds = Counter(s["kind"] for s in spaces.values())
    assert kinds == {"owner": 1, "personal": _PEOPLE, "shared": _SHARED}, kinds
    assert report["spaces"] == {"total": len(spaces), "personal": _PEOPLE, "shared": _SHARED,
                                "people": _PEOPLE + 1}
    # Swarm work only where the owner is a member (only the admin starts it).
    owned = [sid for sid, s in spaces.items() if "owner" in s["members"]]
    expected = {d: len(spaces) * _CYCLES for d in spaces_soak.DOMAINS}
    expected["swarm"] = len(owned) * _CYCLES
    assert report["marks_by_domain"] == expected
    assert set(report["marks_by_language"]) == {"en", "ar"}
    work_done = report["work"]
    for step in ("memory_store", "memory_search", "schedule_task", "soak_publish_page", "document",
                 "knowledge_library"):
        assert work_done[step] == len(spaces) * _CYCLES, (step, work_done)
    assert work_done["dispatch_swarm"] == len(owned) * _CYCLES
    assert work_done["chat_turn"] == (len(spaces) + len(owned)) * _CYCLES
    # The rows are there, each in its own space: read from the stores here.
    data = work / spaces_soak.DATA
    by_space: Counter[str] = Counter()
    for mark, entry in ledger["marks"].items():
        by_space[entry["space"]] += 1
    for db, sql, kind in (
        ("chat_sessions.db", "SELECT tenant_id, messages FROM sessions", "memory"),
        ("cron.db", "SELECT tenant_id, prompt FROM cron_jobs", "scheduled"),
        ("documents.db", "SELECT tenant_id, title FROM documents", "document"),
        ("knowledge.db", "SELECT tenant_id, name FROM knowledge_libraries", "knowledge"),
        ("memory_state.db", "SELECT tenant_id, object FROM beliefs", "memory"),
    ):
        with closing(sqlite3.connect(_store_file(data, db))) as conn:
            rows = conn.execute(sql).fetchall()
        found = Counter()
        for tenant, text in rows:
            for mark in spaces_soak.MARK.findall(str(text)):
                if ledger["marks"].get(mark, {}).get("domain") == kind:
                    assert ledger["marks"][mark]["space"] == tenant, (db, mark, tenant)
                    found[tenant] += 1
        assert set(found) == set(spaces), (db, found)
    with closing(sqlite3.connect(_store_file(data, "swarm_tasks.db"))) as conn:
        tasks = [json.loads(m or "{}").get("tenant_id") for (m,) in conn.execute("SELECT metadata FROM swarm_tasks")]
    assert Counter(tasks) == {sid: _CYCLES for sid in owned}


def test_every_check_read_every_store_and_route(run):
    """What the checks read: every store the work writes, each told its
    space as its rule says, and every read path for every member."""
    _, report, work = run
    last = report["last_check"]
    stores = last["stores"]
    for name, told in (("chat_sessions.db", "tenant_id"), ("cron.db", "tenant_id"),
                       ("documents.db", "tenant_id"), ("knowledge.db", "library"),
                       ("memory_state.db", "tenant_id"), ("swarm_tasks.db", "metadata.tenant_id"),
                       ("checkpoints_*.db", "thread record")):
        assert name in stores and stores[name]["marked_rows"], (name, sorted(stores))
        assert any(told in t for t in stores[name]["tables"].values()), (name, stores[name])
    assert stores["(space folders)"]["marked_rows"]
    ledger = _ledger(work)
    memberships = sum(len(s["members"]) for s in ledger["spaces"].values())
    reads = last["reads"]
    for route in spaces_soak._SHOWN_BY.values():
        for path in route:
            assert reads[path] == memberships, (path, reads)
    assert any(k.startswith("non-member ") for k in reads) and any(k.startswith("names no space ") for k in reads)
    assert reads["nothing bound, refused: chat store"] == 1
    assert last["marks_found_in_store"] == len(ledger["marks"])


def test_the_report_is_on_disk_while_the_soak_runs(run):
    """Whole, after the setup and every cycle, naming the process that
    writes it; then once more with the verdict."""
    statuses = [seen["status"] for seen in _WRITTEN]
    assert statuses[0] == "running" and statuses[-1] == "passed", statuses
    running = [seen for seen in _WRITTEN if seen["status"] == "running"]
    assert len(running) == _CYCLES + 1, statuses
    assert all(seen["pid"] == os.getpid() and seen["pid_started"] for seen in running)
    assert [seen["cycles_done"] for seen in running] == list(range(_CYCLES + 1))


# ── planted crossings ────────────────────────────────────────────────────

#: Per kind of store: its file, its table, how a row of a marker is found,
#: and how its space is restamped. ``space`` is the new space ('' for none).
_PLANTS: dict[str, tuple[str, str]] = {
    "chat": ("chat_sessions.db", "sessions"),
    "memory": ("memory_state.db", "beliefs"),
    "document": ("documents.db", "documents"),
    "knowledge": ("knowledge.db", "knowledge_libraries"),
    "scheduled": ("cron.db", "cron_jobs"),
    "swarm": ("swarm_tasks.db", "swarm_tasks"),
}


def _restamp(data: Path, domain: str, mark: str, space: str) -> str:
    """Give the row of *domain* holding *mark* the space *space*; its rowid."""
    db, table = _PLANTS[domain]
    with closing(sqlite3.connect(_store_file(data, db))) as conn, conn:
        hits = [(rowid, row) for rowid, *row in conn.execute(f'SELECT rowid, * FROM "{table}"')  # noqa: S608
                if any(mark in str(v) for v in row)]
        assert len(hits) == 1, (domain, mark, len(hits))
        rowid = hits[0][0]
        if domain == "swarm":
            (meta,) = conn.execute("SELECT metadata FROM swarm_tasks WHERE rowid = ?", (rowid,)).fetchone()
            held = json.loads(meta or "{}")
            if space:
                held["tenant_id"] = space
            else:
                held.pop("tenant_id", None)
            conn.execute("UPDATE swarm_tasks SET metadata = ? WHERE rowid = ?", (json.dumps(held), rowid))
        else:
            conn.execute(f'UPDATE "{table}" SET tenant_id = ? WHERE rowid = ?', (space, rowid))  # noqa: S608
    return str(rowid)


def _mark_of(ledger: dict[str, Any], space: str, domain: str, cycle: int) -> str:
    [mark] = [m for m, e in ledger["marks"].items()
              if e["space"] == space and e["domain"] == domain and e["cycle"] == cycle]
    return mark


@pytest.fixture(scope="module")
def planted(run, tmp_path_factory):
    """A copy of the healthy run's work folder with two crossings planted in
    each kind of store, checked again by a fresh boot."""
    _, _, work = run
    copy = tmp_path_factory.mktemp("spaces-soak-planted") / "work"
    shutil.copytree(work, copy)
    ledger = _ledger(copy)
    personal = sorted(s for s, e in ledger["spaces"].items() if e["kind"] == "personal")
    shared = next(s for s, e in ledger["spaces"].items() if e["kind"] == "shared")
    owner = next(s for s, e in ledger["spaces"].items() if e["kind"] == "owner")
    plants: list[dict[str, str]] = []
    for domain in _PLANTS:
        # B's row made readable in A: two personal spaces, or the owner's and
        # a shared space for the swarm (the only spaces with swarm rows).
        a, b = (owner, shared) if domain == "swarm" else (personal[0], personal[1])
        mark = _mark_of(ledger, b, domain, 0)
        _restamp(copy / spaces_soak.DATA, domain, mark, a)
        plants.append({"kind": spaces_soak.CROSSED, "domain": domain, "mark": mark, "mark_space": b,
                       "row_space": a, "store": _PLANTS[domain][0]})
        # A row of B's whose space is taken away.
        mark = _mark_of(ledger, b, domain, 1)
        _restamp(copy / spaces_soak.DATA, domain, mark, "")
        plants.append({"kind": spaces_soak.NO_SPACE, "domain": domain, "mark": mark, "mark_space": b,
                       "row_space": "", "store": _PLANTS[domain][0]})
    report = copy.parent / "report.json"
    code, summary = _soak(copy, report, "--check-only")
    return code, summary, plants


def test_planted_crossings_fail_the_check(planted):
    code, report, _ = planted
    assert code == 1 and report["status"] == "failed" and not report["healthy"]


@pytest.mark.parametrize("domain", list(_PLANTS))
def test_a_row_of_b_readable_in_a_is_named(planted, domain):
    """The store, the table, the row, its marker and both spaces."""
    _, report, plants = planted
    plant = next(p for p in plants if p["domain"] == domain and p["kind"] == spaces_soak.CROSSED)
    named = [v for v in report["violations"] if v["kind"] == spaces_soak.CROSSED and v["mark"] == plant["mark"]
             and v["store"] == plant["store"] and v["table"] == _PLANTS[domain][1]]
    assert named, (plant, [v for v in report["violations"] if v.get("mark") == plant["mark"]])
    assert named[0]["mark_space"] == plant["mark_space"] and named[0]["row_space"] == plant["row_space"]
    assert named[0]["row"].startswith(_PLANTS[domain][1] + ".")
    # And A's own reads show it, which is the leak the row is.
    shown = [v for v in report["violations"] if v["kind"] == spaces_soak.READ_CROSSED
             and v["mark"] == plant["mark"] and v["read_space"] == plant["row_space"]]
    assert shown, (domain, plant)


@pytest.mark.parametrize("domain", list(_PLANTS))
def test_a_row_with_no_space_is_named(planted, domain):
    _, report, plants = planted
    plant = next(p for p in plants if p["domain"] == domain and p["kind"] == spaces_soak.NO_SPACE)
    named = [v for v in report["violations"] if v["kind"] == spaces_soak.NO_SPACE and v["mark"] == plant["mark"]
             and v["store"] == plant["store"]]
    assert named, (plant, [v for v in report["violations"] if v.get("mark") == plant["mark"]])
    assert named[0]["mark_space"] == plant["mark_space"] and named[0]["row_space"] == ""


def test_a_planted_check_finds_nothing_it_was_not_given(planted):
    """Every crossing the check names is a planted row, a row that follows
    one (a restamped library's chunks), or a read of one: nothing else."""
    _, report, plants = planted
    marks = {p["mark"] for p in plants}
    stray = [v for v in report["violations"] if v.get("mark") not in marks
             and v["kind"] not in (spaces_soak.LOST,)]
    assert not stray, stray[:5]


@pytest.fixture(scope="module")
def wrong_space(tmp_path_factory):
    work = tmp_path_factory.mktemp("spaces-soak-wrong") / "work"
    report = work.parent / "report.json"
    return _soak(work, report, "--cycles", "1", "--plant-wrong-space", ",".join(spaces_soak.DOMAINS))


@pytest.mark.parametrize("domain", spaces_soak.DOMAINS)
def test_a_write_that_lands_in_the_wrong_space_is_named(wrong_space, domain):
    """The harness files one write of each kind under another space of its
    writer: the row is named in the stores, and the reads of that space
    show it."""
    code, report = wrong_space
    assert code == 1 and report["status"] == "failed"
    stored = [v for v in report["violations"] if v["kind"] == spaces_soak.CROSSED
              and spaces_soak._DOMAIN_OF[v["mark"][-4:-2]] == domain]
    assert stored, (domain, report["violations_by_kind"])
    assert all(v["mark_space"] != v["row_space"] and v["store"] and v["row"] for v in stored)
    shown = [v for v in report["violations"] if v["kind"] == spaces_soak.READ_CROSSED
             and spaces_soak._DOMAIN_OF[v["mark"][-4:-2]] == domain]
    assert shown, (domain, report["violations_by_kind"])


# ── no empty pass ────────────────────────────────────────────────────────


def test_control_a_soak_whose_work_never_happened_fails(tmp_path):
    """The ledger is written and no request is sent: every kind of work is
    missing from the stores and the reads, and nothing is called a crossing."""
    code, report = _soak(tmp_path / "work", tmp_path / "report.json", "--cycles", "1", "--control-no-work")
    assert code == 1 and report["status"] == "failed"
    lost = [v for v in report["violations"] if v["kind"] == spaces_soak.LOST]
    assert {v["where"] for v in lost} == {"store", "reads"}
    assert {v["domain"] for v in lost} == set(spaces_soak.DOMAINS)
    assert set(report["violations_by_kind"]) == {spaces_soak.LOST}


def _ledger_of(spaces: dict[str, list[str]], marks: dict[str, dict[str, Any]] | None = None) -> Any:
    ledger = spaces_soak.Ledger(path=Path(os.devnull), run="abcdef")
    ledger.spaces = {sid: {"index": n, "kind": "shared", "members": members}
                     for n, (sid, members) in enumerate(spaces.items())}
    ledger.marks = marks or {}
    return ledger


def test_a_space_with_no_rows_of_a_kind_fails():
    ledger = _ledger_of({"s1": ["owner", "p0"], "s2": ["p1"]})
    check = spaces_soak.Check(stage="test")
    spaces_soak._check_kept(check, ledger, range(0, 1))
    empty = {(v["mark_space"], v["domain"]) for v in check.violations if v["kind"] == spaces_soak.EMPTY}
    assert empty == {("s1", d) for d in spaces_soak.DOMAINS} | {
        ("s2", d) for d in spaces_soak.DOMAINS if d != "swarm"}


def test_control_a_space_with_its_rows_is_not_empty():
    mark = "sskabcdefs00c000do01"
    ledger = _ledger_of({"s1": ["p0"]}, {mark: {"space": "s1", "domain": "document", "cycle": 0}})
    check = spaces_soak.Check(stage="test")
    check.found_in_store["s1"] = {mark}
    check.seen_by_space["s1|p0|document"] = {mark}
    spaces_soak._check_kept(check, ledger, range(0, 1))
    assert not [v for v in check.violations if v["domain"] == "document"], check.violations


# ── the row checks' classes ──────────────────────────────────────────────


def _judged(rule: str, row: dict[str, Any], how: str, space: str) -> list[str]:
    mark = "sskabcdefs00c000ch01"
    ledger = _ledger_of({"s1": ["p0"], "s2": ["p1"]}, {mark: {"space": "s1", "domain": "chat", "cycle": 0}})
    check = spaces_soak.Check(stage="test")
    spaces_soak._judge_row(check, ledger, store="x.db", rule=rule, table="t", row={"id": 1, "text": mark, **row},
                           how=how, space=space)
    return [v["kind"] for v in check.violations]


def test_each_kind_of_row_is_judged():
    assert _judged("row", {}, "tenant_id", "s1") == []  # the control: its own space
    assert _judged("row", {}, "tenant_id", "s2") == [spaces_soak.CROSSED]
    assert _judged("row", {}, "tenant_id", "") == [spaces_soak.NO_SPACE]
    assert _judged("row", {}, "", "") == [spaces_soak.UNTOLD]
    assert _judged("install", {}, "tenant_id", "s1") == [spaces_soak.INSTALL_WORDS]
    assert _judged("undeclared", {}, "tenant_id", "s1") == [spaces_soak.INSTALL_WORDS]


def test_a_row_is_told_its_space_as_its_store_keeps_it():
    owners = spaces_soak.Owners(threads={"t1": "s1"}, libraries={"lib1": "s2"})
    told = spaces_soak._row_space
    assert told("x", {"tenant_id": "s1"}, owners) == ("tenant_id", "s1")
    assert told("x", {"space_id": " s1 "}, owners) == ("space_id", "s1")
    assert told("swarm_tasks", {"metadata": json.dumps({"tenant_id": "s2"})}, owners) == ("metadata.tenant_id", "s2")
    assert told("checkpoints", {"thread_id": "t1"}, owners) == ("thread record", "s1")
    assert told("checkpoints", {"thread_id": "t9"}, owners) == ("thread record", "")
    assert told("knowledge_chunks", {"library_id": "lib1"}, owners) == ("library", "s2")
    assert told("x", {"other": 1}, owners) == ("", "")


def test_full_text_tables_are_left_to_the_table_they_index():
    for name in ("episodes_fts", "beliefs_fts_data", "knowledge_chunks_fts_idx", "sqlite_sequence"):
        assert spaces_soak._skipped_table(name), name
    for name in ("episodes", "beliefs", "knowledge_chunks", "documents", "cron_jobs"):  # the control
        assert not spaces_soak._skipped_table(name), name


# ── Postgres ─────────────────────────────────────────────────────────────


@pytest.mark.postgres
def test_a_short_soak_on_postgres_is_healthy(tmp_path):
    """The same soak with Kazma's stores on a throwaway Postgres, its tables
    read whole by the check."""
    if not os.environ.get("KAZMA_DATABASE_URL") or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    code, report = _soak(tmp_path / "work", tmp_path / "report.json", "--cycles", "1",
                         "--database-url-from", "KAZMA_DATABASE_URL")
    assert code == 0 and report["healthy"], report["violations"][:10]
    assert report["database"] == "postgres"
    assert any(name.startswith("postgres:") for name in report["last_check"]["stores"]), report["last_check"]
