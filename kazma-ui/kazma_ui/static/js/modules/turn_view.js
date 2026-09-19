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

          [workbench] [settled gates…] [answer] [pending gates…] [chrome]

      A settled gate sits ABOVE the answer (the decision precedes the reply
      it unblocked — what _parkClaimedHitlCard used to achieve by moving
      nodes). A gate still being asked sits BELOW the text so far (the
      question follows the content that provoked it — what _placeHitlCard
      used to achieve with compareDocumentPosition). One rule, declaratively,
      instead of two imperative movers fighting over the same children.

   3. SLOTS ARE FLAT SIBLINGS. Every doc-derived node is a direct child of
      .message-content — required by CSS anyway
      (.message-assistant > .message-content > .message-text), and it makes
      "the card trapped the answer inside the CoT panel" UNREPRESENTABLE.
      There is no nesting for a node to be trapped in, so _rescueTurnDom has
      nothing left to rescue.

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

  function isPendingGate(part) {
    return String((part && part.state) || 'pending') === 'pending';
  }

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
   */
  function slotPlan(doc, has, TD) {
    has = has || {};
    var parts = (doc && doc.parts) || [];
    var i, p, key;

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

    var settled = [];
    var pending = [];
    for (i = 0; i < order.length; i++) {
      key = order[i];
      p = byKey[key];
      (isPendingGate(p) ? pending : settled).push({ key: key, kind: 'hitl', part: p });
    }

    var plan = [];
    if (has.workbench) plan.push({ key: 'workbench', kind: 'workbench' });
    for (i = 0; i < settled.length; i++) plan.push(settled[i]);
    if (has.text) plan.push({ key: 'text', kind: 'text' });
    for (i = 0; i < pending.length; i++) plan.push(pending[i]);
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
        else if (n.classList.contains('agent-progress')) key = 'workbench';
        else if (n.classList.contains('hitl-approval-card')) {
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
     *   discard(key, el, ctx)  → bool             (default false — KEEP)
     *   has(kind, doc, ctx)    → bool             (veto an empty slot)
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

      var plan = slotPlan(doc, { workbench: can('workbench'), text: can('text') }, TD);

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
        var gnode = slots[gk];
        if (!gnode || gnode.parentNode !== content) {
          issues.push('gate-missing:' + gk);
        }
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
    isPendingGate: isPendingGate,
    SLOT_ATTR: SLOT_ATTR,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
