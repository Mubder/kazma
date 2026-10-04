"""Reply retries preserve their evidence and keep independent tenant claims."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from kazma_core.tenant_context import tenant_scope
from kazma_core.x_api.reply_store import XReplyStore


def claim(store, **kwargs):
    return store.claim(summon_id="123", parent_id="456", target_handle="target", summoner="owner", **kwargs)


def test_same_public_mention_is_independent_per_tenant(tmp_path):
    store = XReplyStore(tmp_path / "replies.db")
    with tenant_scope("one"):
        assert claim(store)
        store.mark_awaiting("123", draft="Private one", subject_id="a")
    with tenant_scope("two"):
        assert claim(store)
        assert store.get("123").draft_text == ""
        store.mark_skipped("123", "private two")
        assert store.release("123")
    with tenant_scope("one"):
        assert store.get("123").draft_text == "Private one"
        assert store.history("123") == []


def test_retry_preserves_snapshot_and_row_identity(tmp_path):
    store = XReplyStore(tmp_path / "replies.db")
    assert claim(store, parent_text="p" * 12000, summon_text="s" * 3000)
    store.mark_awaiting("123", draft="First version", subject_id="a", decision={"checks": ["original"]})
    original = store.get("123")
    assert store.release("123")
    assert store.seen("123")
    held = store.get("123")
    assert held.status == "retry_pending"
    assert not store.claim_approval("123", expected_updated_at=original.updated_at, expected_revision=original.revision)
    assert store.history("123")[0]["record"]["draft_text"] == "First version"
    assert store.history("123")[0]["record"]["decision_json"] == '{"checks": ["original"]}'
    assert len(original.parent_text) == 12000
    assert len(original.summon_text) == 3000
    assert claim(store)
    current = store.get("123")
    assert (current.id, current.created_at) == (original.id, original.created_at)
    assert current.attempt_no == 2
    assert current.draft_text == "" and current.decision == {}
    assert not claim(store)


def test_two_store_instances_only_reopen_and_claim_once(tmp_path):
    first = XReplyStore(tmp_path / "replies.db")
    second = XReplyStore(tmp_path / "replies.db")
    assert claim(first)
    first.mark_skipped("123", "original")
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda s: s.release("123"), [first, second])) == [False, True]
        assert sorted(pool.map(claim, [first, second])) == [False, True]
    assert len(first.history("123")) == 1
    assert first.get("123").attempt_no == 2


def test_legacy_unique_migration_preserves_rows_and_sequence(tmp_path):
    from kazma_core.x_api.reply_store import _CREATE

    path = tmp_path / "legacy.db"
    legacy = _CREATE.replace("summon_id TEXT NOT NULL,", "summon_id TEXT NOT NULL UNIQUE,")
    legacy = legacy.replace(",\n    UNIQUE (tenant_id, summon_id)", "")
    with sqlite3.connect(path) as conn:
        conn.executescript(legacy)
        conn.execute("INSERT INTO x_replies (id, summon_id, created_at, updated_at) VALUES (70, '123', 1, 2)")
        conn.execute("INSERT INTO x_replies (id, summon_id, created_at, updated_at) VALUES (90, 'gone', 1, 2)")
        conn.execute("DELETE FROM x_replies WHERE id = 90")
    store = XReplyStore(path)
    assert store.get("123").id == 70
    assert store.get("123").updated_at == 2
    with tenant_scope("other"):
        assert claim(store)
        assert store.get("123").id > 90
    XReplyStore(path)  # A second boot is a no-op.
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_unknown_and_archived_attempts_cannot_reopen(tmp_path):
    store = XReplyStore(tmp_path / "replies.db")
    assert claim(store)
    store.mark_publish_failure("123", "lost response", outcome="unknown")
    assert not store.release("123")
    assert store.history("123") == []
    store.forget("123")
    assert not claim(store)


def test_cursor_is_bound_to_account_and_tenant_without_adopting_legacy(tmp_path):
    store = XReplyStore(tmp_path / "replies.db")
    store.set_since_id("legacy-boundary")
    assert store.get_since_id(account_id="1") == ""
    store.set_since_id("new-boundary", account_id="1")
    assert store.get_since_id(account_id="2") == ""
    assert store.get_since_id(account_id="1") == "new-boundary"
    with tenant_scope("other"):
        assert store.get_since_id(account_id="1") == ""


def test_x_role_separates_policy_and_credentials_from_review():
    from kazma_core.security.platform_rbac import role_allows

    for path, method in (("/api/x/reply", "PUT"), ("/api/x/credentials", "POST"), ("/api/x/disconnect", "POST")):
        assert not role_allows("operator", path, method)
        assert not role_allows("viewer", path, method)
        assert role_allows("admin", path, method)
    assert role_allows("operator", "/api/x/reply/approve", "POST")
    assert not role_allows("viewer", "/api/x/reply/approve", "POST")
