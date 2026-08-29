"""Lightweight internationalization (i18n) system for Kazma UI.

Provides a ``t()`` translation function and a ``TRANSLATIONS`` dict keyed by
language code.  Exposed to Jinja2 templates so that ``{{ t('chat.send') }}``
renders the string in the currently configured language.

Only ``en`` and ``ar`` are shipped by default, but the structure supports
adding more languages by extending ``TRANSLATIONS``.

Usage in templates (after the global is registered)::

    {{ t('nav.dashboard') }}
    {{ t('chat.placeholder') }}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["TRANSLATIONS", "get_arabic_plural_form", "t_plural", "t", "make_translator"]

# ---------------------------------------------------------------------------
# Translation dictionaries
# ---------------------------------------------------------------------------
#
# Keys are dotted strings organised by section (nav, chat, dashboard, …).
# Every key must have an ``en`` entry; ``ar`` entries are provided for the
# Arabic-first experience.  Missing keys fall back to English, and if the
# English key is also missing the dotted key itself is returned.
# ---------------------------------------------------------------------------

from kazma_ui.i18n.catalog import merged as _merged

# The 1,979-entry literal that used to live here now lives in
# ``kazma_ui/i18n/catalog/`` as one module per UI section (audit O5).
# Merged at import so ``TRANSLATIONS`` keeps its exact previous shape.
TRANSLATIONS: dict[str, dict[str, str]] = _merged()

def get_arabic_plural_form(count: int | float) -> str:
    """Return the CLDR plural category for Arabic based on count.

    Categories: 'zero', 'one', 'two', 'few', 'many', 'other'
    """
    n = abs(float(count))
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"

    mod100 = int(n) % 100
    if 3 <= mod100 <= 10:
        return "few"
    if 11 <= mod100 <= 99:
        return "many"
    return "other"


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Translate *key* into *lang*.

    Falls back to English if the language is missing, and to the key itself
    if the key is entirely unknown (so templates never break).

    Optional ``kwargs`` provide ``str.format`` interpolation::

        t('common.welcome', lang='ar', name='أحمد')
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("en") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass  # leave unfilled placeholders intact rather than crashing
    return text


def t_plural(key: str, count: int | float, lang: str = "en", **kwargs: Any) -> str:
    """Plural-aware translation lookup supporting 6-form Arabic CLDR rules."""
    if lang == "ar":
        category = get_arabic_plural_form(count)
        plural_key = f"{key}.{category}"
        entry = TRANSLATIONS.get(plural_key)
        if entry:
            text = entry.get("ar") or entry.get("en")
            if text:
                return text.format(n=count, **kwargs)

    # Fallback to standard 2-form or single key
    form = "one" if count == 1 else "other"
    fallback_key = f"{key}.{form}"
    entry = TRANSLATIONS.get(fallback_key) or TRANSLATIONS.get(key)
    if entry:
        text = entry.get(lang) or entry.get("en") or key
        return text.format(n=count, **kwargs)
    return key


def make_translator(lang: str = "en"):
    """Return closures bound to *lang* for use as Jinja2 globals."""

    def _t(key: str, **kwargs: Any) -> str:
        return t(key, lang=lang, **kwargs)

    return _t


# Supported language codes (for validation / UI toggles)
SUPPORTED_LANGUAGES = sorted({lang for entry in TRANSLATIONS.values() for lang in entry})


def _patch_jinja2_templates() -> None:
    """Patch ``Jinja2Templates.__init__`` to inject default i18n globals."""
    try:
        from fastapi.templating import Jinja2Templates as _Templates
    except Exception as exc:  # pragma: no cover — FastAPI always installed
        logging.getLogger(__name__).debug("Cannot patch Jinja2Templates: %s", exc)
        return

    # Guard against double-patching
    if getattr(_Templates.__init__, "_kazma_i18n_patched", False):
        return

    _original_init = _Templates.__init__

    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = _original_init(self, *args, **kwargs)
        try:
            env = self.env
            env.globals.setdefault("t", make_translator("en"))
            env.globals.setdefault("t_plural", lambda key, count, **kw: t_plural(key, count, lang="en", **kw))
            env.globals.setdefault("lang", lambda: "en")
            env.globals.setdefault("dir", lambda: "ltr")
        except Exception as exc:
            logger.debug("i18n Jinja2 patch failed: %s", exc)
        return result
    _patched_init._kazma_i18n_patched = True
    _Templates.__init__ = _patched_init


_patch_jinja2_templates()
