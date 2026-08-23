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
            assert out == ["init", "ok", "dupe", "gap", 6, 0, 42]
        finally:
            harness_path.unlink(missing_ok=True)
