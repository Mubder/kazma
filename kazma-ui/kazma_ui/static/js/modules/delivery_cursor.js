/* ═══════════════════════════════════════════════════════
   Kazma Delivery Cursor — Turn Delivery V2 client bookkeeping
   Pure logic, DOM-free. Tracks the per-session event cursor
   (monotonic server ``seq``), detects gaps, persists for
   reconnect-resume (?last_seq= / last_event_id).
   ═══════════════════════════════════════════════════════ */

window.KazmaDeliveryCursor = (function() {
  'use strict';

  var LS_PREFIX = 'kazma.deliveryCursor.';

  function loadPersisted(sessionKey) {
    try {
      var raw = window.localStorage.getItem(LS_PREFIX + sessionKey);
      if (raw == null) return 0;
      var n = parseInt(raw, 10);
      return isNaN(n) || n < 0 ? 0 : n;
    } catch (e) { return 0; }
  }

  function persist(sessionKey, seq) {
    try {
      window.localStorage.setItem(LS_PREFIX + sessionKey, String(seq));
    } catch (e) { /* private mode / quota — resume just falls behind */ }
  }

  /**
   * Create a tracker for one live connection.
   * observeSeq(seq) → 'init' | 'ok' | 'dupe' | 'gap'
   *   init: first seq observed on this connection
   *   ok:   exactly last + 1
   *   dupe: ≤ last (replayed frame already applied)
   *   gap:  > last + 1 — frames were missed; caller must resync
   */
  function createTracker() {
    var last = null;
    return {
      observeSeq: function(seq) {
        if (seq == null) return 'dupe';
        var n = parseInt(seq, 10);
        if (isNaN(n) || n < 0) return 'dupe';
        if (last === null) {
          // First frame of a connection. If the server replayed from our
          // persisted cursor this is contiguous; a fresh page simply starts
          // wherever the stream starts (loadSession covers history).
          last = n;
          return 'init';
        }
        if (n === last + 1) { last = n; return 'ok'; }
        if (n <= last) return 'dupe';
        // n > last + 1 — do NOT advance past the hole; the resync/replay
        // will re-deliver the missing range.
        return 'gap';
      },
      last: function() { return last; },
      reset: function() { last = null; },
      /**
       * Seed past a replay window. The server's resumed handshake reports
       * the journal head (`to`); replay may legitimately SKIP frames
       * (command confirmations are not resumable), so the client must treat
       * everything <= head as already-accounted-for instead of reading the
       * skips as gaps. Live frames after head still detect real gaps.
       */
      seed: function(seq) {
        var n = parseInt(seq, 10);
        if (isNaN(n) || n < 0) return;
        if (last === null || n > last) {
          last = n;
        }
      },
    };
  }

  return {
    loadPersisted: loadPersisted,
    persist: persist,
    createTracker: createTracker,
  };
})();
