"""Files and in-process maps that only grew now have a bound (2026-11 audit).

- FA-203: loop-stall dumps (``.kazma/stall-*.txt``) were never removed.
- FA-204: a document-store backup folder that does not verify (an older
  build's format) was never pruned.
- FA-224: ``kazma acquisition status``, a read-only diagnostic, tried a
  clean-up delete in the crawl journal and was refused.
- FA-143: the guard's own log (``guard.log``) never rotated.
- FA-144: backup and bundle manifests were written in place; a crash left a
  truncated manifest.
- FA-145: the alert throttle and the approval-card counter kept every key.
- FA-142: SQLite WAL files kept their largest size; ``settings.db`` kept the
  moved Knowledge tables' pages -- and their text -- free in the file.

Each with its negative control (the old behaviour as a stand-in, a planted
violation, or the unbounded setting).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def own(tmp_path):
    """A folder of the test's own: the suite's fixtures keep stores in tmp_path."""
    folder = tmp_path / "own"
    folder.mkdir()
    return folder

# ── FA-203 ───────────────────────────────────────────────────────────────────


def test_stall_dumps_keep_the_newest_within_the_cap_and_age(own, monkeypatch):
    from kazma_core.observability import loop_stall

    monkeypatch.setattr(loop_stall, "_DUMP_MAX", 50)
    now = time.time()
    for i in range(60):
        dump = own / f"stall-{i:03d}.txt"
        dump.write_text("x")
        os.utime(dump, (now - i, now - i))
    ancient = own / "stall-ancient.txt"
    ancient.write_text("x")
    os.utime(ancient, (now - 40 * 86400,) * 2)
    removed = loop_stall._prune_dumps(own)
    left = sorted(p.name for p in own.glob("stall-*.txt"))
    assert removed == 11 and len(left) == 50 and "stall-ancient.txt" not in left
    assert "stall-000.txt" in left and "stall-059.txt" not in left


def test_negative_control_a_cap_of_everything_removes_nothing_new(own, monkeypatch):
    from kazma_core.observability import loop_stall

    monkeypatch.setattr(loop_stall, "_DUMP_MAX", 10_000)
    for i in range(60):
        (own / f"stall-{i:03d}.txt").write_text("x")
    assert loop_stall._prune_dumps(own) == 0


# ── FA-204 ───────────────────────────────────────────────────────────────────


def _backup_folders(tmp_path, names_ages):
    now = time.time()
    for name, age in names_ages:
        folder = tmp_path / f"document-store-{name}"
        folder.mkdir()
        os.utime(folder, (now - age, now - age))


def test_an_old_unverified_backup_goes_with_the_rest(own, monkeypatch):
    from kazma_core.documents import backup

    monkeypatch.setattr(backup, "verify_document_backup",
                        lambda *, backup_dir: {"ok": "good" in backup_dir.name})
    _backup_folders(own, [("good-1", 100), ("good-2", 200), ("good-3", 300),
                               ("oldformat", 1000), ("failed-new", 50)])
    backup._prune_old(own, retention=2)
    left = sorted(p.name.removeprefix("document-store-") for p in own.iterdir())
    assert left == ["failed-new", "good-1", "good-2"]


def test_with_no_verified_backup_nothing_is_removed(own, monkeypatch):
    from kazma_core.documents import backup

    monkeypatch.setattr(backup, "verify_document_backup", lambda *, backup_dir: {"ok": False})
    _backup_folders(own, [("oldformat-1", 1000), ("oldformat-2", 2000)])
    assert backup._prune_old(own, retention=1) == 0
    assert len(list(own.iterdir())) == 2


# ── FA-224 ───────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return True


def test_a_diagnostic_makes_no_clean_up_write(monkeypatch):
    from kazma_core.diagnostic_scope import read_only_diagnostic
    from kazma_core.web_acquire import crawl_jobs

    monkeypatch.setattr(crawl_jobs, "_retired_cleared", False)
    store = _Store()
    with read_only_diagnostic("acquisition status"):
        crawl_jobs._clear_retired(store)
    assert store.deleted == []
    crawl_jobs._clear_retired(store)  # negative control: outside one it writes
    assert store.deleted == [crawl_jobs._RETIRED_KB_JOBS_KEY]


# ── FA-143 ───────────────────────────────────────────────────────────────────


def _guard():
    spec = importlib.util.spec_from_file_location("_bounded_kazma_guard", ROOT / "scripts" / "service" / "kazma_guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_guard_log_rotates_to_one_sibling(own, monkeypatch):
    guard = _guard()
    monkeypatch.setattr(guard, "GUARD_LOG_MAX_BYTES", 100_000)
    log = guard.GuardLog(own / "guard.log", echo=False)
    pad = "x" * 1000
    for _ in range(300):  # ~300 KB written
        log("info", "test.event", pad=pad)
    files = sorted(p.name for p in own.iterdir())
    assert files == ["guard.log", "guard.log.1"]
    assert (own / "guard.log").stat().st_size <= 100_000 + 2000


def test_negative_control_without_a_limit_one_file_grows(own, monkeypatch):
    guard = _guard()
    monkeypatch.setattr(guard, "GUARD_LOG_MAX_BYTES", 10**12)
    log = guard.GuardLog(own / "guard.log", echo=False)
    for _ in range(300):
        log("info", "test.event", pad="x" * 1000)
    assert sorted(p.name for p in own.iterdir()) == ["guard.log"]


# ── FA-144 ───────────────────────────────────────────────────────────────────

#: Manifest writes outside the atomic writer, with why.
_DECLARED_MANIFEST_WRITES = {
    "kazma-core/kazma_core/documents/hostile_corpus.py": "builds the committed test corpus's manifest, "
                                                         "a repository file, not a backup",
    "kazma-core/kazma_core/hub/cli.py": "writes the skill manifest the author is editing",
}


def _manifest_writes(source: str) -> list[int]:
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("write_text", "write_bytes")
            and "manifest" in ast.unparse(node.func.value).lower()]


def test_every_manifest_is_written_whole():
    found = {}
    for path in sorted((ROOT / "kazma-core" / "kazma_core").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        lines = _manifest_writes(path.read_text(encoding="utf-8"))
        if lines and rel not in _DECLARED_MANIFEST_WRITES:
            found[rel] = lines
    assert not found, f"write manifests with kazma_core.atomic_files.atomic_write_text: {found}"


def test_negative_control_a_planted_manifest_write_is_found():
    assert _manifest_writes("(out / 'manifest.json').write_text(x)\nmanifest_path.write_text(y)\n") == [1, 2]


def test_an_atomic_write_that_fails_leaves_the_old_file(own, monkeypatch):
    from kazma_core import atomic_files

    target = own / "manifest.json"
    target.write_text('{"old": true}')

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_files.os, "replace", broken)
    with pytest.raises(OSError):
        atomic_files.atomic_write_text(target, '{"new": true}')
    assert target.read_text() == '{"old": true}'
    assert [p.name for p in own.iterdir()] == ["manifest.json"]  # no temp file left


# ── FA-145 ───────────────────────────────────────────────────────────────────


def test_the_alert_throttle_keeps_at_most_its_cap(monkeypatch):
    from collections import OrderedDict

    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(ops_alerts, "_state", OrderedDict())
    for i in range(10_000):
        ops_alerts._should_send(f"run.{i}", 900.0)
    assert len(ops_alerts._state) == ops_alerts._STATE_MAX
    assert "run.9999" in ops_alerts._state and "run.0" not in ops_alerts._state


def test_the_card_counter_keeps_at_most_its_cap(monkeypatch):
    from collections import OrderedDict

    from kazma_ui import turn_document

    monkeypatch.setattr(turn_document, "_hitl_emit_seq", OrderedDict())
    for i in range(10_000):
        turn_document.assign_interrupt_id({"tool": "x"}, thread_id=f"t-{i}")
    assert len(turn_document._hitl_emit_seq) == turn_document._HITL_SEQ_MAX
    assert "t-9999" in turn_document._hitl_emit_seq and "t-0" not in turn_document._hitl_emit_seq


# ── FA-142 ───────────────────────────────────────────────────────────────────


def test_a_store_opened_through_the_helper_has_a_journal_size_limit(tmp_path):
    from kazma_core.config_store import SQLITE_JOURNAL_SIZE_LIMIT, apply_sqlite_pragmas

    conn = sqlite3.connect(tmp_path / "a.db")
    try:
        apply_sqlite_pragmas(conn)
        assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] == SQLITE_JOURNAL_SIZE_LIMIT
    finally:
        conn.close()


def _settings_with_dropped_text(path: Path) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, text TEXT)")
    conn.executemany("INSERT INTO knowledge_chunks (text) VALUES (?)",
                     [(f"PLANTED-LIBRARY-TEXT-{i} " * 40,) for i in range(400)])
    conn.execute("DROP TABLE knowledge_chunks")
    conn.close()


def test_compacting_settings_db_removes_the_dropped_tables_text(own):
    from kazma_core.stores.knowledge import _compact_settings_db

    path = own / "settings.db"
    _settings_with_dropped_text(path)
    assert b"PLANTED-LIBRARY-TEXT" in path.read_bytes()  # the negative control: dropped, still there
    before = path.stat().st_size
    assert _compact_settings_db(str(path))
    assert b"PLANTED-LIBRARY-TEXT" not in path.read_bytes() and path.stat().st_size < before
