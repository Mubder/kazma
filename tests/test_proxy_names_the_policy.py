"""The proxy card and its Test name the policy that decides (FA-115).

The 2026-11 functional audit: the hint said scraping routes through the proxy
when it is enabled, but the acquisition policy decides (AGENTS.md section 13):
the proxy is used only with ``KAZMA_ACQUISITION_MODE=local_first_with_remote_
fallback`` and ``proxy`` in ``KAZMA_ACQUISITION_REMOTE_PROVIDERS``. The Test
answered "Proxy testing is disabled by acquisition policy." and named nothing.

With the policy at ``local_only``, the Test names both variables, and the
card's GET carries the live policy. The negative control is the old answer.
"""

from __future__ import annotations

import asyncio

import pytest

VARS = ("KAZMA_ACQUISITION_MODE", "KAZMA_ACQUISITION_REMOTE_PROVIDERS")


@pytest.fixture()
def manager(monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.settings_manager import SettingsManager

    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    return SettingsManager(get_config_store())


def test_the_test_names_both_variables(manager):
    answer = asyncio.run(manager.test_proxy())
    assert answer["success"] is False and answer["error"] == "proxy_not_allowed"
    assert all(v in answer["message"] for v in VARS), answer
    assert answer["policy"] == {"mode": "local_only", "providers": [], "proxy_allowed": False}


def test_the_card_shows_the_live_policy(manager, monkeypatch):
    assert manager.get_proxy_settings()["policy"]["proxy_allowed"] is False
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "proxy")
    assert manager.get_proxy_settings()["policy"]["proxy_allowed"] is True


def test_an_invalid_policy_is_said(manager, monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "everything")
    answer = asyncio.run(manager.test_proxy())
    assert answer["error"] == "policy_invalid" and "KAZMA_ACQUISITION_MODE" in answer["message"]
    assert manager.get_proxy_settings()["policy"]["error"] == "policy_invalid"


def test_the_hint_and_the_card_name_the_variables():
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS as CATALOG

    for key in ("settings.proxy_hint", "settings.proxy_policy_off"):
        for lang in ("en", "ar"):
            assert all(v in CATALOG[key][lang] for v in VARS), (key, lang)


def test_the_old_answer_named_nothing():
    """Negative control: the Test's answer before FA-115."""
    old = "Proxy testing is disabled by acquisition policy."
    assert not any(v in old for v in VARS)
