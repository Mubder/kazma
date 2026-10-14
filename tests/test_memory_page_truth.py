"""The Memory page reports what actually happened (Memory audit M32,
MEM-S08).

Found 2026-10-13: hygiene answered ``ok`` over a failed archive (the page
said "Hygiene complete"); the Episodes chip counted forgotten tombstones and
retired copies; the archive took at most 2,000 ended facts a run without
saying so; the probe panel dropped weekly summaries; and server messages
reached the Arabic page in English. Each check has its negative control.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    db.close()


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import memory_api

    app = FastAPI()
    memory_api.mount_memory_api(app)
    return TestClient(app)


def test_hygiene_is_not_ok_over_a_failed_archive(mem):
    from kazma_ui import memory_api

    def locked():
        raise sqlite3.OperationalError("database is locked")

    mem.mp.setattr(memory_api, "_conn", locked)
    d = _client().post("/api/memory/v2/hygiene/run", json={"archive_invalidated": True}).json()
    assert d["ok"] is False and d["failed"] == ["archive_invalidated"] and d["error"] == "hygiene_partial"


def test_a_clean_hygiene_run_is_ok(mem):
    d = _client().post("/api/memory/v2/hygiene/run", json={"archive_invalidated": True}).json()
    assert d["ok"] is True and d["actions"]["archive_invalidated"]["remaining"] == 0


def test_the_archive_says_what_its_cap_left(mem):
    from kazma_ui import memory_api

    mem.mp.setattr(memory_api, "_ARCHIVE_PER_RUN", 3)
    now = time.time()
    mem.db.executemany(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, valid_from, "
        "ingested_at, valid_until) VALUES (?, 'default', 'user', 'likes', 'set', ?, 0.5, ?, ?, ?)",
        [(f"b{i}", f"x{i}", now - 10, now - 10, now - 5) for i in range(5)])
    mem.db.commit()
    out = memory_api._archive_invalidated_sync()
    assert out == {"archived": 3, "remaining": 2, "cap": 3}


def test_the_episodes_chip_counts_only_what_recall_can_show(mem):
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_core.memory.forget import forget_episode
    from kazma_ui import memory_api

    eid = mirror_episode(session_id="chat-2", turn_number=1, user_text="the pump housings come from Nordvalve",
                         assistant_text="Noted.")
    assert memory_api._memory_admin_summary_sync()["episodes"] == 1
    assert forget_episode(eid, tenant_id="default")["ok"]
    assert memory_api._memory_admin_summary_sync()["episodes"] == 0
    # Negative control: the old count included the tombstone.
    assert mem.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1


def test_server_messages_are_codes_the_page_words(mem):
    from kazma_core.memory.profile import ABOUT_MAX_CHARS, set_about

    out = set_about("default", "x" * (ABOUT_MAX_CHARS + 1))
    assert out["code"] == "about_too_long" and out["length"] == ABOUT_MAX_CHARS + 1
    assert out["max"] == ABOUT_MAX_CHARS and " " not in out["error"]
    mem.db.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('user', 'default', 'person', 'You')")
    mem.db.commit()
    d = _client().delete("/api/memory/v2/entities/user").json()
    assert d.get("code") == "protected" and "protected entity" not in d.get("error", "")


def test_the_page_scripts_word_the_codes_and_show_summaries():
    """The client half: each code has a catalog key in both languages, and the
    probe panel renders weekly summaries."""
    from kazma_ui.i18n.catalog.memory import TRANSLATIONS as MEM
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS as SET

    for key in ("memory.err.not_found", "memory.err.protected", "memory.page.hygiene_partial",
                "memory.page.hygiene_more_left", "memory.console.forgotten_remote_pending",
                "memory.console.hit_summary", "memory.console.probe_degraded"):
        assert MEM[key]["en"] and MEM[key]["ar"], key
    assert SET["settings.about_me_too_long"]["ar"]
    console = (REPO / "kazma-ui/kazma_ui/static/js/memory_console.js").read_text(encoding="utf-8")
    assert "data.weekly_summaries" in console and "remote_vector_deleted === false" in console
    page = (REPO / "kazma-ui/kazma_ui/static/js/memory.js").read_text(encoding="utf-8")
    assert "toast(d.error ||" not in page  # every error goes through errText
