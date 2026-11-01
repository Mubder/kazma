"""The log keeps its format when the settings store cannot answer at boot.

Live 2026-10-30: Windows booted, the guard started Kazma before Postgres was
up, and ``setup_logging`` asked the settings store for ``logging.format``.
The store could not answer, the format fell back to plain text, and the
server wrote plain lines until its next reload, 31 hours later. The daily
digest read JSON lines only and on 2026-10-31 reported "Turns completed: 0
... No failures, no restarts, no alerts" over a day it never read
(tests/test_daily_digest.py now reads both shapes).

The settings store is seeded from the install's kazma.yaml, so that is where
a logging setting stands when the store cannot answer.
"""

from __future__ import annotations

import pytest
from kazma_core import config_loader, config_store, logging_config


def _store_down(monkeypatch):
    def unavailable():
        raise RuntimeError("Postgres is not up yet")

    monkeypatch.setattr(config_store, "get_config_store", unavailable)


@pytest.fixture()
def shipped_logging(monkeypatch):
    monkeypatch.delenv("KAZMA_LOG_FORMAT", raising=False)
    monkeypatch.delenv("KAZMA_LOG_LEVEL", raising=False)
    monkeypatch.delenv("KAZMA_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(
        config_loader, "install_yaml_section",
        lambda *keys: {"format": "json", "level": "DEBUG", "retention_days": 30}
        if keys == ("logging",) else {},
    )


def test_a_boot_before_the_settings_store_keeps_the_shipped_format(monkeypatch, shipped_logging):
    _store_down(monkeypatch)
    assert logging_config.resolve_log_format() == "json"
    assert logging_config._resolve_level_with_config(None) == "DEBUG"
    assert logging_config.resolve_retention_days() == 30


def test_a_setting_the_store_has_wins_over_the_yaml(monkeypatch, shipped_logging):
    class Store:
        def get(self, key):
            return {"logging.format": "text", "logging.retention_days": 3}.get(key)

    monkeypatch.setattr(config_store, "get_config_store", lambda: Store())
    assert logging_config.resolve_log_format() == "text"
    assert logging_config.resolve_retention_days() == 3
    # Unset in the store: the YAML's.
    assert logging_config._resolve_level_with_config(None) == "DEBUG"


def test_the_old_reader_fell_back_to_plain_text(monkeypatch, shipped_logging):
    """Negative control: the store alone, as it was read until 2026-11-01."""
    _store_down(monkeypatch)

    def store_only(key):
        try:
            return config_store.get_config_store().get(key)
        except Exception:
            return None

    monkeypatch.setattr(logging_config, "_store_get", store_only)
    assert logging_config.resolve_log_format() == "text"


def test_the_shipped_kazma_yaml_logs_json(monkeypatch):
    """What the fallback reads on a real install: the shipped file."""
    monkeypatch.delenv("KAZMA_LOG_FORMAT", raising=False)
    _store_down(monkeypatch)
    assert config_loader.install_yaml_section("logging").get("format") == "json"
    assert logging_config.resolve_log_format() == "json"
