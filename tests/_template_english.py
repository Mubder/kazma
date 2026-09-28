"""A template's source as an English reader gets it.

Each ``{{ t('key') }}`` is replaced by the catalog's English, escaped the way
Jinja's autoescape does unless the call ends in ``| safe``. The pages were
translated on 2026-09-28: the words that tests lock ("API version segment",
"Telegram — Main bot", aria-label="Plan") moved from the templates into the
catalog. What those tests lock -- the page says these words -- is unchanged,
so they read the page through this instead of the raw source.
"""

from __future__ import annotations

import re
from pathlib import Path

_T_CALL = re.compile(r"\{\{\s*t\('([a-z0-9_.]+)'\)\s*(\|\s*safe\s*)?\}\}")


def template_english(path: Path) -> str:
    from kazma_ui.i18n import TRANSLATIONS
    from markupsafe import escape

    src = path.read_text(encoding="utf-8")

    def english(m: re.Match[str]) -> str:
        entry = TRANSLATIONS.get(m.group(1))
        if not entry:
            return m.group(0)
        return entry["en"] if m.group(2) else str(escape(entry["en"]))

    return _T_CALL.sub(english, src)
