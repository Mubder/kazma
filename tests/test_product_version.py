"""kazma_core.version — fixed base + live +gSHORTSHA."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from kazma_core.version import (
    clear_version_cache,
    get_base_version,
    get_git_short_sha,
    get_public_version,
    get_version,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_version_cache()
    yield
    clear_version_cache()


def test_base_matches_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m
    assert get_base_version() == m.group(1).split("+", 1)[0]
    assert get_public_version() == get_base_version()


def test_version_with_env_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_GIT_SHA", "deadbeef1234567")
    clear_version_cache()
    full = get_version()
    base = get_base_version()
    assert full == f"{base}+gdeadbee"
    assert get_git_short_sha() == "deadbee"


def test_version_strips_leading_g_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_GIT_SHA", "gabc1234")
    clear_version_cache()
    assert get_git_short_sha() == "abc1234"
    assert get_version().endswith("+gabc1234")


def test_github_sha_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_GIT_SHA", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "11223344556677889900aabb")
    clear_version_cache()
    assert get_git_short_sha() == "1122334"
    assert get_version() == f"{get_base_version()}+g1122334"


def test_live_git_or_base_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_GIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    clear_version_cache()
    full = get_version()
    base = get_base_version()
    assert full == base or re.fullmatch(
        rf"{re.escape(base)}\+g[0-9a-f]+", full
    ), full
