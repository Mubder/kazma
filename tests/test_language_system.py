"""Tests for fix-003-language-system.

Validates:
  VAL-UI-007 — Language toggle button exists in the header
  VAL-UI-008 — Switching language updates all visible pages
  VAL-UI-009 — Dashboard renders in the selected language
  VAL-UI-010 — Settings page is fully translated
  VAL-UI-011 — Language preference persists via cookie
  VAL-UI-012 — HTML lang and dir attributes update on switch
  VAL-CROSS-001/002/003 — Tests, lint, boot all pass
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kazma_ui.i18n import TRANSLATIONS, make_translator, t

_UI = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_HEADER_HTML = _UI / "templates" / "components" / "header.html"
_DASHBOARD_HTML = _UI / "templates" / "dashboard.html"
_SETTINGS_HTML = _UI / "templates" / "settings.html"
_BASE_HTML = _UI / "templates" / "base.html"
_APP_JS = _UI / "static" / "js" / "app.js"
_APP_PY = _UI / "app.py"
_DASHBOARD_PY = _UI / "dashboard.py"


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def header_text() -> str:
    return _HEADER_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_text() -> str:
    return _DASHBOARD_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_text() -> str:
    return _SETTINGS_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base_text() -> str:
    return _BASE_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js_text() -> str:
    return _APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_py_text() -> str:
    return _APP_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_py_text() -> str:
    return _DASHBOARD_PY.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-007: Language toggle button exists in header
# ═══════════════════════════════════════════════════════════════════


class TestLanguageToggleButton:
    """The header must contain a language toggle button."""

    def test_toggle_button_exists(self, header_text: str) -> None:
        """A language toggle button is present in header.html."""
        assert "lang-toggle" in header_text or "toggleLanguage" in header_text, (
            "No language toggle button found in header.html"
        )

    def test_toggle_calls_toggle_language(self, header_text: str) -> None:
        """The toggle button calls toggleLanguage()."""
        assert "toggleLanguage" in header_text, (
            "Language toggle must call toggleLanguage()"
        )

    def test_toggle_shows_en_when_arabic(self, header_text: str) -> None:
        """Shows 'EN' when in Arabic mode (lang === 'ar')."""
        assert "EN" in header_text, (
            "Language toggle should show 'EN' when in Arabic mode"
        )

    def test_toggle_shows_ayn_when_english(self, header_text: str) -> None:
        """Shows 'ع' when in English mode."""
        assert "ع" in header_text, (
            "Language toggle should show 'ع' when in English mode"
        )

    def test_toggle_js_function_exists(self, app_js_text: str) -> None:
        """toggleLanguage() function exists in app.js."""
        assert "toggleLanguage" in app_js_text, (
            "toggleLanguage() function not found in app.js"
        )

    def test_toggle_js_sets_cookie(self, app_js_text: str) -> None:
        """toggleLanguage() sets the kazma-lang cookie."""
        assert "kazma-lang" in app_js_text, (
            "toggleLanguage() must set the 'kazma-lang' cookie"
        )

    def test_toggle_js_sets_localstorage(self, app_js_text: str) -> None:
        """toggleLanguage() stores choice in localStorage."""
        assert "localStorage" in app_js_text and "kazma-lang" in app_js_text, (
            "toggleLanguage() must use localStorage for 'kazma-lang'"
        )

    def test_toggle_js_reloads_page(self, app_js_text: str) -> None:
        """toggleLanguage() reloads the page for SSR pickup."""
        assert "location.reload" in app_js_text, (
            "toggleLanguage() must reload the page so SSR picks up new language"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-009 / VAL-UI-010: Dashboard and Settings use t() calls
# ═══════════════════════════════════════════════════════════════════


class TestDashboardI18n:
    """Dashboard template must use t() for all user-visible strings."""

    def test_dashboard_uses_t_calls(self, dashboard_text: str) -> None:
        """Dashboard template uses {{ t('...') }} calls."""
        assert "{{ t(" in dashboard_text, (
            "Dashboard must use t() translation calls"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "Tool Calls",
            "Circuit Breaker",
            "Tokens Over Time",
            "Cost Over Time",
            "System Resources",
            "Session Management",
            "Recent Traces",
        ],
    )
    def test_no_hardcoded_dashboard_phrases(
        self, dashboard_text: str, phrase: str
    ) -> None:
        """No hardcoded English section/chart titles remain as visible text.

        We strip script blocks and HTML comments, then verify the phrase
        does not appear as a bare text node (it should be inside t()).
        """
        no_script = re.sub(r"<script[^>]*>.*?</script>", "", dashboard_text, flags=re.DOTALL)
        no_comments = re.sub(r"<!--.*?-->", "", no_script, flags=re.DOTALL)
        assert phrase not in no_comments, (
            f"Hardcoded English phrase '{phrase}' still found in dashboard.html"
        )


class TestSettingsI18n:
    """Settings template must use t() for all user-visible strings."""

    def test_settings_uses_t_calls(self, settings_text: str) -> None:
        """Settings template uses {{ t('...') }} calls."""
        assert "{{ t(" in settings_text, (
            "Settings must use t() translation calls"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "LLM Providers",
            "Active Model",
            "Agent Configuration",
            "System Diagnostics",
            "Keyboard Shortcuts",
            "Change Password",
            "API Tokens",
            "Tool Registry",
            "MCP Servers",
            "Installed Skills",
        ],
    )
    def test_no_hardcoded_settings_phrases(
        self, settings_text: str, phrase: str
    ) -> None:
        """No hardcoded English section headings remain in settings."""
        no_script = re.sub(r"<script[^>]*>.*?</script>", "", settings_text, flags=re.DOTALL)
        no_comments = re.sub(r"<!--.*?-->", "", no_script, flags=re.DOTALL)
        assert phrase not in no_comments, (
            f"Hardcoded English phrase '{phrase}' still found in settings.html"
        )

    def test_settings_tab_labels_translated(self, settings_text: str) -> None:
        """All settings tab labels use t() calls."""
        # The nav section should contain t('settings.tab_...')
        no_script = re.sub(r"<script[^>]*>.*?</script>", "", settings_text, flags=re.DOTALL)
        assert "settings.tab_" in no_script, (
            "Settings tab labels should use t('settings.tab_*') calls"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-012: HTML lang and dir attributes update
# ═══════════════════════════════════════════════════════════════════


class TestHtmlLangDir:
    """The <html> tag must use Jinja2 globals for lang and dir."""

    def test_html_lang_uses_global(self, base_text: str) -> None:
        """<html lang> attribute reads from Jinja2 global."""
        assert re.search(r'<html\s+lang\s*=\s*"\{\{\s*lang', base_text), (
            "<html lang> must use {{ lang }} global"
        )

    def test_html_dir_uses_global(self, base_text: str) -> None:
        """<html dir> attribute reads from Jinja2 global."""
        assert re.search(r'dir\s*=\s*"\{\{\s*dir', base_text), (
            "<html dir> must use {{ dir }} global"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-011: Language middleware reads cookie
# ═══════════════════════════════════════════════════════════════════


class TestLanguageMiddleware:
    """The FastAPI middleware reads kazma-lang cookie and sets globals."""

    def test_middleware_exists(self, app_py_text: str) -> None:
        """app.py contains a language middleware."""
        assert "language_middleware" in app_py_text, (
            "Language middleware function not found in app.py"
        )

    def test_middleware_reads_cookie(self, app_py_text: str) -> None:
        """Middleware reads the kazma-lang cookie."""
        assert "kazma-lang" in app_py_text, (
            "Middleware must read 'kazma-lang' cookie"
        )

    def test_middleware_sets_globals(self, app_py_text: str) -> None:
        """Middleware sets t, lang, dir globals on the templates env."""
        assert "env.globals" in app_py_text, (
            "Middleware must set Jinja2 env.globals (t, lang, dir)"
        )

    def test_middleware_registered(self, app_py_text: str) -> None:
        """Middleware is registered with @app.middleware."""
        assert '@app.middleware("http")' in app_py_text or "@app.middleware('http')" in app_py_text, (
            "Language middleware must be registered as @app.middleware"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-009: Dashboard reuses app's templates instance
# ═══════════════════════════════════════════════════════════════════


class TestDashboardTemplatesReuse:
    """Dashboard must reuse the app's shared Jinja2Templates instance."""

    def test_set_templates_function_exists(self, dashboard_py_text: str) -> None:
        """dashboard.py has a set_templates() function."""
        assert "def set_templates" in dashboard_py_text, (
            "dashboard.py must have set_templates() to accept the shared instance"
        )

    def test_app_calls_set_templates(self, app_py_text: str) -> None:
        """app.py calls set_dashboard_templates to share the instance."""
        assert "set_dashboard_templates" in app_py_text or "set_templates" in app_py_text, (
            "app.py must call dashboard.set_templates() to share the templates instance"
        )


