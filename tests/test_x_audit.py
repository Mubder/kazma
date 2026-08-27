"""X integration audit log — every output, full content, timestamped.

Operator decision 2026-08-27: all X activity leaves an append-only audit
trail in kazma-data/x_audit.db. These tests pin the store roundtrip, the
XClient choke-point coverage (success / reply / delete / HTTP error /
network error) and the never-raises guarantee.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import pytest

from kazma_core.x_api import reset_x_audit, query_x_audit
from kazma_core.x_api.audit import log_x_event
from kazma_core.x_api.client import XApiError, XClient
from kazma_core.x_api.config import XCredentials


@pytest.fixture()
def audit_db(tmp_path: Path) -> Path:
    db = tmp_path / "x_audit.db"
    reset_x_audit(db)
    return db


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"x" if payload is not None else b""
        self.headers = {}

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient inside XClient._request."""

    resp: _FakeResp | None = None
    exc: Exception | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def request(self, method: str, url: str, headers: dict | None = None, json: dict | None = None):
        if _FakeAsyncClient.exc is not None:
            raise _FakeAsyncClient.exc
        assert _FakeAsyncClient.resp is not None
        return _FakeAsyncClient.resp


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("kazma_core.x_api.client.httpx.AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.resp = None
    _FakeAsyncClient.exc = None
    yield _FakeAsyncClient
    _FakeAsyncClient.resp = None
    _FakeAsyncClient.exc = None


def _client() -> XClient:
    return XClient(
        XCredentials(
            api_key="k", api_key_secret="ks", access_token="t", access_token_secret="ts"
        )
    )


# ── Store ─────────────────────────────────────────────────────────────


def test_audit_roundtrip_full_content_and_timestamp(audit_db: Path) -> None:
    log_x_event(
        action="post",
        method="POST",
        endpoint="/2/tweets",
        status="success",
        http_status=201,
        tweet_id="1770000000000000000",
        request_body={"text": "مرحبا بالعالم — full content, no truncation"},
        response_body={"data": {"id": "1770000000000000000", "text": "مرحبا بالعالم — full content, no truncation"}},
        duration_ms=431,
    )
    rows = query_x_audit(action="post")
    assert len(rows) == 1
    row = rows[0]
    # Timestamp is a human-readable LOCAL date-and-time with tz offset.
    ts = datetime.fromisoformat(row["ts"])
    assert ts.tzinfo is not None
    assert row["status"] == "success"
    assert row["http_status"] == 201
    assert row["tweet_id"] == "1770000000000000000"
    assert "no truncation" in row["request_body"]
    assert "1770000000000000000" in row["response_body"]
    assert row["duration_ms"] == 431


def test_audit_query_filters_and_never_raises(audit_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_x_event(action="post", status="success")
    log_x_event(action="delete", status="error", http_status=429)
    assert [r["action"] for r in query_x_audit(action="delete")] == ["delete"]
    assert {r["status"] for r in query_x_audit()} == {"success", "error"}

    def _boom() -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("kazma_core.x_api.audit.get_x_audit", _boom)
    log_x_event(action="post")  # must swallow the failure — never raises


# ── Client choke point ────────────────────────────────────────────────


async def test_create_tweet_audited_success(audit_db: Path, fake_http) -> None:
    _FakeAsyncClient.resp = _FakeResp(
        201, {"data": {"id": "1770000000000000001", "text": "hello audit"}}
    )
    tweet = await _client().create_tweet("hello audit")
    assert tweet["id"] == "1770000000000000001"
    rows = query_x_audit(action="post")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "success"
    assert row["http_status"] == 201
    assert row["tweet_id"] == "1770000000000000001"
    assert "hello audit" in row["request_body"]
    assert "1770000000000000001" in row["response_body"]


async def test_reply_and_delete_labels(audit_db: Path, fake_http) -> None:
    _FakeAsyncClient.resp = _FakeResp(201, {"data": {"id": "2"}})
    await _client().create_tweet("a reply", reply_to_id="1")
    assert query_x_audit(action="reply"), "reply_to_id must label the action 'reply'"

    _FakeAsyncClient.resp = _FakeResp(200, {"data": {"deleted": True}})
    await _client().delete_tweet("1770000000000000002")
    rows = query_x_audit(action="delete")
    assert rows and rows[0]["tweet_id"] == "1770000000000000002"


async def test_http_error_audited(audit_db: Path, fake_http) -> None:
    _FakeAsyncClient.resp = _FakeResp(429, {"title": "Too Many Requests"}, text='{"title": "Too Many Requests"}')
    with pytest.raises(XApiError) as ei:
        await _client().create_tweet("will be rate limited")
    assert ei.value.transient
    rows = query_x_audit(status="error")
    assert rows and rows[0]["http_status"] == 429
    assert "Too Many Requests" in rows[0]["response_body"]


async def test_network_error_audited(audit_db: Path, fake_http) -> None:
    _FakeAsyncClient.exc = httpx.ConnectError("boom")
    with pytest.raises(XApiError):
        await _client().create_tweet("network down")
    rows = query_x_audit(status="network_error")
    assert rows and rows[0]["action"] == "post"
    assert rows[0]["http_status"] is None


# ── Settings viewer endpoint ──────────────────────────────────────────


async def test_audit_endpoint_serves_entries(monkeypatch):
    """GET /api/x/audit backs the Settings → X connector audit table."""
    import kazma_core.x_api.audit as audit_mod
    from kazma_ui.x_api import x_audit

    rows = [
        {"ts": "2026-08-27T20:01:02+03:00", "action": "post", "status": "success",
         "http_status": 201, "tweet_id": "1770", "duration_ms": 900,
         "request_body": None, "response_body": None},
        {"ts": "2026-08-27T20:05:00+03:00", "action": "delete", "status": "error",
         "http_status": 429, "tweet_id": None, "duration_ms": 300,
         "request_body": None, "response_body": None},
    ]
    monkeypatch.setattr(audit_mod, "query_x_audit", lambda **k: rows)
    out = await x_audit(limit=10)
    import json as _json
    payload = _json.loads(out.body)
    assert payload["ok"] is True and payload["count"] == 2
    assert payload["entries"][0]["action"] == "post"


async def test_audit_endpoint_bounds_limit(monkeypatch):
    import kazma_core.x_api.audit as audit_mod
    from kazma_ui.x_api import x_audit

    seen = {}

    def fake(**k):
        seen["limit"] = k.get("limit")
        return []

    monkeypatch.setattr(audit_mod, "query_x_audit", fake)
    import json as _json
    payload = _json.loads((await x_audit(limit=9999)).body)
    assert payload["ok"] is True and seen["limit"] == 500
