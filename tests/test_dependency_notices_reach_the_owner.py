"""A dependency problem reaches the owner, and stays in front of them until fixed.

The functional audit of 2026-11 (roadmap themes 11 and 12, after FA-258):
the boot check spoke only at a start, only once a commit had raised a floor,
and its 12-hour cooldown let it drop out of sight; an advisory published
against an installed package said nothing at all. The owner, using Kazma
daily, never saw a notice.

- Theme 11: :func:`advisory_watch.advisory_sweep`, a daily maintenance sweep,
  asks OSV about every installed version and tells each new advisory once,
  in one alert per check, with what fixes it. Reviewed advisories stay
  quiet; OSV not answering raises nothing and is asked again in an hour.
- Theme 12: the boot check keeps its result, and every daily digest names
  each unmet requirement and open advisory, with the command, until a later
  check clears it.

Recorded OSV answers stand in for the network. The boot check alone (an
advisory no floor covers) and the digest without its package lines are the
negative controls.
"""

from __future__ import annotations

import pytest

from kazma_core import install_requirements
from kazma_core.observability import daily_digest
from kazma_core.security import advisory_watch
from kazma_core.security.dependency_scanner import (
    REVIEWED_ADVISORIES,
    DependencyQueryError,
    DependencyReport,
    Vulnerability,
)

DAY = 24 * 3600.0
T0 = 1_800_000_000.0


def _report(*vulns: Vulnerability) -> DependencyReport:
    return DependencyReport(total=100, vulnerabilities=list(vulns))


PYJWT = Vulnerability("pyjwt", "2.10.0", "GHSA-test-jwt1-0001", "token forgery", "2.12.0")
LXML = Vulnerability("lxml", "5.1.0", "GHSA-test-lxml-0002", "entity expansion", "5.3.1")
NO_FIX = Vulnerability("somepkg", "1.0.0", "GHSA-test-none-0003", "no fix yet", None)


@pytest.fixture
def alerts(monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.observability import ops_alerts

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, title, detail="", **_kw: sent.append((key, detail)) or True)
    monkeypatch.setattr(install_requirements, "unmet_requirements", lambda *_a, **_k: [])
    # These advisories are made up (pkg0..., fixed versions this environment
    # is past): every stored one counts as still installed. Closing one once
    # the installed version is past its fix is tests/test_advisories_close_when_fixed.py.
    monkeypatch.setattr(advisory_watch, "_still_open", lambda item: True)
    store = get_config_store()
    for key in (advisory_watch.ADVISORIES_KEY, install_requirements.REQUIREMENTS_KEY):
        store.delete(key)
    yield sent
    for key in (advisory_watch.ADVISORIES_KEY, install_requirements.REQUIREMENTS_KEY):
        store.delete(key)


# ── theme 11: the daily advisory check ───────────────────────────────────


def test_a_new_advisory_is_told_once_with_its_fix(alerts):
    assert advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(PYJWT)) == "told"
    assert len(alerts) == 1
    key, detail = alerts[0]
    assert key == "security.advisories_new"
    assert "pyjwt 2.10.0" in detail and "GHSA-test-jwt1-0001" in detail and "2.12.0" in detail
    # The next day, the same advisory: quiet.
    assert advisory_watch.advisory_sweep(now=T0 + DAY + 1, audit=lambda: _report(PYJWT)) == "quiet"
    assert len(alerts) == 1


def test_one_alert_per_check_names_only_what_is_new(alerts):
    advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(PYJWT))
    assert advisory_watch.advisory_sweep(now=T0 + DAY + 1, audit=lambda: _report(PYJWT, LXML, NO_FIX)) == "told"
    assert len(alerts) == 2
    detail = alerts[1][1]
    assert "lxml 5.1.0" in detail and "somepkg 1.0.0" in detail and "pyjwt" not in detail
    assert "no fixed release yet" in detail


def test_the_check_runs_once_a_day(alerts):
    advisory_watch.advisory_sweep(now=T0, audit=lambda: _report())
    called = []
    assert advisory_watch.advisory_sweep(now=T0 + 3600, audit=lambda: called.append(1)) == "not_due"
    assert called == []


def test_osv_down_raises_nothing_and_is_asked_again_in_an_hour(alerts):
    def down():
        raise DependencyQueryError("OSV could not be asked: timed out")

    assert advisory_watch.advisory_sweep(now=T0, audit=down) == "osv_unavailable"
    assert alerts == []
    assert advisory_watch.advisory_sweep(now=T0 + 600, audit=lambda: _report(PYJWT)) == "not_due"
    assert advisory_watch.advisory_sweep(now=T0 + 3601, audit=lambda: _report(PYJWT)) == "told"


