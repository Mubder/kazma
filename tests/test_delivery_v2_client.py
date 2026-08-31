"""Turn Delivery V2 plan P3 — client cutover source contracts.

Locks the deletion of the incident-patch mechanisms and the presence of the
V2 recovery architecture in the shipped JS. The governing rule
(docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md): every fix removes the
root-cause class; these tests fail if any heuristic patch mechanism returns.

Also runs the pure cursor tracker (modules/delivery_cursor.js) under Node to
unit-test gap/dupe semantics without a browser.
"""

from __future__ import annotations

from tests._module_source import module_source

import json
import shutil
import subprocess
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_UI = _ROOT / "kazma-ui" / "kazma_ui"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_STORE_JS = _UI / "static" / "js" / "stores" / "agentStore.js"
_STREAM_JS = _UI / "static" / "js" / "streaming.js"
_CURSOR_JS = _UI / "static" / "js" / "modules" / "delivery_cursor.js"
_VIS_JS = _UI / "static" / "js" / "modules" / "turn_visibility.js"
_CHAT_HTML = _UI / "templates" / "chat.html"


class TestPatchPileDeleted:
    """The six incident-driven recovery heuristics must stay dead."""

    def test_no_nuclear_poll_interval(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "nuclear-poll" not in src
        assert "_reconcileDelivery" not in src

    def test_no_background_turn_poller(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "_pollBackgroundTurn" not in src
        assert "_stopBackgroundPoll" not in src
        assert "_bgPollTimer" not in src

    def test_no_rendered_text_matching_heuristics(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "_bubbleShowsContent" not in src
        assert "_domMissingAssistantReply" not in src
        assert "_softApplyFinalAssistant" not in src
        assert "_finalFingerprint" not in src
        assert "data-final-fp" not in src

    def test_no_staleness_interval_in_store(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        assert "_stalenessTimer" not in src

    def test_no_prose_regex_catchup(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        # The old copy-coupled protocol: regex on "Reconnected — …running…".
        assert "/reconnected|still running/i" not in src

    def test_chat_exports_resync_not_pollers(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "pollBackgroundTurn:" not in src
        assert "resync: function" in src


class TestV2ArchitecturePresent:
    """The replacement architecture is wired on every layer."""

    def test_unconditional_resync_exists_and_is_gate_free(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function _resyncDelivery(" in src
        # Fired from all page-level triggers. (seq-gap / resume-gap fire from
        # agentStore — asserted in test_store_handles_structured_resumed_frame.)
        for trigger in ("'visibility'", "'focus'", "'pageshow'", "'init'", "'load'", "'idle-watchdog'"):
            assert trigger in src, f"missing resync trigger {trigger}"

    def test_resync_reattaches_live_stream_when_generating(self):
        """2026-08-26: resync-while-generating used to leave NO live transport —
        an undisturbed visible tab painted the reply only on manual refresh."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function _reopenSse(" in src
        assert "_reopenSseRef('resync-' + (reason || '?'))" in src
        assert "last_event_id: _lastSeqSeen" in src

    def test_sse_gap_status_routes_to_resync(self):
        """Journal-gap attach signals status=resync; the SSE client must
        reconcile instead of silently closing (dead-stream class)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "status === 'resync'" in src
        assert "_resyncDelivery('sse-gap')" in src

    def test_truncated_stream_reconciles(self):
        """Stream ended without a terminal frame → reconcile with server
        truth instead of sitting on partial text until a manual refresh."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "var truncated = !data;" in src
        assert "_resyncDelivery('sse-truncated')" in src

    def test_ws_sse_dedupe_guard(self):
        """Every journaled frame fans out to WS too — the store must not
        double-paint tokens/final reply while the SSE stream owns the live
        turn (the duplicated-bubble incident class). The approval card is
        NOT suppressed in the store anymore: dedupe moved to renderHitlCard
        (idempotent on a live card) because suppressing the WS render while
        betting on a late SSE frame left interrupted turns silently paused
        with no card at all (2026-08-26)."""
        chat_src = _CHAT_JS.read_text(encoding="utf-8")
        store_src = _STORE_JS.read_text(encoding="utf-8")
        assert "hasLiveSSE: function()" in chat_src
        # token/done painting still gated on the live SSE
        assert store_src.count("hasLiveSSE()") >= 2
        # card dedupe is at the render site, not the transport
        assert "if (hasInlineApprovalCard()) return;" in chat_src

    def test_missed_approval_card_recovery(self):
        """An interrupted turn whose card never rendered must recover it
        from /api/pending-approvals (server truth), one best-effort shot."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function recoverMissedApproval(" in src
        assert "'/api/pending-approvals'" in src
        assert "setTimeout(recoverMissedApproval, 1200)" in src

    def test_stale_stream_epoch_guard(self):
        """Superseded SSE dispatches (approval resume, re-attach, abort)
        must not paint or run terminal side effects — stale frames created
        empty bubbles and a trailing '_No response received.' AFTER a
        successful reply (2026-08-26)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "var _sseEpoch = 0;" in src
        assert "buildSseCallbacks(++_sseEpoch)" in src
        assert "function _mine() { return epoch === _sseEpoch; }" in src
        # painting + terminal + activity handlers are epoch-gated
        assert src.count("if (!_mine()) return;") >= 7

    def test_empty_notice_never_after_painted_reply(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "var _turnPainted = false;" in src
        assert "_turnPainted = true;" in src
        assert "!_turnPainted)" in src  # empty-bubble fallback guard

    def test_approve_resume_stream_guards(self):
        """The HITL approve-resume stream owns the turn: it invalidates the
        main stream's epoch, marks the turn painted on token paint, its own
        empty-notice respects _turnPainted, and the next-approval handler
        no longer eagerly creates a blank bubble (2026-08-26 six-tweet turn:
        trailing '_No response received.' + empty containers)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        # epoch bump before the approve-resume dispatch
        assert "_sseEpoch++;" in src
        # token paint marks the turn painted
        at = src.index("tokenAccum += tokenData.content;")
        assert "_turnPainted = true;" in src[at:at + 200]
        # both empty-notice branches are guarded
        assert src.count("!_turnPainted)") >= 2
        # no eager blank bubble on the next-approval timeout
        at2 = src.index("// Another danger tool after grant")
        assert "createAssistantMessage()" not in src[at2:at2 + 500]

    def test_build_identity_is_visible(self):
        """The running git commit + start time must be checkable at a glance
        (sidebar badge + /health/live) — the 2026-08-26 incident had fixes
        on disk while the process predating them served the turn."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        html = (_UI / "templates" / "chat.html").read_text(encoding="utf-8")
        assert "function _paintBuildBadge()" in src
        assert "fetch('/health/live')" in src
        assert 'id="build-badge"' in html
        from kazma_ui.health import get_build_info

        info = get_build_info()
        assert set(info) == {"commit", "started_at"}
        assert info["commit"]  # resolves or 'unknown' — never empty

    def test_undelivered_send_is_restored(self):
        """A send that never reached the server (restart/down) is parked in
        a localStorage outbox before dispatch, cleared on the first streamed
        token, and restored with a Retry button on the next load."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function _outboxWrite(" in src
        assert "_outboxWrite(content);" in src
        assert "_outboxClear();  // first streamed token" in src
        assert "function _restoreUndeliveredOutbox(" in src
        # MUST reference the in-scope `messages` variable — an earlier
        # version referenced a nonexistent `data` and ReferenceError'd
        # EVERY loadSession (the "Failed to load session messages
        # (data is not defined)" spam, 2026-08-26).
        assert "_restoreUndeliveredOutbox(messages);" in src
        assert "data && data.messages) || data" not in src
        assert "window.KazmaChat.retry()" in src

    def test_empty_terminal_is_honest_and_traced(self):
        """A turn that terminal'd without any reply must not claim 'Done'
        in the progress panel, and the lifecycle trace must be dumpable
        (the 'done in 1s, no response, message never persisted' incident
        left no evidence anywhere)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "finalizeProgress(_turnPainted ? true : 'empty');" in src
        assert "'No response'" in src
        assert "function diag(" in src
        assert "diagnostics: dumpDiagnostics," in src
        assert "diag('dispatch'," in src
        assert "diag('done'," in src
        assert "diag('empty-terminal');" in src

    def test_transient_load_failure_never_wipes_transcript(self):
        """A failed session-messages fetch (restart window / server down)
        must keep what's on screen and retry — replacing the transcript
        with an error card destroyed the latest reply (2026-08-26)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "_loadMsgAttempts" in src
        assert "var hadContent = !!(messagesEl && messagesEl.querySelector('.message'));" in src
        assert "if (!hadContent) {" in src
        assert "diag('load-messages-failed'" in src

    def test_persist_writers_stamp_ts(self):
        """Every assistant row carries ``ts`` — mixed shapes produced ts-less
        duplicate rows after restarts (2026-08-26).

        This used to be checked as two literal snippets in ``sse_chat.py``,
        one per append path. There is now exactly ONE append path: rows are
        created by ``reply_sink.upsert_reply``, so the invariant is asserted
        against its behaviour rather than against the source text of the
        writers that no longer exist.
        """
        from kazma_ui import reply_sink

        class _Sess:
            session_id = "s1"
            messages: list = []

        class _Txn:
            def __enter__(self):
                return _Sess

            def __exit__(self, *exc):
                return False

        class _Store:
            def transact(self, sid):
                return _Txn()

        original = reply_sink._store
        reply_sink._store = lambda: _Store()
        try:
            reply_sink.upsert_reply("s1", "turn-1", "answer")
        finally:
            reply_sink._store = original

        assert _Sess.messages, "the sink must create the row"
        row = _Sess.messages[-1]
        assert row["role"] == "assistant"
        assert row["ts"], "every assistant row must be timestamped"
        assert row["turn_id"] == "turn-1"

    def test_status_strip_single_owner(self):
        """The top status strip (#thinking-indicator) is Alpine-store-owned:
        beginTurn arms it for EVERY turn start (SSE, WS, approve-resume —
        submitApproval calls beginTurn), terminals clear it. The imperative
        KS.showTyping/hideTyping inline styles on typingEl fought Alpine's
        x-show — the intermittent missing status bar (2026-08-26)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function _setStatusStrip(" in src
        assert "function _clearStatusStrip(" in src
        # beginTurn arms the strip before anything else can
        at = src.index("function beginTurn(")
        seg = src[at:at + 700]
        assert "_setStatusStrip(" in seg
        # no imperative display writes on the Alpine-owned element remain
        assert "showTyping(typingEl" not in src
        assert "hideTyping(typingEl" not in src


class TestUIAuditP0Fixes:
    """docs/audits/AUDIT_UI_DEEP_2026-08-26.md — Phase 1 contracts."""

    _NAV = _UI / "static" / "js" / "modules" / "nav.js"
    _STORE = _UI / "static" / "js" / "stores" / "agentStore.js"
    _CHAT_HTML = _UI / "templates" / "chat.html"

    def test_p0_3_soft_nav_syncs_layout_class(self):
        """`is-chat` is stamped server-side per route; soft-nav must sync it
        or the header stays hidden on every page after visiting chat."""
        src = self._NAV.read_text(encoding="utf-8")
        assert "oldLayout.className = newLayout.className;" in src

    def test_p0_4_bare_x_data_is_bound(self):
        """Bare `x-data` (zero-key Alpine stack) is a LEGITIMATE bind — the
        old 'empty bind' heuristic made every soft-nav into /chat throw and
        fall back to a full reload (the 2-3 reloads symptom)."""
        src = self._NAV.read_text(encoding="utf-8")
        assert "function isEmptyAlpineBind" not in src
        at = src.index("function isAlpineBound(")
        seg = src[at:src.index("}", src.index("return true;", at))]
        assert "PRESENCE of _x_dataStack" in seg

    def test_p0_1_agent_store_registers_after_boot(self):
        """Soft-nav re-runs agentStore.js after Alpine booted (alpine:init
        never fires again) — registration must be synchronous in that case
        or $store.agent (status strip, WS bus, HITL card) stays dead."""
        src = self._STORE.read_text(encoding="utf-8")
        assert "function registerAgentStore() {" in src
        assert "if (window.Alpine) {" in src
        assert "registerAgentStore();" in src

    def test_p0_2_capacity_bar_template_is_single_owner(self):
        """No DOM relocation, no JS-built fallback bar; the chat.html template
        (composer-chrome) owns the markup — and carries the Plan pill for
        parity. The row is a visible toolbar (the ⋯ popover was removed
        2026-08-27 at operator request)."""
        js = _CHAT_JS.read_text(encoding="utf-8")
        html = self._CHAT_HTML.read_text(encoding="utf-8")
        assert "insertBefore(bar, footer)" not in js
        assert "bar.innerHTML =" not in js.split("function bindCapacityBar")[1][:2000]
        assert 'data-cap="/plan on"' in html
        assert 'aria-label="Plan"' in html


class TestUIAuditPhase2Fixes:
    """docs/audits/AUDIT_UI_DEEP_2026-08-26.md — Phase 2 contracts."""

    _NAV = _UI / "static" / "js" / "modules" / "nav.js"
    _COMP = _UI / "static" / "js" / "modules" / "components.js"

    def test_single_shortcut_registry(self):
        """Navigation shortcuts live ONLY in nav.js — components.js and
        chat.js must not carry racing Ctrl+N/1-6/K handlers."""
        comp = self._COMP.read_text(encoding="utf-8")
        chat = _CHAT_JS.read_text(encoding="utf-8")
        nav = self._NAV.read_text(encoding="utf-8")
        assert "Ctrl+1-6" not in comp  # removed block
        assert "window.location.href = '/chat';" not in comp
        assert "Alpine.store('search').toggle()" not in comp
        assert "e.key === 'n'" not in chat
        assert "e.key === 'k'" not in chat
        # nav.js owns K (search) + N (page-aware new chat)
        assert "Alpine.store('search').toggle();" in nav
        assert "KazmaChat.newSession === 'function'" in nav

    def test_all_page_scripts_versioned(self):
        """No unversioned page-script tags remain (stale-JS/fresh-HTML
        split-brain); replay.js no longer hardcodes v=2."""
        for tpl in (_UI / "templates").glob("*.html"):
            src = tpl.read_text(encoding="utf-8")
            for m in re.finditer(r'src="(/static/js/[^"?]+)"', src):
                rel = m.group(1)
                assert rel.endswith(".min.js"), (
                    f"{tpl.name}: unversioned script {rel}"
                )
        replay = (_UI / "templates" / "replay.html").read_text(encoding="utf-8")
        assert "replay.js?v=2" not in replay

    def test_js_version_globs_everything(self):
        """The cache-bust version covers the whole static/js tree — no
        hand-maintained whitelist to go stale."""
        src = (_UI / "app.py").read_text(encoding="utf-8")
        assert "_js_version_files" not in src
        assert '_js_root.rglob("*.js")' in src

    def test_loadsession_in_flight_guard(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "_loadInFlightFor" in src
        assert "if (_loadInFlightFor === sessionId) return;" in src
        # the +100ms duplicate boot schedule is gone
        assert "setTimeout(function() {\n        loadSession(initialSessionId);" not in src

    def test_resync_epoch_race_guard(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "var epochAtFetch = _sseEpoch;" in src
        assert "if (_sseEpoch !== epochAtFetch) return; // a new turn started meanwhile" in src

    def test_unhandled_rejections_surface(self):
        base = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
        assert "unhandledrejection" in base
        assert "showToast" in base
        # The toast must carry the throwing file:line so a stale-cache vs
        # real-bug question is answered without a console round-trip.
        assert "@ ' + m[1] + ':' + m[2]" in base

    def test_swarm_breaker_badge_class_driven(self):
        html = (_UI / "templates" / "swarm.html").read_text(encoding="utf-8")
        js = (_UI / "static" / "js" / "swarm.js").read_text(encoding="utf-8")
        assert 'id="cb-badge-{{ w.name }}"' not in html
        assert 'data-cb-worker="{{ w.name }}"' in html
        assert '[data-cb-worker="' in js

    def test_x_cloak_pass_applied(self):
        """The audit's worst flash sites now carry x-cloak (quote-aware tag
        matching — attributes may contain '>' inside quoted expressions)."""
        tag_re = re.compile(r"""<[a-zA-Z]+(?:[^>"']|"[^"]*"|'[^']*')*>""")
        targets = [
            _UI / "templates" / "settings.html",
            _UI / "templates" / "knowledge_base.html",
            _UI / "templates" / "components" / "modal.html",
            _UI / "templates" / "components" / "header.html",
        ]
        for p in targets:
            src2 = p.read_text(encoding="utf-8")
            for mm in tag_re.finditer(src2):
                tag = mm.group(0)
                if "x-show=" in tag and "<template" not in tag:
                    assert "x-cloak" in tag, f"{p.name}: {tag[:100]}"


class TestUIAuditPhase3Fixes:
    """docs/audits/AUDIT_UI_DEEP_2026-08-26.md — Phase 3 (P2) contracts."""

    def test_voice_buttons_restored(self):
        v5 = (_UI / "static" / "css" / "kazma.v5.css").read_text(encoding="utf-8")
        assert ".composer-voice-btn { display: inline-flex; }" in v5
        assert ".composer-voice-btn { display: none; }" not in v5

    def test_dead_topbar_css_pruned(self):
        css = (_UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
        assert "border-bottom: 1px solid var(--border-subtle);\n  background: var(--bg-panel);\n}" not in css.split(".hitl-status")[1][:600]

    def test_outbox_retry_button_is_dom_injected(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "btn.addEventListener('click', function() {" in src
        assert "<button class=\"btn btn-sm btn-primary\" onclick=" not in src

    def test_remembered_thread_preferred_in_recovery(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "var _lastInterruptedThreadId = '';" in src
        assert "var candidates = [_lastInterruptedThreadId, chatSessionId || ''];" in src
        assert "_lastInterruptedThreadId = String(data.thread_id);" in src

    def test_importmap_covers_all_modules(self):
        base = (_UI / "templates" / "base.html").read_text(encoding="utf-8")
        for mod in (Path(_UI / "static" / "js" / "modules")).glob("*.js"):
            assert f'"/static/js/modules/{mod.name}"' in base, f"importmap missing {mod.name}"

    def test_header_title_from_context(self):
        header = (_UI / "templates" / "components" / "header.html").read_text(encoding="utf-8")
        assert "page_title|default(active_page" in header
        # the inert block is gone from the rendered title element
        title_line = [ln for ln in header.split("\n") if "header-title" in ln]
        assert title_line and "{% block" not in title_line[0]

    def test_has_live_sse_contract_documented(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "CONTRACT: `activeStream` is assigned ONLY by the two turn-owning" in src

    def test_fragile_root_selector_gone(self):
        comp = (_UI / "static" / "js" / "modules" / "components.js").read_text(encoding="utf-8")
        assert "querySelector('[x-data*=\"kazmaApp\"]')" not in comp

    def test_prompt_always_renders_input(self):
        """Browser-reproduced 2026-08-26: the Rename dialog opened with NO
        textbox — promptAsync gated the input row on `opts.placeholder`, and
        session rename passes {title, label, defaultValue} only. A prompt
        must always show its input (placeholder falls back to label/message)."""
        stores = (_UI / "static" / "js" / "modules" / "stores.js").read_text(encoding="utf-8")
        assert "input: opts.placeholder || opts.label || opts.message || ' '," in stores

    def test_ws_connect_sends_resume_cursor(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        assert "?last_seq=" in src
        assert "KazmaDeliveryCursor" in src

    def test_store_handles_structured_resumed_frame(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        assert "type === 'resumed'" in src
        assert "resume-gap" in src  # gap → resync
        assert "seq-gap" in src     # seq gap → resync

    def test_worker_backed_liveness_ticker(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        assert "_startLivenessTicker" in src
        assert "new Worker(" in src
        assert "_livenessCheck" in src

    def test_streaming_js_tracks_last_event_id(self):
        src = _STREAM_JS.read_text(encoding="utf-8")
        assert "id: " in src
        assert "lastEventId" in src

    def test_sse_fallback_retries_with_cursor(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "last_event_id" in src
        assert "_dispatchSse" in src

    def test_cursor_module_loaded_before_store(self):
        html = _CHAT_HTML.read_text(encoding="utf-8")
        cursor_idx = html.find('src="/static/js/modules/delivery_cursor.js')
        store_idx = html.find('src="/static/js/stores/agentStore.js')
        assert cursor_idx != -1, "delivery_cursor.js not included in chat.html"
        assert store_idx != -1
        assert cursor_idx < store_idx, "cursor module must load before agentStore"

    def test_apply_final_paints_unconditionally(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "Server truth \u2192 DOM. ALWAYS." in src


class TestHiddenTabUX:
    """Plan P4: hidden-tab awareness (title badge + terminal notification)."""

    def test_visibility_module_exists_and_wired(self):
        assert _VIS_JS.exists()
        html = _CHAT_HTML.read_text(encoding="utf-8")
        assert 'src="/static/js/modules/turn_visibility.js' in html

    def test_store_reports_activity_and_terminal(self):
        src = _STORE_JS.read_text(encoding="utf-8")
        assert "KazmaTurnVisibility" in src
        assert "noteActivity" in src
        assert "endTurn(" in src


class TestResyncFragmentationFixes:
    """2026-08-27 post-restart incident: one reply painted as 3 bubbles.

    A focus/visibility resync during a live turn ABORTED the healthy SSE
    stream and forced a journal-cursor reopen; the replay's terminal paints
    closed the bubble mid-stream, so every later token opened a NEW bubble
    with its own "Writing reply…" row.
    """

    def test_idle_resync_paints_pending_assistant_content(self):
        """Detached persist can leave pending=true with the full answer.
        Idle resync must still applyFinal (the old !pending gate hid it)."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "leftover `pending` flag" in src or "leftover pending" in src
        idle = src.split("// Idle: paint durable assistant text", 1)[1].split(
            "Idle with nothing to deliver", 1
        )[0]
        assert "applyFinalAssistantText" in idle
        assert "!lastMsg.pending" not in idle

    def test_resync_never_aborts_a_live_stream(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        generating = src.split("if (generating) {", 1)[1].split("return;\n      }", 1)[0]
        # The generating branch must early-return while a stream is live…
        assert "if (activeStream) {" in generating
        assert "NEVER abort it here" in generating
        # …and must NOT contain the old abort-then-skip-reopen pattern.
        assert "activeStream.abort(); } catch (e)" not in generating
        assert "wasLive" not in generating

    def test_resync_paint_not_terminal_while_stream_live(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        final_fn = src.split("applyFinalAssistantText: function", 1)[1]
        assert "(opts.replay || opts.source === 'resync') && !activeStream" in final_fn


class TestServerSidePingPin:
    """Plan KD-7: server-side WS protocol pings are the death certificate for
    black-holed sockets. Without them the WS handler never unwinds on a dead
    connection, the broker keeps a zombie registration, and orphan-TTL
    reaping is suppressed. The 20s/20s values must be PINNED in every launch
    surface — not left resting on an upstream library default."""

    def test_programmatic_launcher_pins_pings(self):
        src = (_UI / "app.py").read_text(encoding="utf-8")
        assert "ws_ping_interval=20" in src
        assert "ws_ping_timeout=20" in src

    def test_cli_serve_pins_pings(self):
        src = (_ROOT / "kazma-cli" / "kazma_cli" / "main.py").read_text(encoding="utf-8")
        assert "ws_ping_interval=20" in src
        assert "ws_ping_timeout=20" in src

    def test_docker_cmd_pins_pings(self):
        dockerfile = _ROOT / "Dockerfile"
        if not dockerfile.exists():
            import pytest

            pytest.skip("no Dockerfile at repo root")
        src = dockerfile.read_text(encoding="utf-8")
        assert "--ws-ping-interval" in src
        assert "--ws-ping-timeout" in src


class TestTurnNotifySettingsToggle:
    """Plan P4 follow-up: operator-visible toggle for task-completion
    notifications (ConfigStore key + Settings UI + live client gate)."""

    def test_server_endpoint_exists(self):
        src = (_UI / "settings.py").read_text(encoding="utf-8")
        assert "/api/notifications/turn-complete" in src
        assert "notifications.turn_complete" in src

    def test_visibility_module_consults_operator_gate(self):
        src = _VIS_JS.read_text(encoding="utf-8")
        assert "/api/notifications/turn-complete" in src
        assert "_serverEnabled" in src

    def test_settings_ui_and_save_wired(self):
        html = _CHAT_HTML.with_name("settings.html").read_text(encoding="utf-8")
        assert "settings.turn_notify_title" in html
        assert "saveTurnNotify()" in html
        js = (_UI / "static" / "js" / "settings_agent.js").read_text(encoding="utf-8")
        assert "notifications.turn_complete" in js
        assert "kazma.notifyOnComplete" in js  # instant mirror to open tabs

    def test_i18n_keys_present(self):
        src = module_source(_UI / "i18n.py")
        for key in ("settings.turn_notify_title", "settings.turn_notify_label",
                    "settings.turn_notify_hint", "settings.turn_notify_save"):
            assert f'"{key}"' in src


class TestWebPushP5:
    """Plan P5: Web Push for Memory-Saver-DISCARDED tabs.

    Everything lazy-imports pywebpush — absent dependency ⇒ every entry
    point is a cheap no-op (prometheus_client degradation contract).
    """

    def test_push_module_exists_and_degrades(self):
        src = (_UI / "push.py").read_text(encoding="utf-8")
        assert "from pywebpush import webpush" in src
        assert "notifications.push.subscriptions" in src
        # Degradation contract: availability check gates everything.
        assert "def push_available" in src

    def test_broker_terminal_hook_wired(self):
        src = (_UI / "delivery.py").read_text(encoding="utf-8")
        assert "notify_push_turn_complete" in src
        assert '"turn_complete", "done"' in src

    def test_push_routes_and_sw_scope(self):
        src = (_UI / "settings.py").read_text(encoding="utf-8")
        for route in ("/api/push/vapid-public-key", "/api/push/subscribe",
                      "/api/push/unsubscribe"):
            assert route in src, f"missing route {route}"
        assert '@router.get("/sw.js")' in src  # ROOT scope — not /static/sw.js
        sw = (_UI / "static" / "sw.js").read_text(encoding="utf-8")
        assert "showNotification" in sw

    def test_client_module_registered_in_chat_page(self):
        html = _CHAT_HTML.read_text(encoding="utf-8")
        assert "push_client.js" in html

    def test_pyproject_optional_extra(self):
        import tomllib

        data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        push = (data.get("project", {}).get("optional-dependencies", {}) or {}).get("push")
        assert push and any("pywebpush" in p for p in push)


class TestCursorTrackerNode:
    """Run the pure tracker logic under Node (no browser needed)."""

    def test_gap_dupe_ok_semantics(self):
        if shutil.which("node") is None:
            import pytest

            pytest.skip("node not available")
        harness = (
            "global.window = { localStorage: { _m:{}, getItem(k){return this._m[k]??null;}, "
            "setItem(k,v){this._m[k]=String(v);} } };\n"
            "const fs = require('fs');\n"
            "eval(fs.readFileSync(process.argv[2], 'utf8'));\n"
            "const K = window.KazmaDeliveryCursor;\n"
            "const t = K.createTracker();\n"
            "const out = [];\n"
            "out.push(t.observeSeq(5));   // init\n"
            "out.push(t.observeSeq(6));   // ok\n"
            "out.push(t.observeSeq(6));   // dupe\n"
            "out.push(t.observeSeq(9));   // gap\n"
            "out.push(t.last());          // stays 6 - never advances past a hole\n"
            "const t2 = K.createTracker();\n"
            "t2.seed(10);                 // seed past a replay window\n"
            "out.push(t2.last());         // 10\n"
            "out.push(t2.observeSeq(8));  // dupe (<= seeded head)\n"
            "out.push(t2.observeSeq(11)); // ok - real gaps after head still fire\n"
            "out.push(K.loadPersisted('nope')); // 0\n"
            "K.persist('s1', 42);\n"
            "out.push(K.loadPersisted('s1'));   // 42\n"
            "console.log(JSON.stringify(out));\n"
        )
        harness_path = _ROOT / ".tmp_cursor_harness.js"
        try:
            harness_path.write_text(harness, encoding="utf-8")
            proc = subprocess.run(
                ["node", str(harness_path), str(_CURSOR_JS)],
                capture_output=True, text=True, timeout=30,
            )
            assert proc.returncode == 0, proc.stderr
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            assert out == ["init", "ok", "dupe", "gap", 6, 10, "dupe", "ok", 0, 42]
        finally:
            harness_path.unlink(missing_ok=True)


class TestPlanFencePresentation:
    """2026-08-27 transcript artifact (the requested PlanFencePresentation
    suite): persisted replies showed ```plan block text + 'Let me…'
    preamble + ':Core stats' glued mid-line.

    Fix shape: the client now post-processes RENDERED html through the
    pure ``transformRenderedForPlan`` (chat.js, near
    stripPlanFenceForDisplay): plan-ish code blocks become a COLLAPSED
    <details class="kazma-plan"> widget, duplicates collapse to ONE
    details, and bare trailing text after </details> gets an explicit <p>
    block boundary. Applied at every paint site; idempotent under the
    repeated innerHTML swaps streaming drives. Behavioral assertions live
    in tests/js/test_plan_render.js (run under Node below).
    """

    _PLAN_JS = _ROOT / "tests" / "js" / "test_plan_render.js"

    def test_transform_exists_near_stripper(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function transformRenderedForPlan(" in src
        assert "function stripPlanFenceForDisplay(" in src
        # The transform must sit with the other paint helpers (same region)
        at_strip = src.index("function stripPlanFenceForDisplay(")
        at_tf = src.index("function transformRenderedForPlan(")
        assert abs(at_tf - at_strip) < 8000

    def test_every_paint_site_applies_the_transform(self):
        """One render function, and paints are idempotent.

        Reply text used to be turned into HTML at four sites with three
        different expressions — ``appendLiveToken`` and the voice hook both
        skipped ``_scrubDsml``, so scaffolding showed while streaming and
        disappeared on the terminal paint. Worse, the terminal paint
        assigned ``innerHTML`` unconditionally, rebuilding the whole message
        subtree even when server truth matched what was already on screen:
        the visible "blink" at the end of every reply.
        """
        src = _CHAT_JS.read_text(encoding="utf-8")

        # A single canonical text -> HTML pipeline, applied everywhere.
        assert "function _renderReplyHTML(text)" in src
        assert (
            "transformRenderedForPlan(\n      KS.markdown(_scrubDsml(stripPlanFenceForDisplay(text)))\n    )"
            in src
        )
        assert src.count("_renderReplyHTML(tokenAccum)") >= 4

        # Paints go through the idempotent helper, never a bare assignment.
        assert "function _paintHTML(textEl, html)" in src
        # Compared against the source string we wrote, not the browser's
        # re-serialization of the DOM — innerHTML round-trips lossily, so an
        # innerHTML comparison never matches and repaints every time.
        assert "if (textEl._kzPaintedHTML === html) return false;" in src
        assert "textEl._kzPaintedHTML = html;" in src
        assert (
            "textEl.innerHTML = transformRenderedForPlan(KS.markdown(stripPlanFenceForDisplay(tokenAccum)))"
            not in src
        ), "raw unconditional paint reintroduced — this is the end-of-reply flash"

        # Server truth still always wins when it actually differs.
        final_fn = src.split("applyFinalAssistantText: function", 1)[1]
        assert "_paintHTML(textEl, _renderReplyHTML(tokenAccum));" in final_fn
        # textContent fallback still uses the raw stripped text.
        assert "textEl.textContent = display;" in final_fn

    def test_fence_splitter_tolerates_space_variant(self):
        """'``` plan' (space between fence and tag) defeated BOTH the text
        stripper and tryIngestPlanFromText — plan text then glued into
        prose/code in the persisted transcript."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        # both fence branches (closed + open) must accept the space variant
        assert src.count("```[ \\t]*plan\\b") >= 2

    def test_details_widget_markers(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert '<details class="kazma-plan"><summary>Plan</summary>' in src
        assert '<div class="kazma-plan-body"><pre>' in src

    def test_behavior_harness_under_node(self):
        if shutil.which("node") is None:
            import pytest

            pytest.skip("node not available")
        proc = subprocess.run(
            ["node", str(self._PLAN_JS)],
            capture_output=True, text=True, timeout=60,
            cwd=str(_ROOT),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "FAIL" not in proc.stdout


class TestWorkbenchCollapseTiming:
    """When the CoT panel collapses — and when it must NOT.

    Collapsing removes a few hundred pixels from above the reply. Doing that
    in ``finalizeProgress`` put a large visual change on the terminal frame,
    which the user saw as the CoT "flashing" as streaming stopped (reported
    right after that change shipped). The transcript still ends up as
    one-line summaries; the collapse just happens at the START of the next
    turn, where the view is already moving to the new user message.
    """

    def test_finalize_does_not_collapse(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        fin = src.split("function finalizeProgress(", 1)[1].split("\n  function ", 1)[0]
        assert "classList.add('is-collapsed')" not in fin, (
            "collapsing at the terminal frame is the end-of-turn flash"
        )
        assert "panel.classList.remove('is-collapsed');" in fin

    def test_next_turn_collapses_finished_panels(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function _collapseFinishedWorkbenches()" in src
        begin = src.split("function beginTurn(opts)", 1)[1].split("\n  function ", 1)[0]
        assert "_collapseFinishedWorkbenches();" in begin, (
            "finished panels must be tidied at the start of the next turn"
        )
        fn = src.split("function _collapseFinishedWorkbenches()", 1)[1].split("\n  function ", 1)[0]
        assert ".agent-progress.is-done" in fn
        assert "aria-expanded" in fn


class TestHitlResumeKeepsWorkbench:
    """Approving a permission card RESUMES a turn — it does not start one.

    ``beginTurn()`` wipes the workbench for a fresh turn. submitApproval
    called it unqualified, so clicking Approve deleted the panel holding
    every step that produced the approval card, leaving a lone "Thinking…"
    row above the answer. And the approve stream rendered its tool activity
    as inline boxes rather than workbench rows, so nothing that happened
    after the approval reached the CoT either.
    """

    def test_begin_turn_takes_a_resume_flag(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        assert "function beginTurn(opts)" in src
        begin = src.split("function beginTurn(opts)", 1)[1].split("\n  function ", 1)[0]
        assert "var resume = !!(opts && opts.resume);" in begin
        # The panel wipe + counter reset must be inside the non-resume branch.
        assert "if (!resume) {" in begin
        wipe_at = begin.index("oldProg.remove()")
        guard_at = begin.index("if (!resume) {")
        assert guard_at < wipe_at, "the workbench wipe must be guarded by !resume"

    def test_approval_paths_resume_instead_of_restarting(self):
        src = _CHAT_JS.read_text(encoding="utf-8")
        # security card + semantic-choice card both resume the open turn
        assert src.count("beginTurn({ resume: true })") >= 2
        approve = src.split("function submitApproval(action, scope)", 1)[1][:2000]
        assert "beginTurn({ resume: true })" in approve
        assert "beginTurn();" not in approve

    def test_approve_stream_logs_tools_into_the_workbench(self):
        """Parity with the main stream: tool activity is workbench rows."""
        src = _CHAT_JS.read_text(encoding="utf-8")
        approve = src.split("function submitApproval(action, scope)", 1)[1]
        approve = approve[: approve.index("onError: function")]
        assert approve.count("logProgress({") >= 2, "tool call + tool result"
        # the divergent inline rendering is gone (the swarm badge stays: it
        # describes work outliving the turn). Match the CODE, not the comment
        # that explains why it was removed.
        assert "box.className = 'tool-call-box'" not in approve
        assert "resultBox.className = 'tool-result-box'" not in approve
        assert "swarm-bg-badge" in approve


class TestComposerFooterRemoved:
    """The "Enter to send" row under the composer is gone.

    It reserved a full line under the input for a shortcut hint the mobile
    stylesheet had already been hiding. The composer char count moved into
    the always-present session-metrics group, so removing the row costs
    nothing and the transcript gains the space.
    """

    _CHAT_HTML = _UI / "templates" / "chat.html"

    def test_hint_and_footer_are_gone_from_the_template(self):
        src = self._CHAT_HTML.read_text(encoding="utf-8")
        assert 'class="input-footer"' not in src
        assert "chat-input-footer-hint" not in src
        assert "send_shortcut" not in src

    def test_char_count_moved_into_session_metrics(self):
        src = self._CHAT_HTML.read_text(encoding="utf-8")
        metrics = src.split('class="capacity-group session-metrics"', 1)[1]
        metrics = metrics[: metrics.index("</div>")]
        assert 'id="composer-chars"' in metrics

    def test_dead_footer_css_removed(self):
        css = (_UI / "static" / "css" / "kazma.css").read_text(encoding="utf-8")
        for sel in (
            ".input-footer {",
            ".chat-input-footer-hint {",
            ".chat-input-footer-hint kbd {",
            '[dir="rtl"] .chat-input-area .input-footer',
        ):
            assert sel not in css, f"dead rule left behind: {sel}"
