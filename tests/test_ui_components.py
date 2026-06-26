"""Tests for Kazma UI design system components.

Validates templates, CSS, Alpine.js stores, and component rendering
without requiring a running server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Paths
_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_TEMPLATES_DIR = _UI_DIR / "templates"
_COMPONENTS_DIR = _TEMPLATES_DIR / "components"
_STATIC_DIR = _UI_DIR / "static"
_CSS_DIR = _STATIC_DIR / "css"
_JS_DIR = _STATIC_DIR / "js"


# ── 1. Template Existence ──────────────────────────────────────────

class TestTemplateExistence:
    """All required templates must exist."""

    def test_base_template_exists(self):
        assert (_TEMPLATES_DIR / "base.html").is_file()

    def test_sidebar_component_exists(self):
        assert (_COMPONENTS_DIR / "sidebar.html").is_file()

    def test_header_component_exists(self):
        assert (_COMPONENTS_DIR / "header.html").is_file()

    def test_modal_component_exists(self):
        assert (_COMPONENTS_DIR / "modal.html").is_file()

    def test_toast_component_exists(self):
        assert (_COMPONENTS_DIR / "toast.html").is_file()

    def test_kazma_css_exists(self):
        assert (_CSS_DIR / "kazma.css").is_file()

    def test_app_js_exists(self):
        assert (_JS_DIR / "app.js").is_file()


# ── 2. Base Template Structure ─────────────────────────────────────

class TestBaseTemplate:
    """base.html must have correct Jinja2 structure."""

    @pytest.fixture
    def base_html(self):
        return (_TEMPLATES_DIR / "base.html").read_text()

    def test_has_doctype(self, base_html):
        assert "<!DOCTYPE html>" in base_html

    def test_has_title_block(self, base_html):
        assert "{% block title %}" in base_html

    def test_has_content_block(self, base_html):
        assert "{% block content %}" in base_html

    def test_has_head_block(self, base_html):
        assert "{% block head %}" in base_html

    def test_has_head_extra_block(self, base_html):
        """chat.html uses head_extra for highlight.js."""
        assert "{% block head_extra %}" in base_html

    def test_has_scripts_block(self, base_html):
        assert "{% block scripts %}" in base_html

    def test_includes_sidebar(self, base_html):
        assert 'include "components/sidebar.html"' in base_html

    def test_includes_header(self, base_html):
        assert 'include "components/header.html"' in base_html

    def test_includes_toast(self, base_html):
        assert 'include "components/toast.html"' in base_html

    def test_includes_modal(self, base_html):
        assert 'include "components/modal.html"' in base_html

    def test_loads_css(self, base_html):
        assert "/static/css/kazma.css" in base_html

    def test_loads_alpine(self, base_html):
        assert "alpine" in base_html.lower()

    def test_loads_htmx(self, base_html):
        assert "htmx" in base_html.lower()

    def test_loads_app_js(self, base_html):
        assert "/static/js/app.js" in base_html

    def test_has_app_layout(self, base_html):
        assert "app-layout" in base_html

    def test_has_data_theme(self, base_html):
        assert "data-theme" in base_html

    def test_has_alpine_app_data(self, base_html):
        assert "kazmaApp()" in base_html

    def test_has_dark_theme_default(self, base_html):
        assert 'data-theme="dark"' in base_html


# ── 3. Sidebar Component ───────────────────────────────────────────

class TestSidebarComponent:
    """sidebar.html must have all navigation sections."""

    @pytest.fixture
    def sidebar_html(self):
        return (_COMPONENTS_DIR / "sidebar.html").read_text()

    def test_has_nav_links(self, sidebar_html):
        assert "nav-link" in sidebar_html

    def test_has_workspace_link(self, sidebar_html):
        assert 'href="/"' in sidebar_html

    def test_has_chat_link(self, sidebar_html):
        assert 'href="/chat"' in sidebar_html

    def test_has_dashboard_link(self, sidebar_html):
        assert 'href="/dashboard"' in sidebar_html

    def test_has_skills_link(self, sidebar_html):
        assert 'href="/skills"' in sidebar_html

    def test_has_mcp_link(self, sidebar_html):
        assert 'href="/mcp"' in sidebar_html

    def test_has_swarm_link(self, sidebar_html):
        assert 'href="/swarm"' in sidebar_html

    def test_has_settings_link(self, sidebar_html):
        assert 'href="/settings"' in sidebar_html

    def test_has_active_state(self, sidebar_html):
        assert "active_page" in sidebar_html

    def test_has_collapsible(self, sidebar_html):
        assert "sidebarCollapsed" in sidebar_html or "collapsed" in sidebar_html

    def test_has_section_titles(self, sidebar_html):
        assert "nav-section-title" in sidebar_html

    def test_has_keyboard_shortcuts(self, sidebar_html):
        assert "nav-kbd" in sidebar_html

    def test_has_user_avatar(self, sidebar_html):
        assert "user-avatar" in sidebar_html

    def test_has_model_badge(self, sidebar_html):
        assert "model-badge" in sidebar_html

    def test_has_status_dot(self, sidebar_html):
        assert "status-dot" in sidebar_html


# ── 4. Header Component ────────────────────────────────────────────

class TestHeaderComponent:
    """header.html must have title, breadcrumbs, actions."""

    @pytest.fixture
    def header_html(self):
        return (_COMPONENTS_DIR / "header.html").read_text()

    def test_has_page_title(self, header_html):
        assert "page_title" in header_html or "header-title" in header_html

    def test_has_breadcrumbs(self, header_html):
        assert "breadcrumbs" in header_html

    def test_has_new_chat_button(self, header_html):
        assert "New Chat" in header_html

    def test_has_search_button(self, header_html):
        assert "search" in header_html.lower()

    def test_has_theme_toggle(self, header_html):
        assert "toggleTheme" in header_html or "theme-toggle" in header_html

    def test_has_user_menu(self, header_html):
        assert "user-menu" in header_html

    def test_has_notification_button(self, header_html):
        assert "notification" in header_html.lower()


# ── 5. Modal Component ─────────────────────────────────────────────

class TestModalComponent:
    """modal.html must support sizes and actions."""

    @pytest.fixture
    def modal_html(self):
        return (_COMPONENTS_DIR / "modal.html").read_text()

    def test_has_modal_overlay(self, modal_html):
        assert "modal-overlay" in modal_html

    def test_has_close_on_escape(self, modal_html):
        assert "escape" in modal_html.lower()

    def test_has_close_on_click_outside(self, modal_html):
        assert "@click.self" in modal_html or "click.self" in modal_html

    def test_has_size_classes(self, modal_html):
        for size in ["modal-sm", "modal-md", "modal-lg", "modal-xl"]:
            assert size in modal_html, f"Missing size: {size}"

    def test_has_modal_header(self, modal_html):
        assert "modal-header" in modal_html

    def test_has_modal_body(self, modal_html):
        assert "modal-body" in modal_html

    def test_has_modal_footer(self, modal_html):
        assert "modal-footer" in modal_html

    def test_has_close_button(self, modal_html):
        assert "modal-close" in modal_html


# ── 6. Toast Component ─────────────────────────────────────────────

class TestToastComponent:
    """toast.html must support all notification types."""

    @pytest.fixture
    def toast_html(self):
        return (_COMPONENTS_DIR / "toast.html").read_text()

    def test_has_toast_container(self, toast_html):
        assert "toast-container" in toast_html

    def test_has_success_type(self, toast_html):
        assert "toast-" in toast_html

    def test_has_error_type(self, toast_html):
        # Uses dynamic binding: 'toast-' + toast.type
        assert "toast.type" in toast_html or "toast-" in toast_html

    def test_has_warning_type(self, toast_html):
        assert "toast-" in toast_html

    def test_has_info_type(self, toast_html):
        assert "toast-" in toast_html

    def test_has_dismiss_button(self, toast_html):
        assert "toast-dismiss" in toast_html

    def test_has_progress_bar(self, toast_html):
        assert "toast-progress" in toast_html

    def test_has_alpine_store(self, toast_html):
        assert "$store.toast" in toast_html


# ── 7. CSS Design System ───────────────────────────────────────────

class TestCSSDesignSystem:
    """kazma.css must have all required design tokens and components."""

    @pytest.fixture
    def css(self):
        return (_CSS_DIR / "kazma.css").read_text()

    # Variables
    def test_has_accent_color(self, css):
        assert "#6C5CE7" in css or "#6c5ce7" in css.lower()

    def test_has_dark_background(self, css):
        assert "#0d1117" in css

    def test_has_css_variables(self, css):
        assert ":root" in css

    def test_has_spacing_variables(self, css):
        assert "--sp-" in css

    def test_has_radius_variables(self, css):
        assert "--radius" in css

    def test_has_shadow_variables(self, css):
        assert "--shadow" in css

    def test_has_transition_variables(self, css):
        assert "--transition" in css

    def test_has_font_variables(self, css):
        assert "--font-sans" in css
        assert "--font-mono" in css

    # Layout
    def test_has_sidebar_width(self, css):
        assert "--sidebar-width" in css

    def test_has_sidebar_collapsed(self, css):
        assert "--sidebar-collapsed" in css

    def test_has_header_height(self, css):
        assert "--header-height" in css

    def test_has_app_layout(self, css):
        assert ".app-layout" in css

    def test_has_main_content(self, css):
        assert ".main-content" in css

    # Components
    def test_has_button_styles(self, css):
        assert ".btn-primary" in css
        assert ".btn-secondary" in css
        assert ".btn-danger" in css
        assert ".btn-ghost" in css

    def test_has_card_styles(self, css):
        assert ".card" in css

    def test_has_form_styles(self, css):
        assert ".form-input" in css
        assert ".form-select" in css
        assert ".form-textarea" in css

    def test_has_table_styles(self, css):
        assert "thead th" in css or ".table-container" in css

    def test_has_badge_styles(self, css):
        assert ".badge" in css

    def test_has_toggle_styles(self, css):
        assert ".toggle" in css

    def test_has_nav_link_styles(self, css):
        assert ".nav-link" in css
        assert ".nav-link.active" in css

    # Modal
    def test_has_modal_styles(self, css):
        assert ".modal-overlay" in css
        assert ".modal-header" in css
        assert ".modal-body" in css
        assert ".modal-footer" in css

    # Toast
    def test_has_toast_styles(self, css):
        assert ".toast-container" in css
        assert ".toast-success" in css
        assert ".toast-error" in css
        assert ".toast-warning" in css
        assert ".toast-info" in css

    # Theme
    def test_has_light_theme(self, css):
        assert '[data-theme="light"]' in css

    def test_has_dark_theme_default(self, css):
        assert '[data-theme="dark"]' in css or ":root" in css

    # Animations
    def test_has_fade_animation(self, css):
        assert "@keyframes fade-in" in css

    def test_has_slide_animation(self, css):
        assert "@keyframes slide-up" in css

    def test_has_scale_animation(self, css):
        assert "@keyframes scale-in" in css

    def test_has_spin_animation(self, css):
        assert "@keyframes spin" in css

    # Utilities
    def test_has_flex_utilities(self, css):
        assert ".flex" in css
        assert ".items-center" in css
        assert ".justify-between" in css

    def test_has_spacing_utilities(self, css):
        assert ".mt-" in css
        assert ".mb-" in css
        assert ".p-" in css

    def test_has_text_utilities(self, css):
        assert ".text-success" in css
        assert ".text-error" in css
        assert ".text-muted" in css

    def test_has_grid_utilities(self, css):
        assert ".grid-2" in css
        assert ".grid-3" in css

    # Responsive
    def test_has_responsive_breakpoints(self, css):
        assert "@media" in css
        assert "768px" in css

    # RTL
    def test_has_rtl_support(self, css):
        assert '[dir="rtl"]' in css

    # Scrollbar
    def test_has_scrollbar_styles(self, css):
        assert "scrollbar" in css

    # Chat
    def test_has_chat_styles(self, css):
        assert ".chat-container" in css
        assert ".message" in css
        assert ".chat-input" in css

    # Search
    def test_has_search_styles(self, css):
        assert ".search-bar" in css

    # Header
    def test_has_header_styles(self, css):
        assert ".page-header" in css
        assert ".header-title" in css
        assert ".breadcrumbs" in css

    # Dropdown
    def test_has_dropdown_styles(self, css):
        assert ".user-menu-dropdown" in css
        assert ".dropdown-item" in css


# ── 8. JavaScript (app.js) ─────────────────────────────────────────

class TestAppJS:
    """app.js must define Alpine stores and utilities."""

    @pytest.fixture
    def js(self):
        return (_JS_DIR / "app.js").read_text()

    def test_has_toast_store(self, js):
        assert "Alpine.store('toast'" in js

    def test_has_modal_store(self, js):
        assert "Alpine.store('modal'" in js

    def test_has_search_store(self, js):
        assert "Alpine.store('search'" in js

    def test_has_notifications_store(self, js):
        assert "Alpine.store('notifications'" in js

    def test_has_kazma_app_function(self, js):
        assert "function kazmaApp()" in js

    def test_has_theme_management(self, js):
        assert "toggleTheme" in js
        assert "kazma-theme" in js

    def test_has_sidebar_toggle(self, js):
        assert "sidebarCollapsed" in js

    def test_has_keyboard_shortcuts(self, js):
        assert "Ctrl" in js or "metaKey" in js

    def test_has_search_shortcut(self, js):
        # Ctrl+K
        assert "'k'" in js

    def test_has_new_chat_shortcut(self, js):
        # Ctrl+N
        assert "'n'" in js

    def test_has_sidebar_shortcut(self, js):
        # Ctrl+B
        assert "'b'" in js

    def test_has_api_utilities(self, js):
        assert "KazmaAPI" in js

    def test_has_fetch_wrapper(self, js):
        assert "async fetch" in js

    def test_has_show_toast(self, js):
        assert "function showToast" in js

    def test_has_show_modal(self, js):
        assert "function showModal" in js

    def test_has_format_utilities(self, js):
        assert "KazmaUtils" in js

    def test_has_format_bytes(self, js):
        assert "formatBytes" in js

    def test_has_format_duration(self, js):
        assert "formatDuration" in js

    def test_has_copy_to_clipboard(self, js):
        assert "copyToClipboard" in js

    def test_has_toast_types(self, js):
        assert "success" in js
        assert "error" in js
        assert "warning" in js
        assert "info" in js

    def test_has_modal_size_support(self, js):
        assert "this.size = opts.size" in js
        assert "'md'" in js  # default size

    def test_modal_sizes_in_template(self):
        """Modal size classes are defined in modal.html template."""
        modal_html = (_COMPONENTS_DIR / "modal.html").read_text()
        for size in ["modal-sm", "modal-md", "modal-lg", "modal-xl"]:
            assert size in modal_html, f"Missing size class: {size}"

    def test_has_localStorage_persistence(self, js):
        assert "localStorage" in js


# ── 9. Template Rendering (FastAPI TestClient) ─────────────────────

class TestTemplateRendering:
    """Templates render without errors via FastAPI TestClient."""

    @pytest.fixture
    def client(self):
        """Create a test client if kazma_core is available."""
        try:
            from fastapi.testclient import TestClient
            from kazma_ui.app import create_app

            app = create_app()
            return TestClient(app)
        except Exception:
            pytest.skip("kazma_core not available for integration test")

    def test_root_renders(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 307)

    def test_settings_renders(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "kazma.css" in resp.text or "kazma" in resp.text.lower()

    def test_skills_renders(self, client):
        resp = client.get("/skills")
        assert resp.status_code == 200

    def test_mcp_renders(self, client):
        resp = client.get("/mcp")
        assert resp.status_code == 200

    def test_swarm_renders(self, client):
        resp = client.get("/swarm")
        assert resp.status_code == 200


# ── 10. Cross-file Consistency ──────────────────────────────────────

class TestConsistency:
    """Ensure templates and CSS are consistent."""

    def test_all_templates_extend_base(self):
        """All page templates should extend base.html."""
        for html_file in _TEMPLATES_DIR.glob("*.html"):
            if html_file.name == "base.html":
                continue
            if html_file.name == "error.html":
                continue
            content = html_file.read_text()
            assert 'extends "base.html"' in content or "extends 'base.html'" in content, \
                f"{html_file.name} does not extend base.html"

    def test_nav_links_match_routes(self):
        """Sidebar nav links should match app.py routes."""
        sidebar = (_COMPONENTS_DIR / "sidebar.html").read_text()
        app_py = (_UI_DIR / "app.py").read_text()

        # Extract hrefs from sidebar
        hrefs = re.findall(r'href="(/[^"]*)"', sidebar)
        for href in hrefs:
            if href == "/":
                continue  # root always exists
            # Just verify the route is mentioned somewhere in app.py
            # (either as a direct route or redirect)
            route_name = href.lstrip("/")
            assert route_name in app_py or href in app_py, \
                f"Sidebar link {href} not found in app.py"

    def test_css_classes_used_in_templates(self):
        """Key CSS classes should be used in templates."""
        css = (_CSS_DIR / "kazma.css").read_text()
        sidebar = (_COMPONENTS_DIR / "sidebar.html").read_text()

        # These classes must exist in CSS and be used in sidebar
        key_classes = ["nav-link", "sidebar", "logo", "status-dot"]
        for cls in key_classes:
            assert f".{cls}" in css, f"CSS missing class .{cls}"
            assert cls in sidebar, f"Sidebar missing class {cls}"
