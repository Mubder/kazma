"""Tests for Wave 4 audit fixes (2026-09-04 audit).

Covers:
- M23: Canonical escapeHtml in util.js encoding quotes & apostrophes
- H11 / M22: Modal store focus capture, focus restoration, overlayOpen tracking,
             and accessibility attributes in modal.html & swarm.html
- stores.js: Dialog fallback recursion fix and doSearch monotonicity guard
- H2 / H9: Research.js event delegation (no inline onclick concatenation),
           SSE tracking in __kazmaEventSources, soft-nav teardown registration
- H9: Replay.js pollTimer clearing and soft-nav teardown registration
- H9: Nav.js teardownLiveSockets handling array of teardown hooks
- H10: Chat.js global Escape & input Escape guarded by modal/overlay open,
       capacity pills gated during _isGenerating
- M24: Dashboard.js HTML escaping in features & traces
- L6: Dashboard.js skeleton fallback unhang
- L15: hitl_approval.js encodeURIComponent on thread ID
- L5: kazma.v5.css .message-avatar-user selector
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
TEMPLATES = REPO_ROOT / "kazma-ui" / "kazma_ui" / "templates"
CSS_DIR = REPO_ROOT / "kazma-ui" / "kazma_ui" / "static" / "css"


class TestWave4FrontendAudit:
    """Verifies that all Wave 4 frontend security, accessibility, and correctness claims are satisfied."""

    def test_m23_canonical_escape_html(self):
        """M23: util.js exports a single canonical escapeHtml encoding single quotes as &#39;."""
        util_js = (STATIC_JS / "modules" / "util.js").read_text(encoding="utf-8")
        assert "function escapeHtml(str)" in util_js
        assert "'&#39;'" in util_js or '"&#39;"' in util_js
        assert "KazmaUtils" in util_js and "escapeHtml(str)" in util_js
        assert "window.escapeHtml = window.escapeHtml || escapeHtml" in util_js
        assert "window.esc = window.esc || escapeHtml" in util_js

    def test_m22_stores_modal_accessibility_and_recursion_guard(self):
        """M22 & H11: stores.js tracks previous activeElement, dataset.overlayOpen, and guards native dialog recursion."""
        stores_js = (STATIC_JS / "modules" / "stores.js").read_text(encoding="utf-8")

        # Focus capture & restoration
        assert "this._previousActiveElement = document.activeElement" in stores_js
        assert "this._previousActiveElement.focus()" in stores_js
        assert "document.documentElement.dataset.overlayOpen = '1'" in stores_js
        assert "delete document.documentElement.dataset.overlayOpen" in stores_js

        # Native dialog binding order: _nativeConfirm must be defined before window.kazmaConfirm
        native_confirm_idx = stores_js.find("const _nativeConfirm =")
        kazma_confirm_idx = stores_js.find("window.kazmaConfirm =")
        assert native_confirm_idx != -1 and kazma_confirm_idx != -1
        assert native_confirm_idx < kazma_confirm_idx, "_nativeConfirm must be bound before window.kazmaConfirm"
        assert "_nativeConfirm(opts && opts.message ? opts.message : '')" in stores_js

        # Search epoch monotonicity
        assert "this._searchEpoch = (this._searchEpoch || 0) + 1" in stores_js
        assert "if (epoch !== this._searchEpoch) return;" in stores_js

    def test_m22_modal_html_accessibility(self):
        """M22: modal.html provides role=dialog, aria-labelledby, and tab focus trapping."""
        modal_html = (TEMPLATES / "components" / "modal.html").read_text(encoding="utf-8")
        assert 'role="dialog"' in modal_html
        assert 'aria-modal="true"' in modal_html
        assert 'aria-labelledby="modal-title"' in modal_html
        assert 'id="modal-title"' in modal_html
        assert "@keydown.tab=" in modal_html

    def test_swarm_modal_accessibility(self):
        """UI Plan B5: task-detail-modal in swarm.html and swarm.js supports dialog semantics and focus restore."""
        swarm_html = (TEMPLATES / "swarm.html").read_text(encoding="utf-8")
        assert 'id="task-detail-modal"' in swarm_html
        assert 'role="dialog"' in swarm_html
        assert 'aria-modal="true"' in swarm_html
        assert 'aria-labelledby="task-detail-title"' in swarm_html

        swarm_js = (STATIC_JS / "swarm.js").read_text(encoding="utf-8")
        assert "_taskDetailPrevActive = document.activeElement" in swarm_js
        assert "document.documentElement.dataset.overlayOpen = '1'" in swarm_js
        assert "delete document.documentElement.dataset.overlayOpen" in swarm_js
        assert "_taskDetailPrevActive.focus()" in swarm_js
        assert "if (e.key === 'Escape') {" in swarm_js

    def test_h2_h9_research_js_delegation_and_teardown(self):
        """H2 + H9: research.js uses event delegation (no inline onclick concatenation) and cleans up SSE/timers."""
        research_js = (STATIC_JS / "research.js").read_text(encoding="utf-8")

        # No inline onclick concatenating task IDs in list cards or buttons
        assert "onclick=\"KazmaResearch.archive('" not in research_js
        assert "onclick=\"KazmaResearch.del('" not in research_js
        assert "onclick=\"KazmaResearch.restore('" not in research_js
        assert "onclick=\"KazmaResearch.delArchived('" not in research_js
        assert 'onclick="KazmaResearch.viewDetail(\'' not in research_js

        # Uses data-act and data-task-id
        assert 'data-act="archive"' in research_js
        assert 'data-act="del"' in research_js
        assert 'data-act="restore"' in research_js
        assert 'data-act="del-archived"' in research_js
        assert 'data-act="view-detail"' in research_js

        # Tracks liveSource in __kazmaEventSources
        assert "window.__kazmaEventSources.push(liveSource)" in research_js

        # Registers soft-nav teardown and provides destroy()
        assert "_registerSoftNavTeardown()" in research_js
        assert "destroy: function ()" in research_js

    def test_h9_replay_js_poll_and_teardown(self):
        """H9: replay.js clears existing pollTimer and registers soft-nav teardown."""
        replay_js = (STATIC_JS / "replay.js").read_text(encoding="utf-8")
        assert "if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }" in replay_js
        assert "_registerSoftNavTeardown()" in replay_js
        assert "destroy: function ()" in replay_js

    def test_h9_nav_js_teardown_array_support(self):
        """H9: nav.js supports both single function and array in kazmaOnSoftNavLeave."""
        nav_js = (STATIC_JS / "modules" / "nav.js").read_text(encoding="utf-8")
        assert "else if (Array.isArray(window.kazmaOnSoftNavLeave))" in nav_js
        assert "window.kazmaOnSoftNavLeave = null" in nav_js

    def test_h10_chat_js_modal_open_escape_guard_and_capacity_gate(self):
        """H10: chat.js respects open modals on Escape and gates capacity pills during generation."""
        chat_js = (STATIC_JS / "chat.js").read_text(encoding="utf-8")
        assert "function _modalOrOverlayOpen()" in chat_js
        assert "if (_modalOrOverlayOpen()) return;" in chat_js
        assert "Please wait for generation to finish or abort first" in chat_js

    def test_m24_dashboard_js_html_escaping(self):
        """M24: dashboard.js escapes feature card text and trace metrics."""
        dashboard_js = (STATIC_JS / "dashboard.js").read_text(encoding="utf-8")
        assert "escapeHtml(f.name)" in dashboard_js
        assert "escapeHtml(f.desc)" in dashboard_js
        assert "escapeHtml(String(trace.duration_ms || ''))" in dashboard_js
        assert "escapeHtml(String(trace.tokens || '0'))" in dashboard_js
        assert "escapeHtml(String(trace.cost || '$0.00'))" in dashboard_js

    def test_l6_dashboard_js_skeleton_rescue(self):
        """L6: dashboard.js skeleton timer rescues hidden empty state."""
        dashboard_js = (STATIC_JS / "dashboard.js").read_text(encoding="utf-8")
        assert "loadingEl.style.display = 'none';" in dashboard_js
        assert "if (emptyEl) emptyEl.style.display = 'block';" in dashboard_js

    def test_l15_hitl_approval_encode_uri_component(self):
        """L15: hitl_approval.js encodes thread ID in approve fetch."""
        hitl_js = (STATIC_JS / "hitl_approval.js").read_text(encoding="utf-8")
        assert "fetch('/api/approve/' + encodeURIComponent(tid)" in hitl_js

    def test_l5_css_message_avatar_user(self):
        """L5: kazma.v5.css targets .message-avatar-user."""
        css = (CSS_DIR / "kazma.v5.css").read_text(encoding="utf-8")
        assert ".message-avatar-user, .message-avatar.u" in css
