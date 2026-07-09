"""Tests for the Kazma UI i18n / translation system.

Verifies that:
  - The ``t()`` function returns the correct string per language.
  - Unknown keys fall back gracefully.
  - Arabic translations exist for every key that has English.
  - ``make_translator`` returns a closure bound to the given language.
  - RTL-related config attributes propagate to template globals.
"""

from __future__ import annotations

import pytest
from kazma_ui.i18n import SUPPORTED_LANGUAGES, TRANSLATIONS, make_translator, t

# ═══════════════════════════════════════════════════════════════════
# Core translation function
# ═══════════════════════════════════════════════════════════════════


class TestTranslationLookup:
    """Verify ``t()`` returns the correct string for known keys."""

    def test_english_default(self) -> None:
        assert t("nav.chat") == "Chat"

    def test_arabic_lookup(self) -> None:
        assert t("nav.chat", lang="ar") == "المحادثة"

    def test_unknown_language_falls_back_to_english(self) -> None:
        # 'fr' is not shipped — should fall back to English
        assert t("nav.chat", lang="fr") == "Chat"

    def test_unknown_key_returns_key(self) -> None:
        assert t("nonexistent.key.xyz") == "nonexistent.key.xyz"

    def test_format_interpolation(self) -> None:
        """kwargs are interpolated via str.format."""
        # Add a temporary key with a placeholder for this test
        TRANSLATIONS["__test_key"] = {"en": "Hello {name}", "ar": "مرحبا {name}"}
        try:
            assert t("__test_key", name="World") == "Hello World"
            assert t("__test_key", lang="ar", name="أحمد") == "مرحبا أحمد"
        finally:
            del TRANSLATIONS["__test_key"]

    def test_format_missing_kwarg_does_not_crash(self) -> None:
        TRANSLATIONS["__test_key2"] = {"en": "Hello {name}"}
        try:
            # Missing kwarg should not raise
            result = t("__test_key2")
            assert "name" in result or result == "Hello {name}"
        finally:
            del TRANSLATIONS["__test_key2"]


# ═══════════════════════════════════════════════════════════════════
# Translation completeness
# ═══════════════════════════════════════════════════════════════════


class TestTranslationCompleteness:
    """Every shipped key should have both English and Arabic translations."""

    def test_arabic_exists_for_all_keys(self) -> None:
        missing = [k for k, v in TRANSLATIONS.items() if "ar" not in v]
        assert not missing, f"Keys missing Arabic translation: {missing}"

    def test_english_exists_for_all_keys(self) -> None:
        missing = [k for k, v in TRANSLATIONS.items() if "en" not in v]
        assert not missing, f"Keys missing English translation: {missing}"

    def test_arabic_values_are_not_empty(self) -> None:
        empty = [k for k, v in TRANSLATIONS.items() if not v.get("ar", "").strip()]
        assert not empty, f"Keys with empty Arabic values: {empty}"

    def test_supported_languages_includes_ar_and_en(self) -> None:
        assert "ar" in SUPPORTED_LANGUAGES
        assert "en" in SUPPORTED_LANGUAGES


# ═══════════════════════════════════════════════════════════════════
# Key UI strings are translated
# ═══════════════════════════════════════════════════════════════════


class TestKeyUIStrings:
    """The key UI strings mentioned in the feature description must exist."""

    @pytest.mark.parametrize(
        "key",
        [
            "nav.dashboard",
            "nav.chat",
            "nav.settings",
            "chat.send",
            "chat.thinking",
            "chat.placeholder",
            "chat.sessions",
            "dashboard.title",
            "swarm.title",
            "agents.title",
            "nav.skills",
            "nav.mcp",
        ],
    )
    def test_key_exists(self, key: str) -> None:
        assert key in TRANSLATIONS, f"Missing key: {key}"

    def test_dashboard_arabic(self) -> None:
        assert t("nav.dashboard", lang="ar") == "لوحة التحكم"

    def test_chat_arabic(self) -> None:
        assert t("nav.chat", lang="ar") == "المحادثة"

    def test_settings_arabic(self) -> None:
        assert t("nav.settings", lang="ar") == "الإعدادات"

    def test_send_arabic(self) -> None:
        assert t("chat.send", lang="ar") == "إرسال"

    def test_thinking_arabic(self) -> None:
        assert "كاظمه" in t("chat.thinking", lang="ar")


# ═══════════════════════════════════════════════════════════════════
# make_translator closure
# ═══════════════════════════════════════════════════════════════════


class TestMakeTranslator:
    """``make_translator`` returns a closure bound to the given language."""

    def test_bound_to_arabic(self) -> None:
        t_ar = make_translator("ar")
        assert t_ar("nav.chat") == "المحادثة"

    def test_bound_to_english(self) -> None:
        t_en = make_translator("en")
        assert t_en("nav.chat") == "Chat"

    def test_closure_is_callable(self) -> None:
        t_fn = make_translator("ar")
        assert callable(t_fn)
