/* ═══════════════════════════════════════════════════════
   Kazma TurnView — the RENDER half of Turn Delivery V2 (KD-4)

   docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md shipped the delivery
   half: journal, monotonic seq, cursor resume, snapshot resync. KD-4 also
   specified the other half —

       "Client paints from state, not events. […] one render() applies
        state→DOM idempotently."

   — and that half was never built. renderTurn kept ASKING THE DOM where to
   paint: querySelector by turn id, fall back to currentMsgEl, fall back to
   "last assistant bubble after the last user row", then scan
   nextElementSibling to guess whether that bubble was historical, then
   _rescueTurnDom to lift nodes back out of a collapsed CoT panel, then
   _hitlCardIsTrapped to work out whether an approval card had swallowed the
   text host. Every one of those is a heuristic over DOM SHAPE, so every
   change to the markup re-broke one of them — the "reply never replaces the
   HITL placeholder" class, fixed ~15 times between 2026-09-01 and -09-19,
   each time through a different path.

   This module is the missing authority. It owns the child list of one
   assistant bubble's .message-content, and that child list is a pure
   function of the TurnDocument. Nothing else may insert, move or remove a
   doc-derived node.

   ── Contract ───────────────────────────────────────────
   1. IDENTITY IS SHARED WITH THE DOCUMENT. Slot keys come from
      KazmaTurnDocument.partKey, the same function the document dedupes
      with. "Which node is this part" therefore cannot drift from "which
      part is this" — they are the same string.

   2. ORDER IS DECLARED, NOT PATCHED:

          [header] [activity] [approvals] [answer] [chrome]

      This is the docs/plans/UNIFIED_TURN_BLOCK.md §3 layout. It replaces

          [workbench] [settled gates…] [answer] [pending gates…]

      where a gate's POSITION encoded its state: settled above the answer,
      still-asking below it. That rule was a real improvement over the two
      imperative movers it replaced (_parkClaimedHitlCard and
      _placeHitlCard fighting over the same children), but it had a cost
      the movers also had — deciding a gate MOVED the answer, because the
      gate crossed it. And four requests in one turn read as four
      unrelated cards scattered around the text.

      Now every gate is a row inside ONE approvals region, in ask order,
      and the region sits above the answer for the whole turn. Settling a
      gate changes its label, not its place, and nothing moves the answer.

   3. THE FOUR REGIONS ARE FLAT SIBLINGS. header, activity, approvals and
      answer are direct children of .message-content — required by CSS
      anyway (.message-assistant > .message-content > .message-text), and
      it makes "the card trapped the answer inside the CoT panel"
      UNREPRESENTABLE: the answer is never a descendant of a region that
      can be collapsed. Approval ROWS are keyed inside the approvals
      region by its own renderer, which is the one nesting there is, and
      it is bounded to a region that is never collapsed away.

   4. AMBIGUITY NEVER DELETES. A slot is removed only when the caller's
      `discard` explicitly returns true; the default is to keep it. Keeping
      a stale node shows something wrong and is reportable. Removing a live
      one shows NOTHING — and silence is the bug class this module exists to
      end. Always fail toward visible.

   5. THE RENDER REPORTS ON ITSELF. Every pass re-derives what should be on
      screen and compares it to what is. A document with an answer and no
      painted answer raises `text-missing` through onInvariant instead of
      waiting for the operator to notice the silence.

   Idempotent by construction: rendering the same document twice performs
   zero DOM mutations (locked by tests/js/test_turn_view.js).
   Pure DOM, no globals, injectable `document` — runs under Node.
   ═══════════════════════════════════════════════════════ */

