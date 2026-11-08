"""A backup model is one a provider can answer for (providers stage 1,
2026-11-08; kazma_core.failover_backup).

On the live install the failover chain named ``google/gemini-2.5-flash``
while the Google provider was off with no key; the monthly drill failed for
it, and OpenRouter and Groq -- with keys, working -- were never offered.
Nothing checked a chain entry. Now each entry says which provider would
serve it and whether it can be called, Settings offers usable backups of
other vendors, and saving a backup nothing can serve is refused.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core import failover_backup as fb

ROWS = [
    {"name": "deepseek", "base_url": "https://api.deepseek.com/v1", "key": "sk-ds", "models": ["deepseek-flash"]},
    {"name": "google", "base_url": "https://generativelanguage.googleapis.com/v1beta", "key": "", "enabled": False,
     "models": ["gemini-2.5-flash"]},
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "key": "sk-or",
     "models": ["google/gemini-2.5-flash"]},
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "key": "sk-gq", "models": ["llama-3.3-70b"]},
    {"name": "server-gpu", "base_url": "http://192.168.1.20:8000/v1", "key": "", "models": ["qwen2.5-32b"]},
]


class _Registry:
    def __init__(self, rows: list[dict[str, Any]] = ROWS, active: str = "deepseek") -> None:
        self.rows = [dict(r) for r in rows]
        self.active = active

    def get_active_profile(self):
        return {"provider": self.active, "model": "deepseek-flash"}

    def get_provider(self, name):
        return next((r for r in self.rows if r["name"] == name), None)

    def list_providers(self):
        return list(self.rows)

    def find_provider_for_model(self, model):
        return next((r for r in self.rows if r.get("enabled", True) and model in r["models"]), None)

    def resolve_provider_credentials(self, name):
        row = self.get_provider(name) or {}
        return row.get("base_url", ""), row.get("key", "")

    def get_visible_models(self, name):
        return list((self.get_provider(name) or {}).get("models", []))

    def probe_model_for(self, name):
        return ""


def test_each_backup_says_who_serves_it_and_whether_it_can_answer():
    rows = fb.backup_status(_Registry(), ["gemini-2.5-flash", "google/gemini-2.5-flash", "qwen2.5-32b",
                                          "deepseek-flash"])
    assert [(r["model"], r["provider"], r["usable"], r["same_vendor"]) for r in rows] == [
        ("gemini-2.5-flash", "", False, False),  # Google is off: nothing serves it
        ("google/gemini-2.5-flash", "openrouter", True, False),
        ("qwen2.5-32b", "server-gpu", True, False),  # on this network: no key needed
        ("deepseek-flash", "deepseek", True, True),  # the model in use's own vendor
    ]


def test_usable_backups_of_other_vendors_are_offered():
    offers = fb.suggest_backups(_Registry())
    assert [(o["provider"], o["model"]) for o in offers] == [
        ("openrouter", "google/gemini-2.5-flash"), ("groq", "llama-3.3-70b"), ("server-gpu", "qwen2.5-32b")]



def test_a_provider_whose_last_check_failed_is_not_offered(monkeypatch):
    # Live 2026-11-08: Z.AI was on, with a key, its Test failing
    # (chat_failing), and Settings offered it as a backup.
    rows = [dict(r) for r in ROWS]
    for r in rows:
        if r["name"] == "groq":
            r["health"] = "chat_failing"
        if r["name"] == "server-gpu":
            r["health"] = "down"
        if r["name"] == "openrouter":
            r["health"] = "healthy"
    offers = fb.suggest_backups(_Registry(rows))
    assert [o["provider"] for o in offers] == ["openrouter"]
    # Control: the same rows without the health rule offer all three.
    monkeypatch.setattr(fb, "_FAILED_HEALTH", frozenset())
    assert [o["provider"] for o in fb.suggest_backups(_Registry(rows))] == ["openrouter", "groq", "server-gpu"]

def test_a_backup_nothing_serves_is_refused_on_save(monkeypatch):
    import kazma_core.model_registry as mr
    from kazma_core.settings_manager import SettingsManager

    written: list[Any] = []
    store = type("S", (), {"batch_set": lambda self, items: written.extend(items)})()
    monkeypatch.setattr(mr, "get_model_registry", lambda: _Registry())
    manager = SettingsManager.__new__(SettingsManager)
    manager._cs = store
    with pytest.raises(ValueError, match="gemini-2.5-flash"):
        manager.save_nonstop_settings({"failover_chain": "gemini-2.5-flash"})
    assert written == []
    manager.save_nonstop_settings({"failover_chain": "google/gemini-2.5-flash, llama-3.3-70b"})
    assert ("agent.nonstop.failover.chain", "google/gemini-2.5-flash,llama-3.3-70b", "agent") in written


def test_settings_shows_the_status_and_offers_a_backup():
    """The page's half (templates/settings.html, settings_agent.js)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
    html = (root / "templates" / "settings.html").read_text(encoding="utf-8")
    js = (root / "static" / "js" / "settings_agent.js").read_text(encoding="utf-8")
    assert "failoverBackup.chain" in html and "backupSuggestionsShown()" in html and "useBackup(s.model)" in html
    assert "/api/settings/failover-backup" in js