def test_a_reviewed_advisory_stays_quiet(alerts):
    vuln_id, review = next(iter(REVIEWED_ADVISORIES.items()))
    reviewed = Vulnerability(review.package, "1.0.0", vuln_id, "reviewed", None)
    assert advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(reviewed)) == "quiet"
    assert alerts == [] and advisory_watch.open_advisories() == []


def test_the_fix_names_the_update_only_when_a_floor_covers_it(alerts, monkeypatch):
    covered = install_requirements.UnmetRequirement("pyjwt", ">=2.12.0", "2.10.0", ("base",))
    monkeypatch.setattr(install_requirements, "unmet_requirements", lambda *_a, **_k: [covered])
    advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(PYJWT, LXML))
    detail = alerts[0][1]
    pyjwt_part, lxml_part = detail.split("; ")[0], detail.split("; ")[1]
    assert "kazma_cli update" in pyjwt_part and "uv pip install" not in pyjwt_part
    assert 'uv pip install' in lxml_part and '"lxml>=5.3.1"' in lxml_part


def test_negative_control_the_boot_check_alone_says_nothing(alerts):
    """An advisory no floor covers yet: the boot check finds nothing unmet,
    alerts nothing -- the gap the daily check closes."""
    assert install_requirements.report_unmet_requirements() == []
    assert alerts == []


def test_the_check_is_a_maintenance_sweep():
    from kazma_core.memory import worker_bootstrap as wb

    assert ("dependency advisories", wb._dependency_advisories) in wb._MAINTENANCE_SWEEPS


# ── theme 12: the digest keeps them in front of the owner ────────────────


def _unmet(monkeypatch, *found: install_requirements.UnmetRequirement) -> None:
    monkeypatch.setattr(install_requirements, "unmet_requirements", lambda *_a, **_k: list(found))


def test_an_unmet_requirement_is_in_every_digest_until_cleared(alerts, monkeypatch):
    _unmet(monkeypatch, install_requirements.UnmetRequirement("fastapi", ">=0.162.0", "0.143.3", ("base",)))
    install_requirements.report_unmet_requirements()
    for _ in range(2):  # every digest, not only the first
        text = daily_digest.build_digest(24)
        attention = text.split("Needs attention:", 1)[1]
        assert "fastapi" in attention and "kazma_cli update" in attention
    _unmet(monkeypatch)  # the next boot finds none
    install_requirements.report_unmet_requirements()
    assert "fastapi" not in daily_digest.build_digest(24)


def test_open_advisories_are_in_the_digest(alerts):
    advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(PYJWT, LXML))
    attention = daily_digest.build_digest(24).split("Needs attention:", 1)[1]
    assert "2 dependency advisories still open" in attention
    assert "GHSA-test-jwt1-0001" in attention and "GHSA-test-lxml-0002" in attention
    # Fixed (the next check finds none): gone from the digest.
    advisory_watch.advisory_sweep(now=T0 + DAY + 1, audit=lambda: _report())
    assert "GHSA-test" not in daily_digest.build_digest(24)


def test_many_advisories_are_counted_past_ten(alerts):
    many = [Vulnerability(f"pkg{i}", "1.0", f"GHSA-test-many-{i:04d}", "", "2.0") for i in range(13)]
    advisory_watch.advisory_sweep(now=T0, audit=lambda: _report(*many))
    assert "and 3 more" in daily_digest.build_digest(24)


def test_negative_control_the_old_digest_named_none(alerts, monkeypatch):
    _unmet(monkeypatch, install_requirements.UnmetRequirement("fastapi", ">=0.162.0", "0.143.3", ("base",)))
    install_requirements.report_unmet_requirements()
    # The old digest had neither line: no package lines, and no open-problems
    # line (resilience plan G2), which lists the same unmet requirement.
    monkeypatch.setattr(daily_digest, "_package_lines", lambda: [])
    monkeypatch.setattr(daily_digest, "_open_problems_line", lambda: None)
    assert "fastapi" not in daily_digest.build_digest(24)


def test_an_unreadable_check_is_said_never_taken_for_none(alerts, monkeypatch):
    import sqlite3

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(install_requirements, "last_requirements_check", locked)
    monkeypatch.setattr(advisory_watch, "open_advisories", locked)
    attention = daily_digest.build_digest(24).split("Needs attention:", 1)[1]
    assert "requirements check could not be read" in attention
    assert "advisory check could not be read" in attention
