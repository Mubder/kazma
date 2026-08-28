"""Backups that silently stopped protecting anything.

Live, 2026-08-28: 29 of 29 universal backups carried
``offsite.ok == False`` -- "Token has been expired or revoked" -- going
back more than a day. Every backup was local-only, so one disk failure
would have taken the data AND all 29 generations at once.

``_offsite_sync``'s own docstring calls itself "The ONLY protection
against disk death". Nothing told the operator it had stopped. The failure
was recorded faithfully in a JSON file nobody reads, which is precisely
the class of silent failure the alerting layer exists for -- the backup
path simply predated it.
"""

from __future__ import annotations

import pytest
from kazma_core.backup.universal import (
    _OFFSITE_ALERT_COOLDOWN_S,
    _alert_on_backup_gaps,
)


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def _alert(key, title, detail="", *, severity="info", cooldown_s=None, **kw):
        calls.append({"key": key, "title": title, "detail": detail,
                      "severity": severity, "cooldown_s": cooldown_s})
        return True

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", _alert)
    return calls


def test_a_failing_offsite_copy_is_reported(sent):
    """The live condition. 29 silent failures is 29 too many."""
    _alert_on_backup_gaps({"ok": False, "error": "invalid_grant: token revoked"}, 0)
    keys = [c["key"] for c in sent]
    assert "backup.offsite_failed" in keys


def test_the_operator_is_told_what_it_costs_them(sent):
    """"Offsite sync failed" is a status line. What matters is that one
    disk failure now loses everything, and what to do about it."""
    _alert_on_backup_gaps({"ok": False, "error": "invalid_grant"}, 0)
    c = next(c for c in sent if c["key"] == "backup.offsite_failed")
    assert "LOCAL ONLY" in c["title"]
    assert "every backup generation" in c["detail"]
    assert "Re-authorise" in c["detail"], "say what fixes it"
    assert c["severity"] == "critical"


def test_a_deliberately_disabled_offsite_is_not_an_alert(sent):
    """Operators who never configured cloud sync must not be nagged --
    that is how a channel gets muted, taking the real alerts with it."""
    _alert_on_backup_gaps({"skipped": "offsite sync disabled"}, 0)
    assert not sent


def test_a_working_offsite_copy_is_silent(sent):
    _alert_on_backup_gaps({"ok": True}, 0)
    assert not sent


def test_failed_databases_are_reported_separately(sent):
    """A local backup missing databases is a different, worse problem than
    a missing offsite copy, and must not be folded into it."""
    _alert_on_backup_gaps({"ok": True}, 3)
    c = next(c for c in sent if c["key"] == "backup.databases_failed")
    assert c["severity"] == "critical"
    assert "3" in c["title"]


def test_the_reminder_interval_suits_a_standing_condition():
    """Offsite stays broken until a human re-authorises, and backups run
    often. At the default cooldown this would alert on nearly every run and
    be tuned out within a day."""
    assert _OFFSITE_ALERT_COOLDOWN_S >= 3600


def test_alerting_can_never_fail_a_completed_backup(monkeypatch):
    """The backup already succeeded by this point. A broken notifier must
    not turn it into a failure."""
    def _boom(*a, **k):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", _boom)
    _alert_on_backup_gaps({"ok": False, "error": "x"}, 2)  # must not raise