(function (root) {
  'use strict';

  var SLOT_ATTR = 'data-slot-key';

  /** Chrome is bubble furniture, not turn content: never a slot, always last. */
  var CHROME_CLASSES = ['message-meta', 'message-actions'];

  function directChildByClass(parent, cls) {
    if (!parent || !parent.children) return null;
    for (var i = 0; i < parent.children.length; i++) {
      var c = parent.children[i];
      if (c.classList && c.classList.contains(cls)) return c;
    }
    return null;
  }

  function bubbleContent(el) {
    if (!el) return null;
    if (el.classList && el.classList.contains('message-content')) return el;
    return directChildByClass(el, 'message-content');
  }

  function interruptIdOf(part, TD) {
    if (TD && typeof TD.interruptIdOf === 'function') return TD.interruptIdOf(part);
    if (!part || typeof part !== 'object') return '';
    if (part.interrupt_id) return String(part.interrupt_id);
    if (part.payload && part.payload.interrupt_id) return String(part.payload.interrupt_id);
    return '';
  }

  function gateSlotKey(part, TD) {
    if (TD && typeof TD.partKey === 'function') return TD.partKey(part);
    return 'hitl:' + interruptIdOf(part, TD);
  }

  // There is deliberately no `isPendingGate(part)` helper. It answered "is
  // this gate pending" from the raw part stamp, which is the second opinion
  // that got a card sorted as pending while it was painted as approved
  // (2026-09-19). Ordering asks the host's resolver, via slotPlan's
  // `gateState`, and nothing else may ask anything else.

  function textOf(doc, TD) {
    if (TD && typeof TD.textOf === 'function') {
      var t = TD.textOf((doc && doc.parts) || []);
      if (String(t || '').trim()) return String(t).trim();
    }
    return '';
  }

  /**
   * The child list this document asks for, in order.
   *
   * Pure: no DOM, no element lookups. `has.workbench` / `has.text` let the
   * caller veto a slot it has nothing to put in (a turn with no activity
   * rows must not mint an empty panel).
   *
   * `gateState` resolves a gate part to the state the UI should treat it
   * as. It is REQUIRED to be the same function the painter labels with.
   *
   * Sorting and painting used to answer "what state is this gate in"
   * separately: the plan read `part.state`, the painter called the host's
   * display resolver. When those disagreed — a part still stamped `pending`
   * while the registry said the decision was claimed — the card was sorted
   * as pending (below the answer) and painted as approved. The operator saw
   * "Approved — running…" sitting underneath the finished reply, and after a
   * refresh "Waiting for approval" under a delete that had already run
   * (2026-09-19, live install).
   *
   * That is the same defect this module was written to remove, one level in:
   * two sources of truth for one fact. So the resolver is threaded through,
   * and the resolved state rides on the entry — the painter is handed the
   * answer rather than invited to compute its own.
   */
  function slotPlan(doc, has, TD, gateState) {
    has = has || {};
    var parts = (doc && doc.parts) || [];
    var i, p, key;
    var resolve = typeof gateState === 'function'
      ? gateState
      : function (part) { return String((part && part.state) || 'pending'); };

    // Collapse to one entry per gate, in ask order. The document already
    // guarantees this (partKey), so a duplicate here means a malformed doc —
    // take the later stamp rather than rendering the gate twice.
    var order = [];
    var byKey = {};
    for (i = 0; i < parts.length; i++) {
      p = parts[i];
      if (!p || p.type !== 'hitl') continue;
      key = gateSlotKey(p, TD);
      if (byKey[key] === undefined) order.push(key);
      byKey[key] = p;
    }

    // Rows in ASK order, whatever state they are in. Ordering by state is
    // what used to move the answer: a settled gate sat above the text and
    // a pending one below it, so approving the first gate relocated the
    // reply. Ask order is stable, and settling a gate now changes its
    // label rather than its place (plan §3).
    var rows = [];
    for (i = 0; i < order.length; i++) {
      key = order[i];
      p = byKey[key];
      var shownRaw = resolve(p);
      // null / omit: no live view yet. A PENDING gate stays omitted
      // (honest empty). A CLAIMED gate in the document must keep its
      // row — skipping it left the DOM card unplanned (keptTail) under
      // the answer (2026-09-20 sequential).
      if (shownRaw == null || shownRaw === '' || shownRaw === 'omit') {
        var partState = String((p && p.state) || '');
        if (!partState || partState === 'pending') continue;
        shownRaw = partState;
      }
      var shown = String(shownRaw);
      // `state` is the resolved answer, carried so the painter uses THIS
      // value rather than resolving again and possibly differently.
      rows.push({ key: key, kind: 'hitl', part: p, state: shown });
    }

    var plan = [];
    // The header is FIRST and, once a turn exists, unconditional: plan §3,
    // "Exists from the first acknowledged turn state, including before the
    // first token." A header that only appears once there is content is
    // the gap the separate bottom bar was invented to fill.
    if (has.header) plan.push({ key: 'header', kind: 'header' });
    if (has.workbench) plan.push({ key: 'workbench', kind: 'workbench' });
    // ── ONE approval region, above the answer ──────────────────────────
    //
    // This replaces one top-level slot per gate. Four requests were four
    // independent cards stacked around the text — settled ones above it,
    // pending ones below — so approving the first MOVED the answer
    // between containers, and a reader could not tell four requests in
    // one turn from four turns.
    //
    // docs/plans/UNIFIED_TURN_BLOCK.md §3: "Zero groups when there are no
    // gates; exactly one when at least one exists", "One row per actual
    // gate ID", "Approval group location stays stable above the answer.
    // Rows update in request order without moving the answer between
    // containers."
    //
    // Rows stay in ASK order regardless of state, which is what makes the
    // position stable: settling a gate changes its label, not its place.
    // The old plan sorted by state, so every decision reshuffled the
    // transcript.
    //
    // Row identity is still partKey — the same string the document
    // dedupes with — so the renderer inside the region keys off exactly
    // what the projector keys off (contract 1).
    if (rows.length) plan.push({ key: 'approvals', kind: 'approvals', rows: rows });
    if (has.text) plan.push({ key: 'text', kind: 'text' });
    return plan;
  }

  function create(opts) {
    opts = opts || {};
    var doc$ = opts.document
      || (typeof document !== 'undefined' ? document : null);
    var TD = opts.turnDocument
      || (root && root.KazmaTurnDocument)
      || null;
    var onInvariant = typeof opts.onInvariant === 'function'
      ? opts.onInvariant
      : function () {};

    /** turnId → bubble element. The registry REPLACES querySelector as the
     *  way to find a turn's bubble. A DOM query can match a bubble the
     *  layout happens to have left behind; this map only ever holds what we
     *  ourselves bound. */
    var byTurn = {};
    /** bubble element → { key → element }. Kept on the element itself so a
     *  removed bubble takes its slot table with it (no leak, no stale map
     *  entry pointing at a detached node). */
    var SLOTS = '__kzSlots';

    function slotsOf(el) {
      if (!el) return null;
      if (!el[SLOTS]) el[SLOTS] = {};
      return el[SLOTS];
    }

    // ── Registry ──────────────────────────────────────────

    function bind(turnId, el) {
      var id = String(turnId || '');
      if (!id || !el) return el;
      byTurn[id] = el;
      try { el.setAttribute('data-turn-id', id); } catch (e) { /* ignore */ }
      return el;
    }

    function elFor(turnId) {
      var id = String(turnId || '');
      if (!id) return null;
      var el = byTurn[id];
      if (!el) return null;
      // A bubble removed by a session switch or an edit/retry must not be
      // painted into off-screen. isConnected is a property of the NODE, not
      // a guess about layout — the one DOM question that is always honest.
      if (el.isConnected === false) {
        delete byTurn[id];
        return null;
      }
      return el;
    }

    /**
     * Rename a turn without touching the DOM.
     *
     * Most frames arrive before the server has stamped a real turn id, so
     * the client opens under the placeholder 'live'. Matching bubbles on
     * that placeholder made any bubble still carrying data-turn-id="live" a
     * magnet for the NEXT turn's tokens (2026-09-03: the same reply painted
     * both above and below the new user message). With a registry the
     * placeholder is just a map key, and promotion is a rename.
     */
    function promote(fromId, toId) {
      var from = String(fromId || '');
      var to = String(toId || '');
      if (!from || !to || from === to) return elFor(to);
      var el = byTurn[from];
      if (!el) return elFor(to);
      delete byTurn[from];
      // Never clobber a DIFFERENT bubble already registered under the real
      // id — that would be two turns claiming one node.
      if (byTurn[to] && byTurn[to] !== el) return byTurn[to];
      return bind(to, el);
    }

    /** Session switch. There is deliberately no per-turn `release` and no
     *  `forget(el)`: a bubble torn out of the transcript is dropped lazily
     *  by `elFor`'s isConnected check, and a second way to evict would be a
     *  second thing to keep in sync. */
    function releaseAll() {
      byTurn = {};
    }

    // ── Adoption ──────────────────────────────────────────

    /**
     * Claim known markup this module did not create.
     *
     * appendMessage builds .message-text; history hydration arrives with
     * approval cards and a workbench already in place; legacy paths may
     * still insert a progress panel mid-turn. Scanning the direct children
     * for nodes the slot table does not yet own is NOT the DOM archaeology
     * this module exists to delete: archaeology was *guessing intent* from
     * shape ("is this bubble historical? is that card trapped?"). This is a
     * declared handover by exact class, bounded to a handful of children,
     * and it runs every pass so a stray insert is ADOPTED rather than
     * duplicated — the reconciler converges on any starting DOM.
     */
    function adopt(el) {
      var content = bubbleContent(el);
      if (!content) return null;
      var slots = slotsOf(el);
      var kids = content.children;
      for (var i = 0; i < kids.length; i++) {
        var n = kids[i];
        if (!n.classList) continue;
        var key = '';
        if (n.classList.contains('message-text')) key = 'text';
        else if (n.classList.contains('turn-header')) key = 'header';
        else if (n.classList.contains('agent-progress')) key = 'workbench';
        else if (n.classList.contains('turn-approvals')) key = 'approvals';
        else if (n.classList.contains('hitl-approval-card')) {
          // A LOOSE card, from history hydration or a transcript written
          // before the approvals region existed. It is adopted under its
          // own gate key so it is visible and reportable rather than
          // silently duplicated beside the row the region will build —
          // and `discard` is what decides whether it goes. Contract 4:
          // this module never deletes on its own initiative.
          var iid = '';
          try { iid = String(n.getAttribute('data-interrupt-id') || ''); } catch (e) { iid = ''; }
          key = 'hitl:' + iid;
        }
        if (!key) continue;
        // A slot already pointing at THIS node, or at another node still in
        // the host, is left alone. One pointing at a node that has since
        // been torn out loses to the node actually on screen — what the
        // reader can see wins over what the table remembers.
        var held = slots[key];
        if (held === n) continue;
        if (held && held.parentNode === content) continue;
        slots[key] = n;
        try { n.setAttribute(SLOT_ATTR, key); } catch (e2) { /* ignore */ }
      }
      return content;
    }

    // ── Render ────────────────────────────────────────────

    /**
     * Apply `doc` to `el`'s children. Idempotent.
     *
     * `renderers`:
     *   build(entry, ctx)      → element | null   (null: nothing to show)
     *   paint(entry, el, ctx)                     (update in place)
     *   rebuild(entry, el, ctx)→ bool             (true: this node cannot
     *                                              represent entry.state;
     *                                              tear it out and build())
     *   discard(key, el, ctx)  → bool             (default false — KEEP)
     *   has(kind, doc, ctx)    → bool             (veto an empty slot;
     *                                              'header' is asked too,
     *                                              and answering false
     *                                              leaves a turn with no
     *                                              status line at all)
     *   gateState(part)        → string           (ONE answer for a gate's
     *                                              state, used for BOTH
     *                                              ordering and labelling —
     *                                              see slotPlan)
     *
     * A hitl entry carries `state`: the resolved value. paint() AND build()
     * must use `entry.state`, never re-resolve — that is the whole point.
     *
     * rebuild is the reversible half of hydration's 'awaiting' posture. A
     * pending part is painted disabled while the registry is unknown, so
     * refresh cannot mint ghost Approve buttons. When the registry later
     * says the gate is live, paint-in-place cannot restore those buttons
     * (awaiting replaced the actions HTML). rebuild returns true, the slot
     * is torn out, and build() mints a real live card. That is not contract
     * 4 (ambiguity never deletes): the renderer is stating a known fact
     * about THIS node, not guessing.
     */
    function render(el, doc, renderers, meta) {
      renderers = renderers || {};
      meta = meta || {};
      // A turn is never painted into the operator's own row. The old
      // painter checked this because its fallback chain could genuinely
      // land on a user bubble ("last message element"); here it is a
      // structural impossibility the caller would have to work at, so the
      // guard is a cheap assertion rather than load-bearing logic. Lifting
      // nodes out of a You bubble is what emptied it after send
      // (2026-09-01).
      if (el && el.classList && el.classList.contains('message-user')) {
        return { painted: false, reason: 'user-bubble' };
      }
      var content = adopt(el);
      if (!content) return { painted: false, reason: 'no-content-host' };
      var slots = slotsOf(el);
      var turnId = String((doc && doc.turnId) || '');
      var ctx = { doc: doc, meta: meta, turnId: turnId, content: content, view: api };

      function can(kind) {
        if (typeof renderers.has !== 'function') return true;
        try { return !!renderers.has(kind, doc, ctx); } catch (e) { return false; }
      }

      var plan = slotPlan(
        doc,
        { header: can('header'), workbench: can('workbench'), text: can('text') },
        TD, renderers.gateState
      );

      // 1. Materialise every planned slot.
      var ordered = [];
      var planned = {};
      var i, entry, node;
      for (i = 0; i < plan.length; i++) {
        entry = plan[i];
        node = slots[entry.key] || null;
        if (node && node.isConnected === false && node.parentNode == null) {
          // Someone else tore it out. Rebuild rather than re-inserting a
          // node whose listeners may have been dropped with it.
          node = null;
          delete slots[entry.key];
        }
        if (node && typeof renderers.rebuild === 'function') {
          var mustRebuild = false;
          try { mustRebuild = !!renderers.rebuild(entry, node, ctx); } catch (eRb) {
            mustRebuild = false;
          }
          if (mustRebuild) {
            try { if (node.parentNode) node.parentNode.removeChild(node); } catch (eRm) { /* ignore */ }
            node = null;
            delete slots[entry.key];
          }
        }
        if (!node && typeof renderers.build === 'function') {
          try { node = renderers.build(entry, ctx) || null; } catch (eB) { node = null; }
        }
        if (!node) continue;          // renderer declined — not an error
        slots[entry.key] = node;
        try { node.setAttribute(SLOT_ATTR, entry.key); } catch (eA) { /* ignore */ }
        if (typeof renderers.paint === 'function') {
          try { renderers.paint(entry, node, ctx); } catch (eP) { /* keep going */ }
        }
        planned[entry.key] = true;
        ordered.push(node);
      }

      // 2. Slots the document no longer asks for. Default: KEEP (contract 4).
      var keptTail = [];
      var key;
      for (key in slots) {
        if (!Object.prototype.hasOwnProperty.call(slots, key)) continue;
        if (planned[key]) continue;
        node = slots[key];
        if (!node) { delete slots[key]; continue; }
        var drop = false;
        if (typeof renderers.discard === 'function') {
          try { drop = !!renderers.discard(key, node, ctx); } catch (eD) { drop = false; }
        }
        if (drop) {
          try { if (node.parentNode) node.parentNode.removeChild(node); } catch (eR) { /* ignore */ }
          delete slots[key];
        } else if (node.parentNode === content) {
          keptTail.push(node);
        }
      }
      // Kept-but-unplanned nodes hold their relative on-screen order, after
      // the planned block. They are visible and reportable, never silent.
      // Read that order off the host in ONE pass rather than sorting with a
      // comparator that re-walks the children for every comparison.
      if (keptTail.length) {
        var kids = content.children;
        for (var z = 0; z < kids.length; z++) {
          if (keptTail.indexOf(kids[z]) >= 0) ordered.push(kids[z]);
        }
      }

      // 3. One ordering pass. insertBefore on a node already in place is a
      //    no-op in the DOM, but we compare first so the pass performs zero
      //    mutations when nothing moved (idempotence is testable).
      var moves = 0;
      var cursor = null;
      for (i = 0; i < ordered.length; i++) {
        node = ordered[i];
        var want = cursor ? cursor.nextElementSibling : content.firstElementChild;
        if (node !== want) {
          try {
            content.insertBefore(node, want || null);
            moves++;
          } catch (eI) { /* a foreign parent — leave it alone */ }
        }
        cursor = node;
      }

      // 4. Chrome last, always — but only touched when it is NOT already
      //    the tail. Appending each chrome node unconditionally "settles"
      //    on the right order while mutating the DOM on EVERY pass, which
      //    would quietly cost idempotence: two writes per render, forever,
      //    and no way for a test to prove the renderer had stopped.
      var tail = [];
      for (i = 0; i < CHROME_CLASSES.length; i++) {
        var chrome = directChildByClass(content, CHROME_CLASSES[i]);
        if (chrome) tail.push(chrome);
      }
      if (tail.length) {
        var kids = content.children;
        var settled = kids.length >= tail.length;
        for (i = 0; settled && i < tail.length; i++) {
          if (kids[kids.length - tail.length + i] !== tail[i]) settled = false;
        }
        if (!settled) {
          for (i = 0; i < tail.length; i++) {
            try { content.appendChild(tail[i]); moves++; } catch (eC) { /* ignore */ }
          }
        }
      }

      var report = {
        painted: true,
        turnId: turnId,
        slots: ordered.length,
        moves: moves,
        plan: plan.map(function (e) { return e.key; }),
      };
      verify(el, content, doc, slots, report, ctx);
      return report;
    }

    /**
     * Re-derive what should be on screen and compare it to what is.
     *
     * This is the loud-failure half of the fix. The old architecture had no
     * way to notice it had gone silent: the operator noticed. Every render
     * now answers "is the answer actually on screen?" and says so when it
     * is not, so a regression surfaces as a diagnostic instead of as a
     * person waiting at a blank bubble.
     */
    function verify(el, content, doc, slots, report, ctx) {
      var want = textOf(doc, TD);
      var issues = [];
      if (want) {
        var textEl = slots['text'];
        if (!textEl || textEl.parentNode !== content) {
          issues.push('text-missing');
        } else {
          var shown = '';
          try { shown = String(textEl.getAttribute('data-md') || ''); } catch (e) { shown = ''; }
          if (!shown) {
            try { shown = String(textEl.textContent || ''); } catch (e2) { shown = ''; }
          }
          if (!String(shown).trim()) issues.push('text-blank');
        }
      }
      var parts = (doc && doc.parts) || [];
      var group = slots['approvals'];
      var groupUp = !!(group && group.parentNode === content);
      for (var i = 0; i < parts.length; i++) {
        if (!parts[i] || parts[i].type !== 'hitl') continue;
        // Only a gate that COULD have produced a card counts. A part with no
        // payload carries no question to render — it still shows up as an
        // activity row, so it is not silent, and flagging it would make the
        // invariant cry wolf on every legacy transcript. An invariant that
        // fires on states nobody can fix gets muted, and then it is worth
        // nothing.
        if (!parts[i].payload) continue;
        var gk = gateSlotKey(parts[i], TD);
        // The row lives INSIDE the approvals region now, so the question
        // is "is it on screen", not "is it a direct child". A loose card
        // adopted from history still counts as on screen: the reader can
        // see it, which is what the invariant is about.
        var gnode = slots[gk];
        var rowUp = false;
        if (groupUp) {
          try {
            rowUp = !!group.querySelector('[data-gate-key="' + gk + '"]');
          } catch (eQ) { rowUp = false; }
        }
        if (!rowUp && (!gnode || gnode.parentNode !== content)) {
          issues.push('gate-missing:' + gk);
        }
      }
      // ── U02: one of each region, per turn ──────────────────────
      //
      // A duplicate is invisible to every other check: the answer is
      // present, the gate has a row, nothing is missing. It just says
      // everything twice. Counting is the only way to see it, and the
      // count is cheap because these are direct children.
      var UNIQUE = ['turn-header', 'turn-approvals', 'agent-progress', 'message-text'];
      for (var u = 0; u < UNIQUE.length; u++) {
        var seen = 0;
        var kids2 = content.children;
        for (var k2 = 0; k2 < kids2.length; k2++) {
          if (kids2[k2].classList && kids2[k2].classList.contains(UNIQUE[u])) seen++;
        }
        if (seen > 1) issues.push('duplicate-region:' + UNIQUE[u] + ':' + seen);
      }

      // A gate row must not ALSO be loose beside the answer. One card in
      // two places is the "approved twice" screenshot.
      if (slots['approvals']) {
        var loose = 0;
        var kids3 = content.children;
        for (var k3 = 0; k3 < kids3.length; k3++) {
          if (kids3[k3].classList
              && kids3[k3].classList.contains('hitl-approval-card')) loose++;
        }
        if (loose) issues.push('gate-outside-group:' + loose);
      }

      if (!issues.length) return;
      report.issues = issues;
      try {
        onInvariant({
          code: issues[0].split(':')[0],
          issues: issues,
          turnId: report.turnId,
          status: (doc && doc.status) || '',
          wantLen: want.length,
          source: (ctx && ctx.meta && ctx.meta.source) || '',
        });
      } catch (e) { /* a reporter must never break a render */ }
    }

    function slot(el, key) {
      var s = el && el[SLOTS];
      return (s && s[String(key)]) || null;
    }

    function stats() {
      var ids = [];
      for (var k in byTurn) {
        if (Object.prototype.hasOwnProperty.call(byTurn, k)) ids.push(k);
      }
      return { turns: ids.length, ids: ids };
    }

    var api = {
      bind: bind,
      elFor: elFor,
      promote: promote,
      releaseAll: releaseAll,
      adopt: adopt,
      render: render,
      slot: slot,
      stats: stats,
      document: doc$,
    };
    return api;
  }

  root.KazmaTurnView = {
    create: create,
    slotPlan: slotPlan,
    bubbleContent: bubbleContent,
    gateSlotKey: gateSlotKey,
    SLOT_ATTR: SLOT_ATTR,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
