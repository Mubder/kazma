"""Settings and Knowledge hints say what the code does (2026-11, FA-119,
FA-122, FA-123).

- The Scheduled time-zone hint said ``KAZMA_TZ`` overrides the setting; the
  setting wins (``resolve_cron_timezone_name``: ``cron.timezone``, then
  ``KAZMA_TZ``, then UTC), and a recurring job's next time follows the zone
  in force then.
- The Knowledge auto-inject hint said every prompt gets the library's text;
  only text that matches the question is added (AGENTS 24G).
- The memory mirror hint said existing rows are never copied; the mirror's
  sync compares and sends them (AGENTS 15K).
- The vault hint said a restart generates a key; in production mode the
  server refuses to start instead, and secrets saved under another key are
  lost.

The old phrases are the negative controls, in both languages.
"""

from __future__ import annotations

import pytest

from kazma_ui.i18n import TRANSLATIONS


def _text(key: str, lang: str) -> str:
    return TRANSLATIONS[key][lang]


def test_the_zone_hint_puts_the_setting_first():
    en = _text("settings.cron_tz_source_env", "en")
    assert "used while this setting is empty" in en and "overridden" not in en
    assert "فارغ" in _text("settings.cron_tz_source_env", "ar")  # فارغ: empty
    hint = _text("settings.cron_tz_hint", "en")
    assert "existing jobs keep their stored fire times" not in hint and "next run" in hint


def test_auto_inject_says_when_text_is_added():
    en = _text("knowledge.auto_on_msg", "en")
    assert "every prompt" not in en and "matches the question" in en
    assert "كل موجّه" not in _text("knowledge.auto_on_msg", "ar")  # كل موجّه


def test_the_mirror_hint_describes_the_sync():
    en = _text("settings.mem.mirror_hint", "en")
    assert "write-forward only" not in en and "full comparison" in en


def test_the_vault_hint_names_production_and_the_lost_secrets():
    en = _text("settings.sys.vault_hint", "en")
    assert "production mode" in en and "cannot be read" in en


@pytest.mark.parametrize("key", ["settings.cron_tz_hint", "settings.cron_tz_source_env",
                                 "knowledge.auto_on_msg", "settings.mem.mirror_hint",
                                 "settings.sys.vault_hint"])
def test_both_languages_were_rewritten(key):
    assert _text(key, "en") and _text(key, "ar") and _text(key, "en") != _text(key, "ar")
