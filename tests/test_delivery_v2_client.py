"""Turn Delivery V2 plan P3 — client cutover source contracts.

Locks the deletion of the incident-patch mechanisms and the presence of the
V2 recovery architecture in the shipped JS. The governing rule
(docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md): every fix removes the
root-cause class; these tests fail if any heuristic patch mechanism returns.

Also runs the pure cursor tracker (modules/delivery_cursor.js) under Node to
unit-test gap/dupe semantics without a browser.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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
        src = (_UI / "i18n.py").read_text(encoding="utf-8")
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
