"""The inbox and history page the complete tenant inventory, including ties."""
from __future__ import annotations

import pytest
from kazma_core.tenant_context import tenant_scope


def collect(first, more, field):
    result = list(first[field])
    cursor = first["next_cursor"]
    while cursor:
        page = more(cursor)
        result.extend(page[field])
        cursor = page["next_cursor"]
    return result


def test_operation_paging_ties_search_and_ownership(tmp_path, monkeypatch):
    from kazma_core.x_api.publication_store import _PublicationStore
    monkeypatch.setattr("kazma_core.x_api.publication_store.time.time", lambda: 1000)
    store = _PublicationStore(tmp_path / "publications.db")
    own = []
    for tenant, count in (("one", 7), ("two", 3)):
        for i in range(count):
            row = store.reserve(tenant_id=tenant, account_id="123", credential_revision="keys", idempotency_key=str(i),
                                text=f"Draft {i} 100% ready", reply_to_id="", due_at=2000+i,
                                max_day=50, max_month=500, duplicate_days=30, origin="schedule")
            if tenant == "one":
                own.append(row["id"])
    first = store.operation_page(tenant_id="one", limit=2)
    rows = collect(first, lambda cursor: store.operation_page(tenant_id="one", limit=2, cursor=cursor), "rows")
    assert first["count"] == 7 and {r["id"] for r in rows} == set(own) and len(rows) == 7
    assert store.operation_page(tenant_id="one", query="100% ready")["count"] == 7
    assert store.operation_page(tenant_id="one", query="Draft 4")["count"] == 1
    with pytest.raises(ValueError):
        store.operation_page(tenant_id="one", cursor="broken")


def test_conversation_paging_search_and_archival(tmp_path, monkeypatch):
    from kazma_core.x_api.reply_store import XReplyStore
    monkeypatch.setattr("kazma_core.x_api.reply_store.time.time", lambda: 1000)
    store = XReplyStore(tmp_path / "replies.db")
    with tenant_scope("one"):
        for i in range(7):
            store.claim(summon_id=str(i), parent_id="parent", target_handle="owner", summoner="reviewer", parent_text="قهوة عربية")
        first = store.conversation_page(limit=2, query="قهوة")
        rows = collect(first, lambda cursor: store.conversation_page(limit=2, query="قهوة", cursor=cursor), "rows")
        assert first["count"] == 7 and len({r.summon_id for r in rows}) == 7
        rec = store.get("1")
        assert store.forget("1", expected_updated_at=rec.updated_at, expected_revision=rec.revision)
        assert store.conversation_page()["count"] == 6
        assert store.conversation_page(state="posted")["count"] == 0
    with tenant_scope("two"):
        assert store.conversation_page()["count"] == 0


def test_draft_paging_is_per_item_and_reaches_old_sets(tmp_path, monkeypatch):
    from kazma_core.agent.artifacts import ArtifactStore
    monkeypatch.setattr("kazma_core.agent.artifacts.time.time", lambda: 1000)
    store = ArtifactStore(tmp_path / "artifacts.db")
    own = []
    for i in range(70):
        proposal = store.save_proposal("one", f"thread-{i}", "x_posts", [f"Coffee {i}-{j}" for j in range(3)])
        own.extend(item["id"] for item in proposal["items"])
    store.save_proposal("two", "thread", "x_posts", ["Private other tenant"])
    first = store.proposal_page(tenant_id="one", limit=17)
    rows = collect(first, lambda cursor: store.proposal_page(tenant_id="one", limit=17, cursor=cursor), "drafts")
    assert first["count"] == 210 and {r["id"] for r in rows} == set(own) and len(rows) == 210
    assert store.proposal_page(tenant_id="one", query="Coffee 69-")["count"] == 3
    store.discard_proposal(own[0], tenant_id="one")
    assert store.proposal_page(tenant_id="one")["count"] == 209
    retired = store.proposal_page(tenant_id="one", only_discarded=True)
    assert retired["count"] == 1 and retired["drafts"][0]["id"] == own[0]
    with pytest.raises(ValueError):
        store.proposal_page(tenant_id="one", cursor="broken")
