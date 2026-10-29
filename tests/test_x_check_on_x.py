"""Check on X settles an operation whose answer was lost (2026-10-29).

Live 2026-10-28: a metrics post timed out after X had published it, and its
delete timed out after X had deleted it. Both stayed ``outcome_unknown``:
only a recorded X answer settled an operation, a timeout leaves none, and the
unknown post kept its text from ever being posted again. X Studio's
"Check on X" (``publication_service.check_on_x``) asks X and settles it.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest

from kazma_core.x_api.publication_store import PublicationPolicyError, _PublicationStore

ACCOUNT = "2064330455885791232"
TEXT = "Kazma metrics · 2026-10-28 · tests & gates https://github.com/Mubder/kazma"
# A post id X made 3 s after the request below (see _post_id).
_SENT = 1793208281.0
_ASYNC_CLIENT = httpx.AsyncClient


def _post_id(epoch: float) -> str:
    return str((int(epoch * 1000) - 1288834974657) << 22)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from kazma_core.x_api import publication_service, publication_store

    s = _PublicationStore(tmp_path / "publications.db")
    publication_store._set_publication_store_for_tests(s)
    monkeypatch.setattr(publication_service, "_config",
                        lambda: SimpleNamespace(account_id=ACCOUNT, credentials=None))
    monkeypatch.setattr(publication_service, "_project_pending", lambda: {"repaired": 0, "failed": []})
    yield s
    publication_store._set_publication_store_for_tests(None)


def _unknown(store, *, kind="post", text=TEXT, target="", key="k1"):
    """An operation sent, whose answer never came back."""
    row = store.reserve(tenant_id="default", account_id=ACCOUNT, credential_revision="keys", idempotency_key=key,
                        text=text, reply_to_id=target, due_at=time.time(), max_day=50, max_month=500,
                        duplicate_days=30, origin="delete" if kind == "delete" else "immediate", kind=kind)
    store.claim(row["id"], owner="o", tenant_id="default", account_id=ACCOUNT, credential_revision="keys")
    assert store.fail(row["id"], owner="o", outcome="unknown", reason="X API timed out after the request was sent")
    with store._connection(transaction=True) as conn:
        conn.execute("UPDATE x_operations SET sent_at = ? WHERE id = ?", (_SENT, row["id"]))
    return store.get(row["id"], tenant_id="default")


class _X:
    """What X shows: posts on the timeline, and posts that exist."""

    def __init__(self, *, timeline=(), existing=(), error=None):
        self.timeline, self.existing, self.error = list(timeline), set(existing), error
        self.windows = []

    async def lookup_post(self, tweet_id):
        if self.error:
            raise self.error
        return {"id": tweet_id} if tweet_id in self.existing else None

    async def own_posts(self, user_id, *, start, end):
        if self.error:
            raise self.error
        self.windows.append((user_id, start, end))
        return self.timeline


def _x(monkeypatch, fake):
    from kazma_core.x_api import publication_service

    monkeypatch.setattr(publication_service, "_client", lambda cfg: fake)


@pytest.mark.asyncio
async def test_a_delete_x_no_longer_shows_is_done(store, monkeypatch):
    from kazma_core.x_api.publication_service import check_on_x

    target = _post_id(_SENT + 3)
    op = _unknown(store, kind="delete", text="", target=target)
    _x(monkeypatch, _X(existing=()))
    result = await check_on_x(op["id"], expected_version=op["version"])
    row = store.get(op["id"], tenant_id="default")
    assert result["ok"] and result["found"]
    assert (row["state"], row["outcome"], row["tweet_id"]) == ("published", "confirmed", target)
    assert "the post is gone" in row["reason"]


@pytest.mark.asyncio
async def test_a_delete_x_still_shows_can_be_sent_again(store, monkeypatch):
    from kazma_core.x_api import publication_service
    from kazma_core.x_api.publication_service import check_on_x

    target = _post_id(_SENT + 3)
    op = _unknown(store, kind="delete", text="", target=target, key="delete:" + target)
    _x(monkeypatch, _X(existing={target}))
    result = await check_on_x(op["id"])
    row = store.get(op["id"], tenant_id="default")
    assert result["ok"] and not result["found"]
    assert (row["state"], row["outcome"]) == ("failed_permanent", "not_sent")
    assert "Delete it again" in row["reason"]

    # The delete asked again is a NEW operation, not a replay of the ended one.
    sent = []

    async def dispatch(ident, *, cfg=None):
        sent.append(ident)
        return True, {"operation_id": ident}

    monkeypatch.setattr(publication_service, "_dispatch_operation", dispatch)
    monkeypatch.setattr(publication_service, "_backfill", lambda cfg: None)
    monkeypatch.setattr(publication_service, "_limits",
                        lambda cfg: {"max_day": 50, "max_month": 500, "duplicate_days": 30})
    monkeypatch.setattr(publication_service, "credential_revision", lambda creds: "keys")
    monkeypatch.setattr("kazma_core.x_api.policy.evaluate_delete",
                        lambda cfg=None: SimpleNamespace(allow=True, reason=""))
    ok, _ = await publication_service.delete(tweet_id=target)
    assert ok and sent and sent[0] != op["id"]
    assert store.get(sent[0], tenant_id="default")["state"] == "scheduled"


@pytest.mark.asyncio
async def test_a_post_x_shows_is_published_with_its_id(store, monkeypatch):
    from kazma_core.x_api.publication_service import check_on_x

    op = _unknown(store)
    made = _post_id(_SENT + 3)
    # X's own spelling: the link as t.co, "&" escaped.
    shown = {"id": made, "text": TEXT.replace("&", "&amp;").replace("https://github.com/Mubder/kazma", "https://t.co/abc"),
             "entities": {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://github.com/Mubder/kazma"}]}}
    other = {"id": _post_id(_SENT + 9), "text": "Something else"}
    fake = _X(timeline=[other, shown])
    _x(monkeypatch, fake)
    result = await check_on_x(op["id"])
    row = store.get(op["id"], tenant_id="default")
    assert result["ok"] and result["found"]
    assert (row["state"], row["tweet_id"]) == ("published", made)
    assert abs(row["published_at"] - (_SENT + 3)) < 1
    _, start, end = fake.windows[0]
    assert start < _SENT < end


@pytest.mark.asyncio
async def test_a_post_x_does_not_show_is_settled_and_its_text_is_free_again(store, monkeypatch):
    from kazma_core.x_api.publication_service import check_on_x

    op = _unknown(store)
    deleted = _unknown(store, kind="delete", text="", target=_post_id(_SENT + 3), key="d1")
    # Negative control: while unknown, the same text cannot be posted again.
    with pytest.raises(PublicationPolicyError, match="Duplicate"):
        store.reserve(tenant_id="default", account_id=ACCOUNT, credential_revision="keys", idempotency_key="again",
                      text=TEXT, reply_to_id="", due_at=time.time(), max_day=50, max_month=500,
                      duplicate_days=30, origin="immediate")
    _x(monkeypatch, _X(timeline=[{"id": _post_id(_SENT + 9), "text": "Something else"}]))
    result = await check_on_x(op["id"])
    row = store.get(op["id"], tenant_id="default")
    assert result["ok"] and not result["found"]
    assert (row["state"], row["outcome"]) == ("failed_permanent", "not_on_x")
    assert "never went out, or it was deleted since" in row["reason"]
    assert f"post {deleted['reply_to_id']}, made 3 s after this request" in row["reason"]
    store.reserve(tenant_id="default", account_id=ACCOUNT, credential_revision="keys", idempotency_key="again",
                  text=TEXT, reply_to_id="", due_at=time.time(), max_day=50, max_month=500,
                  duplicate_days=30, origin="immediate")


@pytest.mark.asyncio
async def test_anything_x_does_not_answer_plainly_changes_nothing(store, monkeypatch):
    from kazma_core.x_api.client import XApiError
    from kazma_core.x_api.publication_service import check_on_x

    op = _unknown(store)
    _x(monkeypatch, _X(error=XApiError("X API timed out (no answer in 20 s).", outcome="unknown")))
    result = await check_on_x(op["id"])
    assert result["code"] == "x_error"
    _x(monkeypatch, _X(timeline=[{"id": _post_id(_SENT + 3), "text": TEXT}, {"id": _post_id(_SENT + 5), "text": TEXT}]))
    assert (await check_on_x(op["id"]))["code"] == "ambiguous"
    assert (await check_on_x(op["id"], expected_version=op["version"] - 1))["code"] == "conflict"
    row = store.get(op["id"], tenant_id="default")
    assert (row["state"], row["version"]) == ("outcome_unknown", op["version"])


@pytest.mark.asyncio
async def test_a_reply_matches_its_parent_and_the_handles_x_adds(store, monkeypatch):
    from kazma_core.x_api.publication_service import check_on_x

    parent = _post_id(_SENT - 600)
    op = _unknown(store, text="Thanks, fixed now.", target=parent)
    elsewhere = {"id": _post_id(_SENT + 2), "text": "@someone Thanks, fixed now.",
                 "referenced_tweets": [{"type": "replied_to", "id": _post_id(_SENT - 50)}]}
    mine = {"id": _post_id(_SENT + 3), "text": "@someone Thanks, fixed now.",
            "referenced_tweets": [{"type": "replied_to", "id": parent}]}
    _x(monkeypatch, _X(timeline=[elsewhere, mine]))
    await check_on_x(op["id"])
    assert store.get(op["id"], tenant_id="default")["tweet_id"] == mine["id"]


# ── The client's two reads, against X's own answers ─────────────────────────

def _client_answering(monkeypatch, answer):
    from kazma_core.x_api import ai_budget
    from kazma_core.x_api.client import XClient
    from kazma_core.x_api.config import XCredentials

    original, seen = _ASYNC_CLIENT, []

    async def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=answer(request))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(ai_budget, "reserve_read", lambda *a, **k: None)
    return XClient(XCredentials("a", "b", "c", "d")), seen


@pytest.mark.asyncio
async def test_lookup_reads_xs_not_found_and_refuses_anything_else(monkeypatch):
    from kazma_core.x_api.client import XApiError

    gone = {"errors": [{"value": "123", "detail": "Could not find tweet with ids: [123].", "title": "Not Found Error",
                        "resource_type": "tweet", "parameter": "ids", "resource_id": "123",
                        "type": "https://api.twitter.com/2/problems/resource-not-found"}]}
    client, _ = _client_answering(monkeypatch, lambda r: gone)
    assert await client.lookup_post("123") is None
    client, _ = _client_answering(monkeypatch, lambda r: {"data": [{"id": "123", "author_id": ACCOUNT}]})
    assert (await client.lookup_post("123"))["id"] == "123"
    hidden = {"errors": [{"value": "123", "type": "https://api.twitter.com/2/problems/not-authorized-for-resource"}]}
    client, _ = _client_answering(monkeypatch, lambda r: hidden)
    with pytest.raises(XApiError):
        await client.lookup_post("123")


@pytest.mark.asyncio
async def test_own_posts_reads_every_page_of_the_window(monkeypatch):
    def answer(request):
        if "pagination_token" in str(request.url):
            return {"data": [{"id": "2", "text": "b"}], "meta": {}}
        return {"data": [{"id": "1", "text": "a"}], "meta": {"next_token": "t1"}}

    client, seen = _client_answering(monkeypatch, answer)
    posts = await client.own_posts(ACCOUNT, start=_SENT - 120, end=_SENT + 900)
    assert [p["id"] for p in posts] == ["1", "2"]
    assert f"/2/users/{ACCOUNT}/tweets?" in seen[0] and "start_time=" in seen[0] and "end_time=" in seen[0]
