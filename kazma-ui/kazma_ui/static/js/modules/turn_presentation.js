/* ═══════════════════════════════════════════════════════
   Kazma TurnPresentation — the derived header model

   docs/plans/UNIFIED_TURN_BLOCK.md §5 asks for one place that turns
   "what the server says" into "what the header shows", and §7 is explicit
   that it must be a MAPPING and not a second state machine:

       "These are presentation states mapped from server facts; do not add
        a second execution state machine in the browser."

   So this module is a pure function. It has no timers, no storage, no DOM
   and no memory: given a TurnDocument and the server facts the page
   already holds, it returns what the header should read. Every incident in
   this class came from a surface that computed its own answer — the live
   task card ran its own phase machine on its own clock, which is how the
   operator got "Done 0s" while the graph was still working, and
   "Approved — running…" under a finished reply.

   ── What it deliberately does NOT do ───────────────────
   * It never decides that a turn is over. `close_turn` is the only closer
     (AGENTS.md §31A). A client-side wall clock that marks a turn complete
     is the `forceEndTurn` defect and is forbidden.
   * It never decides that a gate is authorized or expired. The registry
     does (AGENTS.md §30). Timer expiry and transport loss are reported as
     themselves, never as an outcome (invariant U11).
   * It never invents elapsed time. `elapsedS` comes from the server's own
     stamp; `elapsedAtMs` says WHEN that stamp was taken so the caller can
     tick a display forward without the number drifting into a claim.

   Pure logic, DOM-free. Runs under Node.
   ═══════════════════════════════════════════════════════ */