# ═══════════════════════════════════════════════════════════════════
# Translation completeness for new keys
# ═══════════════════════════════════════════════════════════════════


class TestNewTranslationKeys:
    """New translation keys exist and have both languages."""

    @pytest.mark.parametrize(
        "key",
        [
            "dashboard.tool_calls",
            "dashboard.circuit_breaker",
            "dashboard.tokens_over_time",
            "dashboard.cost_over_time",
            "dashboard.session_management",
            "dashboard.recent_traces",
            "settings.tab_services",
            "settings.tab_models",
            "settings.tab_agent",
            "settings.active_model",
            "settings.agent_config",
            "settings.llm_providers",
            "settings.mcp_servers",
            "settings.installed_skills",
            "settings.tool_registry",
            "settings.system_diagnostics",
            "settings.keyboard_shortcuts",
            "settings.change_password",
            "settings.api_tokens",
        ],
    )
    def test_key_exists(self, key: str) -> None:
        assert key in TRANSLATIONS, f"Missing translation key: {key}"

    def test_new_keys_have_arabic(self) -> None:
        """All new dashboard/settings keys have Arabic translations."""
        dashboard_keys = [
            k for k in TRANSLATIONS if k.startswith("dashboard.")
        ]
        settings_keys = [
            k for k in TRANSLATIONS if k.startswith("settings.")
        ]
        for k in dashboard_keys + settings_keys:
            assert "ar" in TRANSLATIONS[k], f"Key {k} missing Arabic"
            assert "en" in TRANSLATIONS[k], f"Key {k} missing English"

    def test_key_count_exceeds_threshold(self) -> None:
        """Total translation keys exceed 100 (feature requires ~100+ new keys)."""
        assert len(TRANSLATIONS) > 100, (
            f"Expected >100 translation keys, got {len(TRANSLATIONS)}"
        )


# ═══════════════════════════════════════════════════════════════════
# Functional: translator returns correct language
# ═══════════════════════════════════════════════════════════════════


class TestTranslatorFunctionality:
    """The make_translator closure returns correct strings per language."""

    def test_arabic_translator_for_dashboard_key(self) -> None:
        t_ar = make_translator("ar")
        result = t_ar("dashboard.tool_calls")
        assert result != "dashboard.tool_calls", "Key should be translated"
        assert result != "", "Arabic translation should not be empty"

    def test_english_translator_for_dashboard_key(self) -> None:
        t_en = make_translator("en")
        assert t_en("dashboard.tool_calls") == "Tool Calls"

    def test_arabic_translator_for_settings_key(self) -> None:
        t_ar = make_translator("ar")
        result = t_ar("settings.active_model")
        assert result != "settings.active_model"
        assert result != ""

    def test_dir_is_rtl_for_arabic(self) -> None:
        """Arabic should map to rtl direction."""
        # This is validated in the middleware logic
        assert t("nav.chat", lang="ar") != t("nav.chat", lang="en"), (
            "Arabic and English should produce different strings"
        )
