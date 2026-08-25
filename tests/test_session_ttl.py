"""SessionStore 5-minute TTL is not a durable job lookup."""

from __future__ import annotations

from kazma_core.sessions.ttl import (
    SESSION_TTL_SECONDS,
    refuse_session_lookup_for_durable_job,
    session_store_not_for_long_jobs,
)


def test_ttl_is_five_minutes() -> None:
    assert SESSION_TTL_SECONDS == 300


def test_honesty_message() -> None:
    msg = session_store_not_for_long_jobs("cron fire")
    assert "5 minutes" in msg
    assert "delivery_target" in msg
    assert "cron fire" in msg


def test_refuse_returns_none() -> None:
    assert refuse_session_lookup_for_durable_job(job_kind="reminder", thread_id="t1") is None
