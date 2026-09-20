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
   * How many gates are still asking, per the SERVER's view.
   *
   * `facts.gateViews` is the canonical list the page already holds
   * (`gate_view.py` -> `_serverGateViews`). It is used in preference to the
   * part stamps for exactly the reason the renderer is: a part can still
   * read `pending` while the registry has recorded the decision, and a
   * header that counts stamps would announce "1 awaiting your decision"
   * next to a card reading Approved (2026-09-19, live install).
   */
  function pendingGates(doc, facts) {
    var views = (facts && facts.gateViews) || null;
    if (Array.isArray(views) && views.length) {
      var live = 0;
      for (var i = 0; i < views.length; i++) {
        var v = views[i];
        if (v && (v.interactive || String(v.state || '') === 'pending')) live++;
      }
      return live;
    }
    // No authoritative view yet. Fall back to the document's own stamps —
    // an honest "we have not been told" rather than a confident zero.
    var parts = (doc && doc.parts) || [];
    var n = 0;
    for (var j = 0; j < parts.length; j++) {
      if (parts[j] && parts[j].type === 'hitl'
          && String(parts[j].state || 'pending') === 'pending') n++;
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
  function connectionOf(facts) {
    facts = facts || {};
    if (facts.streamLive) return 'live';
    if (facts.reconnecting) return 'reconnecting';
    if (facts.serverGenerating || facts.stopRequested) return 'reconnecting';
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
    counts.pending = pendingGates(doc, facts);
    var terminal = isTerminal(phase);
    return {
      turnId: String((doc && doc.turnId) || ''),
      phase: phase,
      terminal: terminal,
      connection: connectionOf(facts),
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
    elapsedOf: elapsedOf,
    countParts: countParts,
    pendingGates: pendingGates,
    isTerminal: isTerminal,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