(function (root) {
  'use strict';

  /** Phases, in the order §7 of the plan lists them. */
  var PHASE = {
    QUEUED: 'queued',
    WORKING: 'working',
    APPROVAL: 'approval',
    RESUMING: 'resuming',
    STOPPING: 'stopping',
    COMPLETED: 'completed',
    FAILED: 'failed',
    CANCELLED: 'cancelled',
    RECOVERING: 'recovering',
    INTERRUPTED: 'interrupted',
  };

  /** A phase the turn cannot leave on its own. */
  var TERMINAL = {
    completed: 1, failed: 1, cancelled: 1, interrupted: 1,
  };

  function isTerminal(phase) {
    return !!TERMINAL[String(phase || '')];
  }

  function countParts(doc, TD) {
    var parts = (doc && doc.parts) || [];
    var out = { tools: 0, steps: 0, thoughts: 0, gates: 0, pending: 0 };
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (!p || typeof p !== 'object') continue;
      if (p.type === 'tool') { out.tools++; out.steps++; }
      else if (p.type === 'status') out.steps++;
      else if (p.type === 'reasoning') out.thoughts++;
      else if (p.type === 'hitl') {
        out.gates++;
        out.steps++;
      }
    }
    return out;
  }

  /**
   * The gates the approval group will actually SHOW, with their states.
   *
   * This applies the same omission rule as `turn_view.js:slotPlan`: a
   * pending gate with no resolved view yet is left out, because minting a
   * row for it would mean ghost Approve buttons for a gate the registry
   * has not confirmed.
   *
   * It has to be the same rule, or the two surfaces contradict each other
   * on screen. Observed in the browser mid-resume: the header read "4
   * approvals" (counting parts) while the group beneath it read "3
   * requests" (counting rendered rows). One fact, two answers — the class
   * this whole plan exists to remove, reproduced in the fix for it.
   *
   * `facts.gateState` is the page's one resolver (`_hitlDisplayState`).
   * Without it — pure tests, or a page that has not joined the registry
   * yet — `facts.gateViews` is consulted, then the part's own stamp, which
   * is the honest "nothing better to consult" answer rather than a
   * confident zero.
   */
  function gateRows(doc, facts) {
    facts = facts || {};
    var parts = (doc && doc.parts) || [];
    var resolve = typeof facts.gateState === 'function' ? facts.gateState : null;
    var views = Array.isArray(facts.gateViews) ? facts.gateViews : null;
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (!p || p.type !== 'hitl') continue;
      var state = null;
      if (resolve) {
        try { state = resolve(p); } catch (e) { state = null; }
      } else if (views) {
        var iid = String(p.interrupt_id
          || (p.payload && p.payload.interrupt_id) || '');
        for (var v = 0; v < views.length; v++) {
          var view = views[v];
          if (!view) continue;
          var vid = String(view.interrupt_id || view.gate_id || '');
          if (vid && vid === iid) {
            state = view.interactive ? 'pending' : String(view.state || '');
            break;
          }
        }
      }
      var stamp = String(p.state || '');
      if (state == null || state === '' || state === 'omit') {
        // Same as slotPlan: a pending gate with no view stays omitted, a
        // settled one keeps its row.
        if (!stamp || stamp === 'pending') continue;
        state = stamp;
      }
      out.push({ part: p, state: String(state) });
    }
    return out;
  }

  /** How many gates are still asking, per the resolver the renderer uses. */
  function pendingGates(doc, facts) {
    var rows = gateRows(doc, facts);
    var n = 0;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].state === 'pending') n++;
    }
    return n;
  }

  /**
   * The presentation phase.
   *
   * Reads, in order of authority: an explicit server lifecycle, the
   * document's status, then the page's own request state. A request state
   * (`stopRequested`) can only ever produce "stopping" — never
   * "cancelled", because only the server can say the cancellation took
   * (plan §7: "Await server acknowledgement; do not prematurely mark
   * cancelled").
   */
  function phaseOf(doc, facts) {
    facts = facts || {};
    var status = String((doc && doc.status) || '');

    if (facts.recovering) return PHASE.RECOVERING;
    if (status === 'error' || facts.failed) return PHASE.FAILED;
    if (facts.cancelled) return PHASE.CANCELLED;
    if (facts.interrupted) return PHASE.INTERRUPTED;

    var awaiting = pendingGates(doc, facts) > 0;
    if (awaiting) return PHASE.APPROVAL;

    if (facts.stopRequested && status !== 'done') return PHASE.STOPPING;
    if (facts.resuming) return PHASE.RESUMING;

    if (status === 'done') return PHASE.COMPLETED;
    if (status === 'paused') {
      // Paused with no pending gate is not "approval required": the gate
      // settled and the graph has not reported back yet.
      return facts.serverGenerating ? PHASE.RESUMING : PHASE.WORKING;
    }
    if (facts.serverGenerating || status === 'streaming') {
      // Nothing produced yet — say Queued rather than Working, which is
      // what the reader can actually tell apart.
      var counts = countParts(doc, null);
      var hasText = false;
      var parts = (doc && doc.parts) || [];
      for (var i = 0; i < parts.length; i++) {
        if (parts[i] && parts[i].type === 'text'
            && String(parts[i].text || '').trim()) { hasText = true; break; }
      }
      if (!hasText && !counts.steps && !String((doc && doc.stream) || '').trim()) {
        return PHASE.QUEUED;
      }
      return PHASE.WORKING;
    }
    return PHASE.QUEUED;
  }

  /**
   * Connection state, reported SEPARATELY from execution.
   *
   * Plan §3: "A disconnected indicator describes the connection separately
   * from execution. Disconnection is not completion or failure." The two
   * used to be one field, which is how a dropped socket read as a finished
   * turn.
   */
  /** No frame for this long during a live turn is worth saying out loud.
   *  Server heartbeats land every ~8-10s, so silence past this is the
   *  journal having gone quiet rather than the model thinking. */
  var STALL_MS = 20000;

  function connectionOf(facts) {
    facts = facts || {};
    var running = !!(facts.serverGenerating || facts.stopRequested);
    var silentMs = Number(facts.lastSignalAgoMs);
    // Silence is reported as silence, with its duration, and recovery is
    // the reconciler's job (plan §11: "Recovery is bounded with backoff").
    // The old bar ran its OWN retry budget and latched on "not responding"
    // — a second recovery loop next to the one that was already running,
    // and the only thing it added was a label.
    if (running && isFinite(silentMs) && silentMs >= STALL_MS) {
      return 'stalled';
    }
    if (facts.streamLive) return 'live';
    if (facts.reconnecting) return 'reconnecting';
    if (running) return 'reconnecting';
    return 'idle';
  }

  /**
   * Elapsed seconds, as the SERVER last reported them, plus when that was.
   *
   * The caller may tick a display forward from `elapsedAtMs`; it may not
   * treat the result as a fact about the turn. Plan §3: "A local timer may
   * update elapsed display. It cannot mark a gate expired or a turn
   * complete." Never restarts on reconnect, because it is not the client's
   * clock to restart.
   */
  function elapsedOf(doc) {
    var secs = Number((doc && doc.elapsedS) || 0);
    if (!isFinite(secs) || secs < 0) secs = 0;
    var at = Number((doc && doc.elapsedAtMs) || 0);
    return { seconds: secs, stampedAtMs: isFinite(at) ? at : 0 };
  }

  /**
   * Everything the header renders, derived once.
   *
   * `facts` are the page's own: streamLive, serverGenerating, stopRequested,
   * resuming, cancelled, failed, interrupted, recovering, reconnecting,
   * gateViews, retrySupported.
   */
  function header(doc, facts) {
    facts = facts || {};
    var phase = phaseOf(doc, facts);
    var counts = countParts(doc, null);
    // Gates are counted as the group renders them, not as the document
    // stamps them, so the header and the group can never disagree.
    var rows = gateRows(doc, facts);
    counts.gates = rows.length;
    counts.pending = 0;
    for (var g = 0; g < rows.length; g++) {
      if (rows[g].state === 'pending') counts.pending++;
    }
    var terminal = isTerminal(phase);
    return {
      turnId: String((doc && doc.turnId) || ''),
      phase: phase,
      terminal: terminal,
      // A finished turn has no connection to report. The page's own facts
      // lag the terminal frame until its next status read, and a completed
      // header read "Completed · Reconnecting…" in that gap (live,
      // 2026-09-26).
      connection: terminal ? 'idle' : connectionOf(facts),
      // How long the journal has been quiet, so the header can SAY it
      // rather than just colour itself amber.
      silentMs: (function () {
        var ms = Number(facts.lastSignalAgoMs);
        return (isFinite(ms) && ms > 0) ? ms : 0;
      })(),
      elapsed: elapsedOf(doc),
      counts: counts,
      model: String((doc && doc.model) || ''),
      // Stop is offered while the server could still act on it. Offering
      // it on a finished turn is a button that lies.
      canStop: !terminal && (
        phase === PHASE.WORKING || phase === PHASE.QUEUED
        || phase === PHASE.RESUMING || phase === PHASE.APPROVAL
      ),
      canRetry: !!facts.retrySupported && (
        phase === PHASE.FAILED || phase === PHASE.INTERRUPTED
        || phase === PHASE.CANCELLED
      ),
      // The approval group owns the decision UI; the header only says a
      // decision is outstanding and how many.
      awaiting: counts.pending,
    };
  }

  root.KazmaTurnPresentation = {
    PHASE: PHASE,
    header: header,
    phaseOf: phaseOf,
    connectionOf: connectionOf,
    STALL_MS: STALL_MS,
    elapsedOf: elapsedOf,
    countParts: countParts,
    pendingGates: pendingGates,
    gateRows: gateRows,
    isTerminal: isTerminal,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
