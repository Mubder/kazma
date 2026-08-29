"""A weekly break is only a problem if it is a surprise.

The Google grant expired on 2026-08-27 and nothing said so: Gmail was
unusable and 29 consecutive backups went local-only, both found by someone
going looking. Staying on OAuth "Testing" status is a deliberate choice
here -- passing Google's verification would mean owning and hosting a
public home page for a single-user personal agent -- and the price is that
Google expires the grant every 7 days, predictably.

Predictable is workable. Silent is not.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from kazma_core.observability import connector_health as ch


@pytest.fixture
def vault(monkeypatch):
    store: dict[str, str] = {"email.gmail.refresh_token": "a-token"}
    monkeypatch.setattr(ch, "_vault_get", lambda k: store.get(k, ""))
    return store


@pytest.fixture
def alerts(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ch, "_alert",
                        lambda key, title, detail, severity: sent.append(
                            {"key": key, "title": title,
                             "detail": detail, "severity": severity}))
    return sent


def _probe(monkeypatch, ok, detail=""):
    async def _p():
        return ok, detail

    monkeypatch.setattr(ch, "_probe_google", _p)


# ── the silent failure this exists to end ─────────────────────────────


def test_an_expired_grant_is_reported_as_critical(vault, alerts, monkeypatch):
    _probe(monkeypatch, False, "invalid_grant: Token has been expired or revoked")
    st = asyncio.run(ch.check_google())

    assert st.ok is False
    a = next(a for a in alerts if a["key"] == "connector.google_expired")
    assert a["severity"] == "critical"
    assert "Disconnect" in a["detail"], "say exactly how to fix it"


def test_the_expiry_alert_says_backups_are_unaffected(vault, alerts, monkeypatch):
    """The offsite copy runs through rclone on its own credential. Letting
    the operator believe their backups died with Gmail would cause a panic
    that is not warranted."""
    _probe(monkeypatch, False, "invalid_grant")
    asyncio.run(ch.check_google())
    a = next(a for a in alerts if a["key"] == "connector.google_expired")
    assert "rclone" in a["detail"] and "unaffected" in a["detail"]


# ── warning BEFORE the cliff ──────────────────────────────────────────


def test_a_grant_near_the_cliff_warns_a_day_early(vault, alerts, monkeypatch):
    """Six days old, still working. This is the whole point: tell the
    operator while they can still act calmly."""
    vault["email.gmail.connected_at"] = str(int(time.time() - 6.2 * 86400))
    _probe(monkeypatch, True, "connected")

    st = asyncio.run(ch.check_google())
    assert st.ok is True, "it still works -- this is a warning, not a failure"
    a = next(a for a in alerts if a["key"] == "connector.google_expiring")
    assert a["severity"] == "warn"
    assert "Disconnect" in a["detail"]


def test_a_fresh_grant_is_silent(vault, alerts, monkeypatch):
    """Nagging from day one is how a channel gets muted."""
    vault["email.gmail.connected_at"] = str(int(time.time() - 1 * 86400))
    _probe(monkeypatch, True)
    st = asyncio.run(ch.check_google())
    assert st.ok is True
    assert alerts == []


def test_an_unknown_grant_age_does_not_fake_freshness(vault, alerts, monkeypatch):
    """Grants minted before the timestamp existed have no age. Treating
    unknown as 'just connected' would suppress the warning an old grant
    most needs."""
    assert ch.google_grant_age_days() is None
    _probe(monkeypatch, True)
    st = asyncio.run(ch.check_google())
    assert st.age_days is None
    assert st.expires_in_days is None
    assert alerts == [], "no age means no expiry claim, in either direction"


@pytest.mark.parametrize("age_days,expected", [(0.0, 7.0), (3.0, 4.0), (9.0, 0.0)])
def test_remaining_days_never_goes_negative(vault, monkeypatch, age_days, expected):
    vault["email.gmail.connected_at"] = str(int(time.time() - age_days * 86400))
    _probe(monkeypatch, True)
    st = asyncio.run(ch.check_google(alert_on_findings=False))
    assert st.expires_in_days == pytest.approx(expected, abs=0.05)


# ── the live probe, not the clock ─────────────────────────────────────


def test_a_revoked_grant_is_caught_even_when_young(vault, alerts, monkeypatch):
    """A grant can be revoked from the account's third-party access page
    long before 7 days. A clock-only check would call that healthy right up
    until someone tried to use it."""
    vault["email.gmail.connected_at"] = str(int(time.time() - 0.5 * 86400))
    _probe(monkeypatch, False, "invalid_grant")

    st = asyncio.run(ch.check_google())
    assert st.ok is False
    assert any(a["key"] == "connector.google_expired" for a in alerts)


def test_no_connected_account_is_skipped_not_failed(monkeypatch, alerts):
    monkeypatch.setattr(ch, "_vault_get", lambda k: "")
    st = asyncio.run(ch.check_google())
    assert st.ok is True and st.skipped
    assert alerts == [], "an install with no Google account must not be nagged"


def test_a_raising_probe_never_escapes(vault, monkeypatch):
    async def _boom():
        raise RuntimeError("network on fire")

    monkeypatch.setattr(ch, "_probe_google", _boom)
    st = asyncio.run(ch.check_google(alert_on_findings=False))
    assert st.ok is False  # must not raise


# ── pacing and wiring ─────────────────────────────────────────────────


def test_the_reminder_interval_suits_a_standing_condition():
    """It stays broken until a human re-consents. A short cooldown would
    fire every run and be tuned out before the operator got home."""
    assert ch._COOLDOWN_S >= 6 * 3600


def test_the_ttl_matches_googles_testing_mode_rule():
    assert ch.TESTING_MODE_TTL_DAYS == 7.0


def test_the_check_is_scheduled_nightly():
    import inspect

    from kazma_core.memory import worker_bootstrap

    src = inspect.getsource(worker_bootstrap)
    assert 'enqueue_task("connector_health", {})' in src
    assert 'register_handler("connector_health"' in src


def test_the_connect_time_is_recorded_on_reconnect():
    """Without it there is no age, and no early warning."""
    import inspect

    from kazma_skills.native.email_manager import oauth_gmail

    src = inspect.getsource(oauth_gmail.persist_gmail_tokens)
    assert "email.gmail.connected_at" in src
