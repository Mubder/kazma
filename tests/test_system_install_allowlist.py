"""Allowlist and API contract for system package/extra installer."""

from __future__ import annotations

from kazma_core.system.installer import ALLOWED_EXTRAS, ALLOWED_PACKAGES


def test_allowed_extras_match_pyproject_optional_set():
    # Must include all documented optional extras from pyproject (except 'all' meta).
    expected = {"rag", "dev", "test", "tui", "observability", "web", "all"}
    assert ALLOWED_EXTRAS == expected


def test_rag_packages_allowlisted():
    assert "chromadb" in ALLOWED_PACKAGES
    assert "sentence-transformers" in ALLOWED_PACKAGES


def test_no_arbitrary_packages():
    assert "requests" not in ALLOWED_PACKAGES
    assert "evil-pkg" not in ALLOWED_PACKAGES
