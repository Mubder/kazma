/* ═══════════════════════════════════════════════════════
   Kazma TurnDocument — parts projector (P1/P2 + C)
   Pure logic, DOM-free. A turn is reasoning / tool / status / hitl / text.
   content + activity are derived. Works in the browser and under Node.
   applyEvent is the only mutator; seq-deduped so SSE + WS can both feed it.
   ═══════════════════════════════════════════════════════ */
(function (root) {
  'use strict';

  function textOf(parts) {
    var text = '';
    if (!Array.isArray(parts)) return text;
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p && p.type === 'text' && String(p.text || '').trim()) {
        text = String(p.text).trim();
      }
    }
    return text;
  }

  /**
   * Activity rows for the workbench.
   *
   * `gateState` is the SAME resolver the renderer orders and labels gates
   * with. Without it this function was a third independent answer to "what
   * state is this gate in" — it read the raw part stamp, so the workbench
   * could print "Waiting for approval" next to a card reading "Approved".
   * Callers that have no resolver (hydration, legacy messages) get the
   * part's own stamp, which is the right answer when there is nothing
   * better to consult.
   */
  function activityOf(parts, gateState) {
    var rows = [];
    if (!Array.isArray(parts)) return rows;
    var resolveGate = typeof gateState === 'function'
      ? gateState
      : function (part) { return String((part && part.state) || 'pending'); };
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (!p || typeof p !== 'object') continue;
      var kind = String(p.type || '');
      if (kind === 'reasoning' && String(p.text || '').trim()) {
        rows.push({
          kind: 'thought',
          title: 'Working notes',
          detail: String(p.text),
          state: 'done',
        });
      } else if (kind === 'tool') {
        rows.push({
          kind: 'tool',
          title: String(p.name || p.title || 'tool'),
          detail: String(p.result || p.detail || p.args || ''),
          state: String(p.state || 'done'),
          ts: p.ts || null,
        });
      } else if (kind === 'status' && String(p.title || '').trim()) {
        rows.push({
          kind: 'status',
          title: String(p.title),
          state: String(p.state || 'done'),
          ts: p.ts || null,
        });
      } else if (kind === 'hitl') {
        var hs = String(resolveGate(p) || 'pending');
        var htitle = 'Waiting for approval';
        if (hs === 'awaiting') htitle = 'Waiting for approval';
        else if (hs === 'approved' || hs === 'inflight') htitle = 'Approved';
        else if (hs === 'denied') htitle = 'Denied';
        else if (hs === 'timeout' || hs === 'error' || hs === 'settled' || hs === 'done') {
          htitle = 'Approval resolved';
        }
        rows.push({
          kind: 'status',
          title: htitle,
          detail: String(p.tool || p.detail || ''),
          state: 'info',
          ts: p.ts || null,
        });
      }
    }
    return rows;
  }

  function activityForMessage(msg) {
    if (!msg || typeof msg !== 'object') return [];
    if (Array.isArray(msg.activity) && msg.activity.length) return msg.activity;
    return activityOf(msg.parts);
  }

  function splitStreamAndFinal(streamed, finalText) {
    var streamedS = String(streamed || '').trim();
    var finalS = String(finalText || '').trim();
    if (!streamedS && !finalS) return { reasoning: '', text: '' };
    if (!streamedS) return { reasoning: '', text: finalS };
    if (!finalS) return { reasoning: '', text: streamedS };
    if (streamedS === finalS) return { reasoning: '', text: finalS };
    var probe = streamedS.length > 80 ? streamedS.slice(0, 80) : streamedS;
    var fProbe = finalS.length > 80 ? finalS.slice(0, 80) : finalS;
    if (finalS.indexOf(probe) === 0 || streamedS.indexOf(fProbe) === 0) {
      return { reasoning: '', text: finalS.length >= streamedS.length ? finalS : streamedS };
    }
    return { reasoning: streamedS, text: finalS };
  }

  function partKey(part) {
    var kind = String((part && part.type) || '');
    if (kind === 'text') return 'text';
    if (kind === 'reasoning') return 'reasoning:' + String(part.text || '').slice(0, 240);
    if (kind === 'tool') {
      return 'tool:' + String(part.name || part.title || '') + ':' +
        String(part.state || '') + ':' +
        String(part.result || part.detail || '').slice(0, 80);
    }
    if (kind === 'status') return 'status:' + String(part.title || '');
    // One slot PER GATE, keyed by interrupt id — NOT one slot per turn.
    // A turn can pause more than once (sequential "Allow this tool" clicks).
    // Collapsing every gate into a single 'hitl' slot meant the second gate
    // overwrote the first, so the document held one decision while the
    // transcript showed two cards — and the renderer had no authority left
    // to reconcile against. Mirrors turn_document.py::_part_key.
    if (kind === 'hitl') return 'hitl:' + interruptIdOf(part);
    return kind + ':' + JSON.stringify(part).slice(0, 80);
  }

  var HITL_RANK = {
    pending: 0,
    approved: 1,
    denied: 1,
    inflight: 2,
    settled: 3,
    done: 3,
    timeout: 3,
    error: 3,
  };

  function hitlRank(state) {
    var s = String(state || 'pending').toLowerCase();
    return Object.prototype.hasOwnProperty.call(HITL_RANK, s) ? HITL_RANK[s] : 0;
  }

  function interruptIdOf(part) {
    if (!part || typeof part !== 'object') return '';
    if (part.interrupt_id) return String(part.interrupt_id);
    if (part.payload && part.payload.interrupt_id) return String(part.payload.interrupt_id);
    return '';
  }

  function hitlToolOf(part) {
    if (!part || typeof part !== 'object') return '';
    var t = String(part.tool || part.tool_name || '').trim();
    if (t) return t;
    if (part.payload && typeof part.payload === 'object') {
      return String(part.payload.tool || part.payload.tool_name || '').trim();
    }
    return '';
  }

  /** Every gate in this turn, in the order they were asked. */
  function hitlPartsOf(parts) {
    var out = [];
    if (!Array.isArray(parts)) return out;
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] && parts[i].type === 'hitl') out.push(parts[i]);
    }
    return out;
  }

  /** The gate still waiting on the operator, else the most recent one.
   *  Gates normally settle in order, but a replayed stamp must not let a
   *  resolved gate mask one that is still holding the graph. */
  function hitlPartOf(parts) {
    var all = hitlPartsOf(parts);
    var found = null;
    for (var i = 0; i < all.length; i++) {
      if (String(all[i].state || 'pending') === 'pending') return all[i];
      found = all[i];
    }
    return found;
  }

  /** Merge one gate's part with a newer stamp of the SAME gate.
   *  Gates with ids never reach each other here — partKey gives each its own
   *  slot. The id/tool mismatch branches are the fallback for id-less legacy
   *  frames, which all share the empty-id slot. */
  function mergeHitlPart(existing, incoming) {
    if (!incoming || typeof incoming !== 'object') {
      return existing && typeof existing === 'object' ? existing : {};
    }
    if (!existing || typeof existing !== 'object' || existing.type !== 'hitl') {
      var fresh = {};
      var fk;
      for (fk in incoming) {
        if (Object.prototype.hasOwnProperty.call(incoming, fk)) fresh[fk] = incoming[fk];
      }
      fresh.type = 'hitl';
      return fresh;
    }
    var oldId = interruptIdOf(existing);
    var newId = interruptIdOf(incoming);
    if (oldId && newId && oldId !== newId) {
      var nextGate = {};
      var nk;
      for (nk in incoming) {
        if (Object.prototype.hasOwnProperty.call(incoming, nk)) nextGate[nk] = incoming[nk];
      }
      nextGate.type = 'hitl';
      return nextGate;
    }
    var oldTool = hitlToolOf(existing);
    var newTool = hitlToolOf(incoming);
    if (oldTool && newTool && oldTool !== newTool) {
      var toolGate = {};
      var tk;
      for (tk in incoming) {
        if (Object.prototype.hasOwnProperty.call(incoming, tk)) toolGate[tk] = incoming[tk];
      }
      toolGate.type = 'hitl';
      return toolGate;
    }
    if (hitlRank(incoming.state) < hitlRank(existing.state)) return existing;
    var out = {};
    var ek;
    for (ek in existing) {
      if (Object.prototype.hasOwnProperty.call(existing, ek)) out[ek] = existing[ek];
    }
    var ik;
    for (ik in incoming) {
      if (Object.prototype.hasOwnProperty.call(incoming, ik)) out[ik] = incoming[ik];
    }
    out.type = 'hitl';
    var incPayload = incoming.payload;
    var oldPayload = existing.payload;
    if (!incPayload && oldPayload) out.payload = oldPayload;
    else if (incPayload && oldPayload && typeof incPayload === 'object' && typeof oldPayload === 'object') {
      var mp = {};
      var pk;
      for (pk in oldPayload) {
        if (Object.prototype.hasOwnProperty.call(oldPayload, pk)) mp[pk] = oldPayload[pk];
      }
      for (pk in incPayload) {
        if (Object.prototype.hasOwnProperty.call(incPayload, pk)) mp[pk] = incPayload[pk];
      }
      out.payload = mp;
    }
    var iid = newId || oldId;
    if (iid) {
      out.interrupt_id = iid;
      if (out.payload && typeof out.payload === 'object' && !out.payload.interrupt_id) {
        var pp = {};
        for (pk in out.payload) {
          if (Object.prototype.hasOwnProperty.call(out.payload, pk)) pp[pk] = out.payload[pk];
        }
        pp.interrupt_id = iid;
        out.payload = pp;
      }
    }
    return out;
  }

  function activityToParts(activity) {
    var out = [];
    if (!Array.isArray(activity)) return out;
    for (var i = 0; i < activity.length; i++) {
      var row = activity[i];
      if (!row || typeof row !== 'object') continue;
      var kind = String(row.kind || '');
      if (kind === 'tool') {
        out.push({
          type: 'tool',
          name: String(row.title || 'tool'),
          result: String(row.detail || ''),
          state: String(row.state || 'done'),
        });
      } else if (kind === 'thought') {
        var detail = String(row.detail || '');
        if (detail.trim()) out.push({ type: 'reasoning', text: detail });
      } else if (kind === 'status' || kind === 'info') {
        var title = String(row.title || '').trim();
        if (title) out.push({ type: 'status', title: title, state: String(row.state || 'done') });
      }
    }
    return out;
  }

  function mergeParts(existing, incoming) {
    existing = (existing || []).filter(function (p) { return p && typeof p === 'object'; });
    incoming = (incoming || []).filter(function (p) { return p && typeof p === 'object'; });
    var oldText = textOf(existing);
    var newText = textOf(incoming);
    var out = [];
    var seen = {};

    function add(part, replace) {
      if (part.type === 'text') return;
      var key = partKey(part);
      if (seen[key]) {
        if (replace && part.type === 'hitl') {
          for (var ri = 0; ri < out.length; ri++) {
            if (partKey(out[ri]) === key) { out[ri] = mergeHitlPart(out[ri], part); return; }
          }
        }
        return;
      }
      seen[key] = 1;
      out.push(part);
    }
    for (var i = 0; i < existing.length; i++) add(existing[i]);
    for (var j = 0; j < incoming.length; j++) add(incoming[j], true);

    if (
      oldText && newText && oldText.trim() !== newText.trim() &&
      newText.indexOf(oldText.length > 80 ? oldText.slice(0, 80) : oldText) !== 0
    ) {
      var rkey = partKey({ type: 'reasoning', text: oldText });
      if (!seen[rkey]) {
        out.unshift({ type: 'reasoning', text: oldText });
        seen[rkey] = 1;
      }
    }
    var chosen = newText || oldText;
    if (chosen) out.push({ type: 'text', text: chosen });
    return out;
  }

  function partsFromStream(streamed, finalText, activity) {
    var split = splitStreamAndFinal(streamed, finalText);
    var incoming = [];
    if (split.reasoning) incoming.push({ type: 'reasoning', text: split.reasoning });
    incoming = incoming.concat(activityToParts(activity));
    if (split.text) incoming.push({ type: 'text', text: split.text });
    return mergeParts([], incoming);
  }

  function empty(turnId) {
    return {
      turnId: String(turnId || ''),
      seq: 0,
      seen: {},
      status: 'streaming',
      model: '',
      stream: '',
      parts: [],
    };
  }

  function eventKey(ev) {
    if (ev && ev.seq != null && ev.seq !== '' && Number(ev.seq) > 0) {
      return 'seq:' + String(ev.seq);
    }
    var t = String((ev && ev.type) || '');
    if (t === 'hitl' || t === 'approval_required' || t === 'approval_needed'
        || t === 'paused_for_approval') {
      var iid = String((ev && (ev.interrupt_id
        || (ev.payload && ev.payload.interrupt_id))) || '');
      var hs = String((ev && ev.state) || '');
      return 'hitl:' + iid + ':' + hs;
    }
    var step = (ev && ev.step) || {};
    var c = String((ev && (ev.content || ev.reply || step.title || '')) || '');
    var st = String(step.state || ev.state || '');
    var d = String(step.detail || ev.detail || '').slice(0, 40);
    return 'h:' + t + ':' + c.length + ':' + c.slice(0, 48) + ':' + st + ':' + d;
  }

  function replaceToolPart(parts, incoming) {
    var name = String((incoming && (incoming.name || incoming.title)) || '');
    var next = (parts || []).slice();
    var found = -1;
    for (var i = next.length - 1; i >= 0; i--) {
      if (next[i] && next[i].type === 'tool'
          && String(next[i].name || next[i].title || '') === name) {
        found = i;
        break;
      }
    }
    if (found >= 0) {
      next[found] = incoming;
      return next;
    }
    return mergeParts(next, [incoming]);
  }

  function eventToParts(ev) {
    var type = String((ev && ev.type) || '');
    var step = (ev && ev.step) || {};
    if (type === 'progress' && step && step.kind) {
      return activityToParts([step]);
    }
    if (type === 'tool_start' || type === 'tool_call' || type === 'tool_lifecycle') {
      return [{
        type: 'tool',
        name: String(ev.tool_name || ev.name || step.title || 'tool'),
        result: String(ev.inputs || ev.args || ev.detail || step.detail || ''),
        state: 'running',
      }];
    }
    if (type === 'tool_result') {
      return [{
        type: 'tool',
        name: String(ev.tool_name || ev.name || 'tool'),
        result: String(ev.result || ev.detail || ''),
        state: 'done',
      }];
    }
    if (type === 'status' || type === 'status_update') {
      var title = String((ev.message || ev.title || ev.status || step.title || '')).trim();
      if (!title) return [];
      return [{ type: 'status', title: title, state: 'running' }];
    }
    return [];
  }

  function applyEvent(doc, ev) {
    if (!doc) doc = empty('');
    ev = ev || {};
    var key = eventKey(ev);
    if (doc.seen && doc.seen[key]) return doc;
    var next = {
      turnId: doc.turnId || String(ev.turn_id || ev.turnId || ''),
      seq: doc.seq || 0,
      seen: {},
      status: doc.status || 'streaming',
      model: doc.model || '',
      stream: doc.stream || '',
      parts: (doc.parts || []).slice(),
    };
    var sk;
    for (sk in (doc.seen || {})) {
      if (Object.prototype.hasOwnProperty.call(doc.seen, sk)) next.seen[sk] = 1;
    }
    next.seen[key] = 1;
    var seq = Number(ev.seq);
    if (seq > next.seq) next.seq = seq;
    if (ev.model) next.model = String(ev.model);
    if (ev.turn_id && !next.turnId) next.turnId = String(ev.turn_id);

    var type = String(ev.type || '');
    if (type === 'hydrate') {
      if (Array.isArray(ev.parts) && ev.parts.length) {
        next.parts = ev.parts;
      }
      if (ev.content) {
        next.parts = mergeParts(next.parts, [{ type: 'text', text: String(ev.content) }]);
        next.stream = String(ev.content);
      }
      if (Array.isArray(ev.activity) && ev.activity.length) {
        next.parts = mergeParts(next.parts, activityToParts(ev.activity));
      }
      // The gate that matters is the one still waiting, not the newest one:
      // a turn that paused twice ends with the SECOND gate's part even while
      // the first is what the graph is blocked on.
      var hydratedHitl = hitlPartOf(next.parts);
      var hState = hydratedHitl ? String(hydratedHitl.state || 'pending') : '';
      if (hState === 'pending' && (ev.open || ev.pending)) next.status = 'paused';
      else if ((hState === 'approved' || hState === 'denied' || hState === 'inflight')
          && (ev.open || ev.pending)) next.status = 'streaming';
      else next.status = (ev.open || ev.pending) ? 'paused' : 'done';
      return next;
    }
    if (type === 'token' || type === 'llm_delta') {
      var chunk = String(ev.content || '');
      if (ev.full) next.stream = chunk;
      else next.stream += chunk;
      next.parts = mergeParts(next.parts, partsFromStream(next.stream, next.stream));
      next.status = 'streaming';
      return next;
    }
    if (type === 'done' || type === 'turn_complete') {
      var finalText = String(ev.content || next.stream || '');
      next.parts = mergeParts(next.parts, partsFromStream(next.stream, finalText));
      next.stream = finalText || next.stream;
      next.status = ev.interrupted ? 'paused' : 'done';
      return next;
    }
    if (type === 'capacity') {
      var reply = String(ev.reply || ev.content || '');
      if (reply) {
        next.parts = mergeParts(next.parts, partsFromStream(next.stream, reply));
        next.stream = reply;
      }
      next.status = 'done';
      return next;
    }
    if (type === 'hitl' || type === 'approval_needed' || type === 'paused_for_approval'
        || type === 'approval_required') {
      var hitlState = String(ev.state || 'pending');
      var hitlPayload = ev.payload || ev;
      var iid = String(ev.interrupt_id || (hitlPayload && hitlPayload.interrupt_id) || '');
      var hitlPart = {
        type: 'hitl',
        tool: String(ev.tool || (ev.step && ev.step.title) || ''),
        state: hitlState,
        interrupt_id: iid,
        payload: hitlPayload,
      };
      // Carried only when THIS client watched the decision happen. The
      // renderer lets it outrank a stale gate-registry row; see the hydrate
      // branch, which strips it, because a part read back from the server is
      // never this tab's live decision.
      if (ev.view && typeof ev.view === 'object') hitlPart.view = ev.view;
      else if (hitlPayload && typeof hitlPayload === 'object' && hitlPayload.view
          && typeof hitlPayload.view === 'object') {
        hitlPart.view = hitlPayload.view;
      }
      next.parts = mergeParts(next.parts, [hitlPart]);
      // Any pending gate pauses the turn — not just the newest one. With one
      // part per gate, "the last hitl part" is the gate asked most recently,
      // which after a sequential approve is the SETTLED one; reading status
      // off it reported "streaming" while the graph sat blocked.
      var mergedHitl = hitlPartOf(next.parts);
      var resolved = String((mergedHitl && mergedHitl.state) || hitlState);
      if (resolved === 'pending') next.status = 'paused';
      else if (resolved === 'approved' || resolved === 'denied' || resolved === 'inflight') {
        next.status = 'streaming';
      } else {
        next.status = 'done';
      }
      return next;
    }
    var extra = eventToParts(ev);
    if (extra.length) {
      var toolish = extra.length === 1 && extra[0].type === 'tool';
      next.parts = toolish
        ? replaceToolPart(next.parts, extra[0])
        : mergeParts(next.parts, extra);
    }
    return next;
  }

  function legacyTurnId(msg) {
    msg = msg || {};
    var raw = String(msg.ts || '') + '|' + String(msg.content || '');
    var h = 0;
    for (var i = 0; i < raw.length; i++) {
      h = ((h << 5) - h + raw.charCodeAt(i)) | 0;
    }
    return 'legacy-' + (h >>> 0).toString(16);
  }

  function hydrateMessage(msg) {
    if (!msg || typeof msg !== 'object') return msg;
    if (String(msg.role || '').toLowerCase() !== 'assistant') return msg;
    var out = {};
    var k;
    for (k in msg) {
      if (Object.prototype.hasOwnProperty.call(msg, k)) out[k] = msg[k];
    }
    var parts = Array.isArray(out.parts) ? out.parts : null;
    var activity = Array.isArray(out.activity) ? out.activity : null;
    if (!parts || !parts.length) {
      parts = partsFromStream('', String(out.content || ''), activity);
      if (parts && parts.length) out.parts = parts;
    }
    if (parts && parts.length && !(activity && activity.length)) {
      var derived = activityOf(parts);
      if (derived.length) out.activity = derived;
    }
    if (!String(out.turn_id || out.turnId || '').trim()) {
      out.turn_id = legacyTurnId(out);
    }
    return out;
  }

  function fromMessage(msg) {
    msg = hydrateMessage(msg || {});
    var doc = empty(String(msg.turn_id || msg.turnId || ''));
    return applyEvent(doc, {
      type: 'hydrate',
      turn_id: doc.turnId,
      content: msg.content || '',
      parts: msg.parts,
      activity: msg.activity,
      model: msg.model || '',
      open: msg.open,
      pending: msg.pending,
    });
  }

  root.KazmaTurnDocument = {
    textOf: textOf,
    activityOf: activityOf,
    activityForMessage: activityForMessage,
    mergeParts: mergeParts,
    mergeHitlPart: mergeHitlPart,
    hitlPartsOf: hitlPartsOf,
    hitlPartOf: hitlPartOf,
    hitlRank: hitlRank,
    // partKey / interruptIdOf are the document's identity functions. The
    // renderer keys its DOM slots off the SAME functions, so "which node is
    // this part" can never drift from "which part is this".
    partKey: partKey,
    interruptIdOf: interruptIdOf,
    partsFromStream: partsFromStream,
    splitStreamAndFinal: splitStreamAndFinal,
    empty: empty,
    applyEvent: applyEvent,
    fromMessage: fromMessage,
    hydrateMessage: hydrateMessage,
    replaceToolPart: replaceToolPart,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
