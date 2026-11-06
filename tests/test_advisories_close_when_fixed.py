"""An advisory leaves Open problems when the installed version is past its fix.

The daily OSV check stores what it found. On the drill VM (2026-11-07)
`kazma update` installed certifi, oauthlib and pypdf past their fixes, and
the Dashboard kept all five advisories, each saying "It closes when the
installed version is past the fix": the stored list was read as it was until
the next day's check. Each stored advisory is now held against the version
installed now. The negative control is the stored list itself.
"""

from __future__ import annotations

from importlib import metadata

import pytest

from kazma_core.security import advisory_watch as aw

_PYTEST = metadata.version("pytest")


@pytest.fixture
def stored(monkeypatch):
    items = [
        {"package": "pytest", "version": "0.1", "id": "GHSA-fixed-by-update", "fixed": "1.0"},
        {"package": "pytest", "version": _PYTEST, "id": "GHSA-still-open", "fixed": "999.0"},
        {"package": "pytest", "version": _PYTEST, "id": "GHSA-no-fix-yet", "fixed": None},
        {"package": "kazma-package-not-installed", "version": "1.0", "id": "GHSA-gone", "fixed": "2.0"},
    ]
    monkeypatch.setattr(aw, "_read_state", lambda: {"open": items})
    return items


def test_only_what_the_installed_versions_still_have_is_open(stored):
    ids = [a["id"] for a in aw.open_advisories()]
    assert ids == ["GHSA-still-open", "GHSA-no-fix-yet"]
    # Negative control: the stored list, as it was read before.
    assert len(stored) == 4


def test_open_problems_shows_the_same(stored):
    from kazma_core.observability import open_problems

    titles = [p.title for p in open_problems._advisories("en")]
    assert any("GHSA-still-open" in t for t in titles), titles
    assert not any("GHSA-fixed-by-update" in t or "GHSA-gone" in t for t in titles), titles


def test_a_version_that_cannot_be_compared_stays_open():
    assert aw._still_open({"package": "pytest", "fixed": "not a version!"}) is True
