"""The interface says what the backup does, on the schedule it runs (FA-116).

The 2026-11 functional audit: Settings → Backup said the backup "runs
automatically every 24h" and holds "vectors ... and Postgres". The scheduler
runs it every 6 hours (``_BACKUP_EXPORT_INTERVAL_HOURS``); search vectors are
not copied, they are rebuilt (``universal._rebuilt_dirs``); and the universal
backup runs no ``pg_dump`` -- it records whether the separate one is fresh.

Every interface string about backups is read in both languages: any number of
hours it names is the scheduler's, and the description never claims the
vectors or Postgres as part of the copy. The old string is the negative
control.
"""

from __future__ import annotations

import importlib
import pkgutil
import re

import kazma_ui.i18n.catalog as catalog
from kazma_core.memory.worker_bootstrap import _BACKUP_EXPORT_INTERVAL_HOURS

_HOURS = re.compile(r"(\d+)\s*(?:h\b|hours?\b|hrs?\b|ساعات|ساعة)")
_OLD = {
    "en": "Backs up ALL Kazma data: every database, chat history, beliefs, vectors, settings, documents, "
          "workspace, and Postgres. Runs automatically every 24h. Use this button to run it now.",
    "ar": "ينسخ احتياطياً كل بيانات كاظمه: كل قواعد البيانات، سجل المحادثات، المعتقدات، المتجهات، "
          "الإعدادات، المستندات، مساحة العمل، و Postgres.",
}


def _backup_strings() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for module in pkgutil.iter_modules(catalog.__path__):
        table = getattr(importlib.import_module(f"{catalog.__name__}.{module.name}"), "TRANSLATIONS", {})
        found.update({key: value for key, value in table.items() if "backup" in key.lower()})
    return found


def wrong_hours(text: str) -> set[int]:
    return {int(n) for n in _HOURS.findall(text)} - {_BACKUP_EXPORT_INTERVAL_HOURS}


def claims_vectors_or_postgres_copied(text: str) -> bool:
    """The description lists vectors or Postgres among what it copies."""
    lowered = text.lower()
    says_vectors = "vectors" in lowered or "المتجهات" in text
    vectors_ok = "not copied" in lowered or "لا تُنسخ" in text
    says_postgres = "postgres" in lowered
    postgres_ok = "its own dump" in lowered or "نسخة خاصة بها" in text
    return (says_vectors and not vectors_ok) or (says_postgres and not postgres_ok)


def test_every_backup_string_names_the_schedule_it_runs_on():
    strings = _backup_strings()
    assert "settings.backup_desc" in strings
    for key, value in strings.items():
        for lang in ("en", "ar"):
            assert not wrong_hours(value.get(lang, "")), (key, lang, value.get(lang))


def test_the_description_says_what_the_backup_holds_in_both_languages():
    desc = _backup_strings()["settings.backup_desc"]
    for lang in ("en", "ar"):
        assert str(_BACKUP_EXPORT_INTERVAL_HOURS) in desc[lang], lang
        assert not claims_vectors_or_postgres_copied(desc[lang]), lang


def test_the_old_description_fails_both_rules():
    """Negative control: the text before FA-116."""
    assert wrong_hours(_OLD["en"]) == {24}
    assert claims_vectors_or_postgres_copied(_OLD["en"])
    assert claims_vectors_or_postgres_copied(_OLD["ar"])
