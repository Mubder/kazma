"""Ordered manifests cannot resend known/unknown segments or overbook a thread."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kazma_core.x_api import config, ledger, publication_service, schedule
    from kazma_core.x_api.config import XCredentials, get_x_config

    state = {"cfg": replace(get_x_config(), enabled=True, account_id="123", handle="kazma",
                            credentials=XCredentials("k", "s", "t", "ts"), max_posts_per_day=10),
             "calls": [], "failure": None, "fail_at": 2, "gate": None}
    monkeypatch.setattr(config, "get_x_config", lambda: state["cfg"])
    monkeypatch.setattr(ledger, "_ledger", ledger.XPostLedger(tmp_path / "posts.db"))
    schedule.reset_x_scheduled_store(tmp_path / "schedule.db")

    class Client:
        async def create_tweet(self, text, *, reply_to_id=""):
            state["calls"].append((text, reply_to_id))
            if state["gate"]:
                await state["gate"].wait()
            if len(state["calls"]) == state["fail_at"] and state["failure"]:
                raise state["failure"]
            return {"id": str(900 + len(state["calls"]))}

    monkeypatch.setattr(publication_service, "_client", lambda cfg: Client())
    return state


def _stage():
    from kazma_core.x_api.threads import prepare_thread

    return prepare_thread(["First post", "Second post", "Third post"], intent_key="ordered")


async def _run(detail):
    from kazma_core.x_api.threads import publish_thread

    return await publish_thread(detail["id"], revision=detail["revision"], token=detail["approval_token"], actor="operator")


async def test_exact_order_uses_confirmed_predecessors_and_no_generic_send(env):
    from kazma_core.x_api import publication_service

    detail = _stage()
    assert not env["calls"]
    ok, _ = await publication_service._dispatch_operation(detail["segments"][0]["operation_id"])
    assert not ok and not env["calls"]
    result = await _run(detail)
    assert result["state"] == "published" and result["published_count"] == 3
    assert env["calls"] == [("First post", ""), ("Second post", "901"), ("Third post", "902")]
    assert [event["event"] for event in result["history"]] == ["published", "approved_remaining", "staged"]
    assert result["history"][1]["actor"] == "operator"
    with pytest.raises(ValueError):
        await _run(detail)
    assert len(env["calls"]) == 3


def test_reservations_all_commit_or_all_rollback(env):
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.thread_store import thread_page

    env["cfg"] = replace(env["cfg"], max_posts_per_day=2)
    with pytest.raises(ValueError, match="cap"):
        _stage()
    assert not thread_page()["threads"]
    assert not get_publication_store().list_operations(tenant_id="default")


async def test_partial_known_unsent_resumes_without_first_post(env):
    from kazma_core.x_api.client import XApiError

    env["failure"] = XApiError("Definitely unsent", outcome="not_sent")
    partial = await _run(_stage())
    assert partial["published_count"] == 1 and partial["state"] == "partial"
    assert partial["segments"][1]["state"] == "failed_permanent"
    env["failure"] = None
    result = await _run(partial)
    assert result["state"] == "published"
    assert [text for text, _ in env["calls"]].count("First post") == 1
    assert env["calls"][-1] == ("Third post", "903")


async def test_partial_unknown_never_resumes_or_rebooks(env):
    from kazma_core.x_api.client import XApiError

    env["failure"] = XApiError("Reply lost after dispatch", outcome="unknown")
    partial = await _run(_stage())
    assert partial["state"] == "outcome_unknown" and not partial["can_resume"]
    with pytest.raises(ValueError, match="unknown"):
        await _run(partial)
    with pytest.raises(ValueError, match="Duplicate"):
        from kazma_core.x_api.threads import prepare_thread

        prepare_thread(["First post", "Second post"], intent_key="another")
    assert len(env["calls"]) == 2


async def test_concurrent_approval_has_one_owner(env):
    detail = _stage()
    env["gate"] = asyncio.Event()
    first = asyncio.create_task(_run(detail))
    for _ in range(100):
        if env["calls"]:
            break
        await asyncio.sleep(.01)
    with pytest.raises(ValueError, match="claimed|segment"):
        await _run(detail)
    env["gate"].set()
    await first
    assert len(env["calls"]) == 3


async def test_credentials_change_invalidates_whole_thread(env):
    from kazma_core.x_api.config import XCredentials

    detail = _stage()
    env["cfg"] = replace(env["cfg"], credentials=XCredentials("other", "s", "t", "ts"))
    with pytest.raises(ValueError, match="credentials"):
        await _run(detail)
    assert not env["calls"]


async def test_cancel_partial_retains_published_and_unknown(env):
    from kazma_core.x_api.client import XApiError
    from kazma_core.x_api.thread_store import cancel_thread

    env["failure"] = XApiError("Unknown", outcome="unknown")
    partial = await _run(_stage())
    cancelled = cancel_thread(partial["id"], revision=partial["revision"], token=partial["approval_token"])
    assert [segment["state"] for segment in cancelled["segments"]] == ["published", "outcome_unknown", "cancelled"]
    assert cancelled["state"] == "outcome_unknown" and not cancelled["can_resume"]
    assert not cancelled["can_cancel"]
    with pytest.raises(ValueError):
        cancel_thread(cancelled["id"], revision=cancelled["revision"], token="")


async def test_expired_approval_requires_fresh_review(env):
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.thread_store import review_thread

    detail = _stage()
    with get_publication_store()._connection(transaction=True) as conn:
        conn.execute("UPDATE x_threads SET expires_at = ?", (time.time() - 1,))
    with pytest.raises(ValueError, match="expired"):
        await _run(detail)
    reviewed = review_thread(detail["id"], revision=detail["revision"])
    assert reviewed["revision"] > detail["revision"]
    assert (await _run(reviewed))["state"] == "published"


async def test_restore_invalidates_thread_approval_and_requires_review(env):
    from kazma_core.x_api.restore import pause_restored_publishing
    from kazma_core.x_api.thread_store import review_thread

    detail = _stage()
    pause_restored_publishing()
    with pytest.raises(ValueError):
        await _run(detail)
    from kazma_core.x_api.thread_store import thread_detail

    restored = thread_detail(detail["id"])
    assert not restored["approval_token"] and restored["revision"] > detail["revision"]
    refreshed = review_thread(detail["id"], revision=restored["revision"])
    assert refreshed["approval_token"] and not env["calls"]


def test_crash_after_activation_requires_review_before_a_known_unsent_resume(env):
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.thread_store import _activate_segment, _claim_thread, review_thread, thread_detail

    detail = _stage()
    owner = _claim_thread(detail["id"], revision=detail["revision"], token=detail["approval_token"], cfg=env["cfg"], actor="interrupted")
    _activate_segment(detail["id"], 0, owner=owner)
    with get_publication_store()._connection(transaction=True) as conn:
        conn.execute("UPDATE x_threads SET lease_until = ?", (time.time() - 1,))
    interrupted = thread_detail(detail["id"])
    assert interrupted["can_review"] and not interrupted["approval_token"]
    reviewed = review_thread(detail["id"], revision=interrupted["revision"])
    assert reviewed["segments"][0]["state"] == "awaiting_approval"
    assert not env["calls"]


async def test_live_disable_between_segments_stops_remaining_posts(env, monkeypatch):
    from kazma_core.x_api import publication_service

    detail = _stage()
    client = publication_service._client(env["cfg"])
    original = client.create_tweet

    async def disable_after_first(text, **kwargs):
        result = await original(text, **kwargs)
        env["cfg"] = replace(env["cfg"], enabled=False)
        return result

    monkeypatch.setattr(client, "create_tweet", disable_after_first)
    monkeypatch.setattr(publication_service, "_client", lambda cfg: client)
    result = await _run(detail)
    assert result["published_count"] == 1 and result["state"] == "partial"
    assert len(env["calls"]) == 1


async def test_changed_manifest_cannot_send_with_original_review(env):
    import json

    from kazma_core.x_api.publication_store import get_publication_store

    detail = _stage()
    with get_publication_store()._connection(transaction=True) as conn:
        original = conn.execute("SELECT manifest FROM x_threads WHERE id = ?", (detail["id"],)).fetchone()[0]
        changed = json.loads(original)
        changed.reverse()
        conn.execute("UPDATE x_threads SET manifest = ? WHERE id = ?", (json.dumps(changed), detail["id"]))
    result = await _run(detail)
    assert result["state"] == "partial" and not env["calls"]


def test_thread_pages_reach_old_reviews_without_cross_tenant_rows(env):
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.thread_store import thread_page
    from kazma_core.x_api.threads import prepare_thread

    expected = set()
    for index in range(3):
        expected.add(prepare_thread([f"Start {index}", f"Finish {index}"], intent_key=f"page-{index}")["id"])
    with get_publication_store()._connection(transaction=True) as conn:
        conn.execute("UPDATE x_threads SET created_at = 100")
    first = thread_page(limit=2)
    second = thread_page(limit=2, cursor=first["next_cursor"])
    assert len(first["threads"]) == 2 and len(second["threads"]) == 1 and not second["next_cursor"]
    assert {row["id"] for row in first["threads"] + second["threads"]} == expected
    with tenant_scope("other"):
        assert thread_page()["threads"] == []
    with pytest.raises(ValueError, match="cursor"):
        thread_page(cursor="broken")
