"""One transactional authority owns X send attempts and quota commitments."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def store(tmp_path):
    from kazma_core.x_api.publication_store import _PublicationStore

    return _PublicationStore(tmp_path / "x_publications.db")


def reserve(store, key="one", text="One post", due=None, **over):
    args = dict(tenant_id="default", account_id="account", credential_revision="rev",
                idempotency_key=key, text=text, reply_to_id="", due_at=due or time.time(),
                max_day=2, max_month=10, duplicate_days=30, origin="test", origin_ref="")
    args.update(over)
    return store.reserve(**args)


def test_key_binds_exact_payload_and_account(store):
    from kazma_core.x_api.publication_store import PublicationConflictError

    first = reserve(store)
    assert reserve(store)["id"] == first["id"]
    with pytest.raises(PublicationConflictError):
        reserve(store, text="Changed post")
    assert reserve(store, account_id="other")["id"] != first["id"]


def test_only_one_store_can_claim_and_cancel_loses_after_claim(store):
    from kazma_core.x_api.publication_store import _PublicationStore

    row = reserve(store)
    other = _PublicationStore(store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda args: args[0].claim(row["id"], owner=args[1],
            tenant_id="default", account_id="account", credential_revision="rev"), ((store, "a"), (other, "b"))))
    assert sum(claim is not None for claim in claims) == 1
    assert not store.cancel(row["id"], tenant_id="default", expected_version=row["version"])


def test_reservations_are_atomic_across_instances(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError, _PublicationStore

    other = _PublicationStore(store.path)
    def book(pair):
        try:
            return reserve(pair[0], key=pair[1], text=pair[1], max_day=1)
        except PublicationPolicyError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(book, ((store, "first"), (other, "second"))))
    assert sum(row is not None for row in rows) == 1


def test_future_windows_are_not_all_counted_as_today(store):
    now = time.time()
    reserve(store, key="today", text="Today", due=now, max_day=1)
    reserve(store, key="tomorrow", text="Tomorrow", due=now + 86401, max_day=1)
    from kazma_core.x_api.publication_store import PublicationPolicyError

    with pytest.raises(PublicationPolicyError):
        reserve(store, key="overlap", text="Overlapping", due=now + 86399, max_day=1)


def test_claim_checks_account_and_credentials(store):
    row = reserve(store)
    assert store.claim(row["id"], owner="a", tenant_id="other", account_id="account", credential_revision="rev") is None
    assert store.claim(row["id"], owner="a", tenant_id="default", account_id="other", credential_revision="rev") is None
    assert store.claim(row["id"], owner="a", tenant_id="default", account_id="account", credential_revision="changed") is None


def test_concurrent_replies_reserve_target_limit_atomically(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError, _PublicationStore

    other = _PublicationStore(store.path)
    def book(pair):
        try:
            return reserve(pair[0], key=pair[1], text=pair[1], max_day=10,
                           metadata={"summon_id": pair[1], "reply_target": "target", "reply_conversation": pair[1]},
                           reply_limits={"daily": 5, "target": 1, "cooldown": 0})
        except PublicationPolicyError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(book, ((store, "first"), (other, "second"))))
    assert sum(row is not None for row in rows) == 1


def test_unknown_reply_keeps_conversation_cooldown(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError

    args = dict(metadata={"summon_id": "one", "reply_target": "target", "reply_conversation": "thread"},
                reply_limits={"daily": 5, "target": 5, "cooldown": 3600})
    first = reserve(store, **args)
    store.claim(first["id"], owner="a", tenant_id="default", account_id="account", credential_revision="rev")
    store.fail(first["id"], owner="a", outcome="unknown", reason="lost")
    with pytest.raises(PublicationPolicyError, match="cooldown"):
        reserve(store, key="second", text="Another reply", **args)


def test_operator_target_exception_still_obeys_daily_reply_cap(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError

    args = dict(metadata={"summon_id": "one", "reply_target": "target", "operator_summon": True},
                reply_limits={"daily": 1, "target": 1, "cooldown": 3600})
    reserve(store, **args)
    with pytest.raises(PublicationPolicyError, match="Daily reply"):
        reserve(store, key="second", text="Second", **args)


def test_unknown_never_reclaims_and_keeps_quota(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError

    row = reserve(store, max_day=1)
    store.claim(row["id"], owner="a", tenant_id="default", account_id="account", credential_revision="rev")
    store.fail(row["id"], owner="a", outcome="unknown", reason="response lost")
    assert store.get(row["id"], tenant_id="default")["state"] == "outcome_unknown"
    assert store.claim(row["id"], owner="b", tenant_id="default", account_id="account", credential_revision="rev") is None
    with pytest.raises(PublicationPolicyError):
        reserve(store, key="another", text="Another", max_day=1)


def test_expired_send_recovers_to_unknown_not_pending(store):
    row = reserve(store)
    store.claim(row["id"], owner="a", tenant_id="default", account_id="account", credential_revision="rev", lease_seconds=30)
    assert store.recover(now=time.time() + 31) == 1
    assert store.get(row["id"], tenant_id="default")["state"] == "outcome_unknown"


def test_confirmation_and_outbox_commit_together(store):
    row = reserve(store)
    store.claim(row["id"], owner="a", tenant_id="default", account_id="account", credential_revision="rev")
    assert store.confirm(row["id"], owner="a", tweet_id="123")
    assert store.get(row["id"], tenant_id="default")["state"] == "published"
    entries = store.outbox(tenant_id="default")
    assert len(entries) == 1 and entries[0]["operation_id"] == row["id"]
    assert store.confirm(row["id"], owner="a", tweet_id="123") is False
    assert len(store.outbox(tenant_id="default")) == 1
    assert store.finish_projection(entries[0]["id"], tenant_id="default")
    assert store.outbox(tenant_id="default") == []


def test_rejected_send_releases_reservation_but_retains_events(store):
    row = reserve(store, max_day=1)
    store.claim(row["id"], owner="a", tenant_id="default", account_id="account", credential_revision="rev")
    store.fail(row["id"], owner="a", outcome="rejected", reason="HTTP 403")
    reserve(store, key="another", text="Another", max_day=1)
    assert len(store.events(row["id"], tenant_id="default")) >= 3


def test_reschedule_rechecks_windows_and_revision(store):
    from kazma_core.x_api.publication_store import PublicationPolicyError

    now = time.time()
    first = reserve(store, key="first", text="First", due=now + 100, max_day=1)
    second = reserve(store, key="second", text="Second", due=now + 86500, max_day=1)
    with pytest.raises(PublicationPolicyError):
        store.reschedule(second["id"], tenant_id="default", expected_version=second["version"], due_at=now + 101, max_day=1, max_month=10)
    assert store.get(second["id"], tenant_id="default")["due_at"] == second["due_at"]
    assert not store.reschedule(first["id"], tenant_id="other", expected_version=first["version"], due_at=now + 101, max_day=1, max_month=10)
