"""The Dashboard's "Tokens Over Time" and "Cost Over Time" are the ledger's.

They drew the process's running totals, sampled by the open page every 10 s
and kept only in that page's memory (60 samples): bars that only grew, reset
by a server restart or a reload, and "Day" / "Week" never more than the tab
had seen (2026-10-22). They now draw ``/api/dashboard/usage``: the per-call
LLM ledger summed per bucket. Negative control: the running totals the page
used to sample do not give the spend per bucket.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from kazma_core.observability import llm_ledger
from starlette.requests import Request


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    yield conn
    llm_ledger.close_llm_ledger()
    monkeypatch.setattr(llm_ledger, "_conn", None)


def _call(conn, at: datetime, tokens: int, cost: float) -> None:
    conn.execute(
        "INSERT INTO llm_calls (ts, total_tokens, cost_usd) VALUES (?, ?, ?)",
        (at.isoformat(), tokens, cost),
    )
    conn.commit()


def test_each_bucket_holds_what_was_spent_in_it(ledger) -> None:
    since = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    _call(ledger, since + timedelta(minutes=1), 1000, 0.01)
    _call(ledger, since + timedelta(minutes=4, seconds=59), 500, 0.02)
    _call(ledger, since + timedelta(minutes=12), 70, 0.003)
    _call(ledger, since - timedelta(seconds=1), 9999, 9.0)       # before the range
    _call(ledger, since + timedelta(hours=1), 9999, 9.0)         # after it
    buckets = llm_ledger.usage_series(since, since + timedelta(hours=1), 300)
    assert len(buckets) == 12
    assert [b["tokens"] for b in buckets[:3]] == [1500, 0, 70]
    assert [b["calls"] for b in buckets[:3]] == [2, 0, 1]
    assert buckets[0]["cost"] == pytest.approx(0.03)
    assert sum(b["tokens"] for b in buckets) == 1570
    assert buckets[1]["start"] == (since + timedelta(minutes=5)).timestamp()


def test_negative_control_the_old_running_totals_are_not_spend_per_bucket(ledger) -> None:
    """The page sampled the process's cumulative total: every bar the sum of
    everything before it. Spend of 1500 then 70 drew 1500 then 1570."""
    since = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    _call(ledger, since + timedelta(minutes=1), 1500, 0.03)
    _call(ledger, since + timedelta(minutes=12), 70, 0.003)
    per_bucket = [b["tokens"] for b in llm_ledger.usage_series(since, since + timedelta(hours=1), 300)]
    running, total = [], 0
    for value in per_bucket:
        total += value
        running.append(total)
    assert running != per_bucket
    assert per_bucket[:3] == [1500, 0, 70] and running[:3] == [1500, 1500, 1570]


def test_an_unreadable_ledger_is_none_never_an_empty_chart(ledger, monkeypatch) -> None:
    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(llm_ledger, "_get_conn", broken)
    since = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    assert llm_ledger.usage_series(since, since + timedelta(hours=1), 300) is None


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


@pytest.mark.parametrize("range_,bars,width", [("hour", 12, 300), ("day", 24, 3600), ("week", 28, 21600)])
def test_the_route_gives_each_range_its_buckets(ledger, monkeypatch, range_, bars, width) -> None:
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import usage_over_time

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    _call(ledger, datetime.now(UTC) - timedelta(seconds=30), 1234, 0.05)
    body = json.loads(usage_over_time(_request("/api/dashboard/usage"), range=range_).body)
    assert body["available"] is True and body["range"] == range_
    assert body["bucket_seconds"] == width and len(body["buckets"]) == bars
    # The call made a moment ago is in the newest bucket, so the chart's
    # right edge is now.
    assert body["buckets"][-1]["tokens"] == 1234
    assert body["totals"] == {"tokens": 1234, "cost": pytest.approx(0.05), "calls": 1}


def test_an_unknown_range_is_the_hour(ledger, monkeypatch) -> None:
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import usage_over_time

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    body = json.loads(usage_over_time(_request("/api/dashboard/usage"), range="year").body)
    assert body["range"] == "hour" and len(body["buckets"]) == 12


def test_an_unreadable_ledger_says_so(ledger, monkeypatch) -> None:
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import usage_over_time

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "")
    monkeypatch.setattr(llm_ledger, "usage_series", lambda *a, **k: None)
    body = json.loads(usage_over_time(_request("/api/dashboard/usage")).body)
    assert body == {"available": False}


@pytest.mark.parametrize("role,status", [("viewer", 403), ("operator", 403), ("admin", 200)])
def test_usage_is_admin_only(ledger, monkeypatch, role, status) -> None:
    import kazma_ui.auth as auth
    from kazma_ui.dashboard import usage_over_time

    monkeypatch.setattr(auth, "get_kazma_secret", lambda: "configured")
    monkeypatch.setattr(auth, "is_authenticated", lambda request, secret: True)
    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"role": role, "source": "session"})
    assert usage_over_time(_request("/api/dashboard/usage")).status_code == status
