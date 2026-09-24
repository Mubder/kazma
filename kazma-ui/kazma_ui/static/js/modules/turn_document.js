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
   * FNV-1a, 32 bits, over UTF-8 bytes. `Math.imul` is an exact 32-bit
   * multiply, so this is reproducible digit for digit in any language that
   * can do the same — which is the whole point: turn_document.py computes
   * the identical value. See `legacyTurnId`.
   */
  function fnv1a32(str, seed) {
    var h = (seed >>> 0) || 0x811c9dc5;
    var s = String(str == null ? '' : str);
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      // Inline UTF-8 so the byte stream matches Python's `.encode("utf-8")`
      // without depending on TextEncoder (absent in some embedded views).
      var bytes;
      if (c < 0x80) bytes = [c];
      else if (c < 0x800) bytes = [0xc0 | (c >> 6), 0x80 | (c & 0x3f)];
      else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
        var lo = s.charCodeAt(i + 1);
        if (lo >= 0xdc00 && lo <= 0xdfff) {
          i++;
          var cp = 0x10000 + ((c - 0xd800) << 10) + (lo - 0xdc00);
          bytes = [
            0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f),
            0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f),
          ];
        } else {
          bytes = [0xef, 0xbf, 0xbd];      // lone surrogate → U+FFFD
        }
      } else if (c >= 0xd800 && c <= 0xdfff) {
        bytes = [0xef, 0xbf, 0xbd];
      } else {
        bytes = [0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f)];
      }
      for (var b = 0; b < bytes.length; b++) {
        h = Math.imul(h ^ bytes[b], 0x01000193) >>> 0;
      }
    }
    return h >>> 0;
  }

  function hex8(n) {
    var s = (n >>> 0).toString(16);
    while (s.length < 8) s = '0' + s;
    return s;
  }

  /** The tool call this part belongs to, as the graph named it. */
  function toolCallIdOf(part) {
    if (!part || typeof part !== 'object') return '';
    return String(part.call_id || part.tool_call_id || '');
  }

  /** A key-less "running" tool part that a later stamp of its tool
   *  finished. Key-less parts key on name + state + text, so a running
   *  stamp and its done stamp never merge; producers that dropped the call
   *  id wrote exactly that pair and "Running..." stuck beside the finished
   *  row (turn e99d06a0b33f, 2026-09-24). Keyed parts are never touched; a
   *  key-less running part with no finished twin stays. Mirrors
   *  turn_document.py `_stale_running_twin`. */
  function staleRunningTwin(parts, i) {
    var p = parts[i];
    if (!p || toolCallIdOf(p) || String(p.state || '') !== 'running') return false;
    var name = String(p.name || p.title || '');
    for (var j = i + 1; j < parts.length; j++) {
      var later = parts[j];
      if (later && typeof later === 'object' &&
          String(later.type || '') === 'tool' &&
          String(later.name || later.title || '') === name &&
          (later.state === 'done' || later.state === 'failed')) return true;
    }
    return false;
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
   *
   * Every row carries `id`, the part's own key. The renderer needs a stable
   * handle to keep an expanded row expanded and a focused control focused
   * while the row's content changes (UNIFIED_TURN_BLOCK.md §3), and deriving
   * it here means the row id and the part key cannot drift apart.
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
      // `ts: null` used to be written on every row that had no timestamp.
      // Python omits the key, so the two normalizers produced different
      // shapes for the same part — invisible to both suites because each
      // had its own examples (UNIFIED_TURN_BLOCK_PHASE0.md §6.1).
      var row;
      if (kind === 'reasoning' && String(p.text || '').trim()) {
        rows.push({
          id: partKey(p),
          kind: 'thought',
          title: 'Thoughts',
          detail: String(p.text),
          state: 'done',
        });
      } else if (kind === 'tool') {
        if (staleRunningTwin(parts, i)) continue;
        row = {
          id: partKey(p),
          kind: 'tool',
          title: String(p.name || p.title || 'tool'),
          detail: String(p.result || p.detail || p.args || ''),
          state: String(p.state || 'done'),
        };
        if (p.ts) row.ts = p.ts;
        rows.push(row);
      } else if (kind === 'status' && String(p.title || '').trim()) {
        row = {
          id: partKey(p),
          kind: 'status',
          title: String(p.title),
          state: String(p.state || 'done'),
        };
        if (p.ts) row.ts = p.ts;
        rows.push(row);
      } else if (kind === 'hitl') {
        var hs = String(resolveGate(p) || 'pending');
        var htitle = 'Waiting for approval';
        if (hs === 'awaiting') htitle = 'Waiting for approval';
        else if (hs === 'approved' || hs === 'inflight') htitle = 'Approved';
        else if (hs === 'denied') htitle = 'Denied';
        else if (hs === 'timeout' || hs === 'error' || hs === 'settled' || hs === 'done') {
          htitle = 'Approval resolved';
        }
        row = {
          id: partKey(p),
          kind: 'status',
          title: htitle,
          detail: String(p.tool || p.detail || ''),
          state: 'info',
        };
        if (p.ts) row.ts = p.ts;
        rows.push(row);
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
    if (kind === 'reasoning') return 'reasoning';
    if (kind === 'tool') {
      // One slot PER CALL, keyed by the graph's own run id when the
      // producer sent one. The old key was name + state + result[:80], so
      // the SAME call changed identity the moment its state went
      // running→done or its result grew: a re-keyed row loses its expanded
      // state and its focus on every update, and two concurrent calls to
      // one tool collided into a single key. Mirrors turn_document.py.
      var callId = toolCallIdOf(part);
      if (callId) return 'tool#' + callId;
      // Legacy frames carry no call id. Keep the content-derived key so
      // old transcripts still dedupe the way they were written.
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

  /** A tool call only moves forward: running → anything terminal. */
  function toolRank(state) {
    var s = String(state || 'done').toLowerCase();
    return (s === 'running' || s === 'pending' || s === '') ? 0 : 1;
  }

  /**
   * Merge two stamps of the SAME tool call.
   *
   * Reached only when both share a key, which with a call id means they
   * are genuinely the same call. A late `running` frame must not un-finish
   * a call that already reported, and a terminal frame that carries no
   * result must not blank the one the earlier frame delivered.
   */
  function mergeToolPart(existing, incoming) {
    if (!incoming || typeof incoming !== 'object') {
      return existing && typeof existing === 'object' ? existing : {};
    }
    if (!existing || typeof existing !== 'object' || existing.type !== 'tool') {
      return incoming;
    }
    if (toolRank(incoming.state) < toolRank(existing.state)) return existing;
    var out = {};
    var k;
    for (k in existing) {
      if (Object.prototype.hasOwnProperty.call(existing, k)) out[k] = existing[k];
    }
    for (k in incoming) {
      if (Object.prototype.hasOwnProperty.call(incoming, k)) out[k] = incoming[k];
    }
    out.type = 'tool';
    if (!String(incoming.result || '').trim() && String(existing.result || '').trim()) {
      out.result = existing.result;
    }
    return out;
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

  function mergeReasoningPart(existing, incoming) {
    if (!incoming || typeof incoming !== 'object') {
      return existing && typeof existing === 'object' ? existing : {};
    }
    if (!existing || typeof existing !== 'object' || existing.type !== 'reasoning') {
      var fresh = {};
      var fk;
      for (fk in incoming) {
        if (Object.prototype.hasOwnProperty.call(incoming, fk)) fresh[fk] = incoming[fk];
      }
      fresh.type = 'reasoning';
      return fresh;
    }
    var oldT = String(existing.text || '');
    var newT = String(incoming.text || '');
    var out = {};
    var ek, ik;
    for (ek in existing) {
      if (Object.prototype.hasOwnProperty.call(existing, ek)) out[ek] = existing[ek];
    }
    for (ik in incoming) {
      if (Object.prototype.hasOwnProperty.call(incoming, ik)) out[ik] = incoming[ik];
    }
    out.type = 'reasoning';
    if (!newT) out.text = oldT;
    else if (!oldT || newT === oldT || newT.indexOf(oldT) !== -1) out.text = newT;
    else if (oldT.indexOf(newT) !== -1) out.text = oldT;
    else {
      var cut = earlierCutOf(oldT, newT);
      out.text = cut !== null ? oldT.slice(0, cut) + newT
        : oldT.replace(/\s+$/, '') + '\n\n' + newT;
    }
    return out;
  }

  /** Shortest overlap that counts as "the same narration, later". */
  var REASONING_MIN_OVERLAP = 64;

  /** Where `old` ends with an earlier cut of `nw`, or null. A durable
   *  checkpoint stores the narration mid-stream and the full narration
   *  arrives later; appending it printed the notes twice, the first copy
   *  stopping mid-sentence (turn e99d06a0b33f, 2026-09-24). Mirrors
   *  turn_document.py `_earlier_cut_of`; shared cases in
   *  tests/fixtures/unified_turn/merge/reasoning_merge.json. */
  function earlierCutOf(old, nw) {
    if (nw.length < REASONING_MIN_OVERLAP) return null;
    var probe = nw.slice(0, REASONING_MIN_OVERLAP);
    var at = old.indexOf(probe);
    while (at >= 0) {
      if (nw.indexOf(old.slice(at)) === 0) return at;
      at = old.indexOf(probe, at + 1);
    }
    return null;
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
        var toolPart = {
          type: 'tool',
          name: String(row.title || 'tool'),
          result: String(row.detail || ''),
          state: String(row.state || 'done'),
        };
        // A stored row keyed by call id must come back as the same part,
        // or a history load would split one call into two rows.
        var rid = String(row.id || '');
        if (rid.indexOf('tool#') === 0) toolPart.call_id = rid.slice(5);
        else if (row.call_id) toolPart.call_id = String(row.call_id);
        out.push(toolPart);
      } else if (kind === 'thought') {
        var detail = String(row.detail || '');
        if (detail.trim()) out.push({ type: 'reasoning', text: detail });
      } else if (kind === 'status' || kind === 'info') {
        // A row whose id names another part is that part's RENDERING,
        // not a part of its own. Reviving it mints a duplicate with a
        // different key — belt to the braces above: even a caller that
        // folds a derived activity list cannot conjure a second row.
        var rid = String(row.id || '');
        if (rid.indexOf('hitl:') === 0 || rid.indexOf('reasoning') === 0
            || rid.indexOf('tool') === 0) continue;
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
        if (replace && part.type === 'reasoning') {
          for (var rj = 0; rj < out.length; rj++) {
            if (partKey(out[rj]) === key) { out[rj] = mergeReasoningPart(out[rj], part); return; }
          }
        }
        if (replace && part.type === 'tool') {
          // With a call id the key is stable across running→done, so the
          // second stamp of a call arrives HERE rather than as a new part.
          // Dropping it (the old behavior for every duplicate key) would
          // freeze every tool row at "running".
          for (var rt = 0; rt < out.length; rt++) {
            if (partKey(out[rt]) === key) { out[rt] = mergeToolPart(out[rt], part); return; }
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
      var displaced = { type: 'reasoning', text: oldText };
      var rkey = partKey(displaced);
      if (seen[rkey]) {
        for (var di = 0; di < out.length; di++) {
          if (partKey(out[di]) === rkey) {
            out[di] = mergeReasoningPart(out[di], displaced);
            break;
          }
        }
      } else {
        out.unshift(displaced);
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

  /** Wall clock, in one place so a test can pin it. */
  function nowMs() {
    if (typeof root.__kazmaNowMs === 'function') return root.__kazmaNowMs();
    return Date.now();
  }

  function empty(turnId) {
    return {
      turnId: String(turnId || ''),
      // Delivery order of FRAMES on this thread.
      seq: 0,
      // Authoritative revision of this TURN's durable state, as the server
      // stamped it. A different counter from `seq`, deliberately: one
      // orders frames, the other orders writes, and
      // UNIFIED_TURN_BLOCK.md §6 forbids comparing them.
      rev: 0,
      schema: 0,
      // Elapsed time as the SERVER last reported it, and the local instant
      // that report arrived. The header may tick a display forward from
      // `elapsedAtMs`; it may not treat the result as a fact about the
      // turn, and it never restarts the clock on reconnect (plan §3).
      // Deriving elapsed from a client clock is how the live task card
      // printed "Done 0s" while the graph was still working.
      elapsedS: 0,
      elapsedAtMs: 0,
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
    var callId = toolCallIdOf(incoming);
    var next = (parts || []).slice();
    var found = -1;
    for (var i = next.length - 1; i >= 0; i--) {
      if (!next[i] || next[i].type !== 'tool') continue;
      if (callId) {
        // Identity beats name. Two concurrent calls to one tool used to
        // land on the same row because "last part with this name" was the
        // only handle there was.
        if (toolCallIdOf(next[i]) === callId) { found = i; break; }
        continue;
      }
      if (String(next[i].name || next[i].title || '') === name
          && !toolCallIdOf(next[i])) {
        found = i;
        break;
      }
    }
    if (found >= 0) {
      next[found] = mergeToolPart(next[found], incoming);
      return next;
    }
    return mergeParts(next, [incoming]);
  }

  /**
   * Move streamed narration into the thoughts region.
   *
   * Called the moment a turn produces a tool call or a gate: whatever the
   * model said BEFORE asking to act was thinking out loud, not the reply.
   *
   * Without this the narration sits in the answer region until something
   * displaces it. Mid-turn there is no final text to compare against, so
   * `splitStreamAndFinal(stream, stream)` classifies everything streamed
   * as `text`; the separation only happens retroactively when a differing
   * final arrives — which on a resume leg is the backfill frame. The
   * reader sees the text swap the instant they click Approve.
   *
   * `stream` is cleared as well, because the next leg's tokens append to
   * it: leaving it would let `partsFromStream` rebuild a text part
   * containing the narration we just folded away.
   *
   * Idempotent (invariant U04): with no text part left there is nothing
   * to move, so a replayed or duplicated frame is a no-op.
   */
  function foldNarration(doc) {
    if (!doc || doc.status === 'done') return doc;
    var parts = doc.parts || [];
    var narration = '';
    var kept = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p && p.type === 'text') {
        var t = String(p.text || '').trim();
        if (t) narration = t;
        continue;
      }
      kept.push(p);
    }
    if (!narration) return doc;
    doc.parts = mergeParts(kept, [{ type: 'reasoning', text: narration }]);
    doc.stream = '';
    return doc;
  }

  function eventToParts(ev) {
    var type = String((ev && ev.type) || '');
    var step = (ev && ev.step) || {};
    if (type === 'progress' && step && step.kind) {
      return activityToParts([step]);
    }
    if (type === 'tool_start' || type === 'tool_call' || type === 'tool_lifecycle') {
      var toolState = ev.status === 'tool_completed' ? 'done'
        : (ev.status === 'tool_failed' ? 'failed' : 'running');
      var startPart = {
        type: 'tool',
        name: String(ev.tool_name || ev.name || step.title || 'tool'),
        result: String(ev.result || ev.error || ev.inputs || ev.args || ev.detail || step.detail || ''),
        state: toolState,
      };
      if (ev.tool_call_id) startPart.call_id = String(ev.tool_call_id);
      return [startPart];
    }
    if (type === 'tool_result') {
      var endPart = {
        type: 'tool',
        name: String(ev.tool_name || ev.name || 'tool'),
        result: String(ev.result || ev.detail || ''),
        state: 'done',
      };
      if (ev.tool_call_id) endPart.call_id = String(ev.tool_call_id);
      return [endPart];
    }
    if (type === 'status' || type === 'status_update') {
      var title = String((ev.message || ev.title || ev.status || step.title || '')).trim();
      if (!title) return [];
      return [{ type: 'status', title: title, state: 'running' }];
    }
    return [];
  }

  // An action has one start boundary even when multiple transports/reporters
  // describe it. A later update must not consume the next model invocation's
  // text. Use document identity, not event sequence or rendered content.
  // Callers require an action ID before destructive reclassification: legacy
  // telemetry without identity cannot establish a new stream boundary.
  function hasPart(doc, part) {
    var key = partKey(part);
    return (doc.parts || []).some(function (existing) {
      return partKey(existing) === key;
    });
  }

  function applyEvent(doc, ev) {
    if (!doc) doc = empty('');
    ev = ev || {};
    var key = eventKey(ev);
    if (doc.seen && doc.seen[key]) return doc;
    var next = {
      turnId: doc.turnId || String(ev.turn_id || ev.turnId || ''),
      seq: doc.seq || 0,
      rev: Number(doc.rev || 0),
      schema: Number(doc.schema || 0),
      elapsedS: Number(doc.elapsedS || 0),
      elapsedAtMs: Number(doc.elapsedAtMs || 0),
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
      // ── U05: an old snapshot cannot regress an authoritative revision ──
      // /messages and /status are fetched in parallel on every resync, and
      // a slow response can land after newer state is already applied.
      // Without a revision the only defence was that mergeParts happens to
      // be additive, which says nothing about `status`: a stale row could
      // still stamp the turn done, or paused, over the truth.
      var evRev = Number(ev.rev);
      if (!isFinite(evRev) || evRev < 0) evRev = 0;
      if (evRev > 0 && evRev < Number(doc.rev || 0)) return doc;
      if (evRev > next.rev) next.rev = evRev;
      var evSchema = Number(ev.schema);
      if (isFinite(evSchema) && evSchema > next.schema) next.schema = evSchema;
      if (Array.isArray(ev.parts) && ev.parts.length) {
        // MERGE, never replace. A snapshot covers what was DURABLE when it
        // was taken; tokens streamed since are not in it, so assigning it
        // over the document dropped live content, and an omitted part is
        // ambiguity, not an authoritative removal (plan §5, §6.6).
        // Replacement needs explicit removal semantics, which no producer
        // sends today.
        // One sentence cannot be both the thinking and the reply.
        //
        // A paused row carries BOTH: `_hitl_persist_parts` writes the
        // narration as `reasoning`, while the text checkpoints written
        // during streaming (`DurablePresentation`) already left a `text`
        // part with the same string, and upsert merges rather than
        // replaces. Merging the snapshot verbatim therefore put the
        // narration back in the answer region (measured in the live
        // page, 2026-09-20).
        //
        // When a snapshot contradicts itself this way the CLASSIFICATION
        // wins: something deliberately said "this is a thought", and
        // nothing deliberately said "this is the answer".
        var thoughts = {};
        var ti2;
        for (ti2 = 0; ti2 < ev.parts.length; ti2++) {
          var tp = ev.parts[ti2];
          if (tp && tp.type === 'reasoning' && String(tp.text || '').trim()) {
            thoughts[String(tp.text).trim()] = 1;
          }
        }
        var incoming = [];
        for (ti2 = 0; ti2 < ev.parts.length; ti2++) {
          var ip = ev.parts[ti2];
          if (ip && ip.type === 'text' && thoughts[String(ip.text || '').trim()]) {
            continue;
          }
          incoming.push(ip);
        }
        next.parts = mergeParts(next.parts, incoming);
      }
      // The `content` column is the row's text WITHOUT a classification:
      // at a pause the server stores the narration there and records in
      // `parts` that it is a thought (`_hitl_persist_parts`). Re-adding it
      // as a text part here put it straight back under the CoT block one
      // event after the gate folded it away — measured in the live page,
      // 2026-09-20.
      //
      // So: if this snapshot's own parts already call this exact text a
      // thought, believe them. Legacy rows, which carry content and no
      // parts, are unaffected.
      if (ev.content) {
        var evText = String(ev.content);
        var asThought = evText.trim();
        var classified = false;
        if (asThought && Array.isArray(ev.parts)) {
          for (var ci = 0; ci < ev.parts.length; ci++) {
            var cp = ev.parts[ci];
            if (cp && cp.type === 'reasoning'
                && String(cp.text || '').trim() === asThought) {
              classified = true;
              break;
            }
          }
        }
        if (!classified) {
          next.parts = mergeParts(next.parts, [{ type: 'text', text: evText }]);
          next.stream = evText;
        }
      }
      // Activity is DERIVED from parts, so folding both in double-counts.
      // A stored row carries both (the /messages serializer sends
      // `parts` and `activity`), and activity_of renders a gate as a
      // status row — which activityToParts turned back into a `status`
      // part keyed status:Approved, distinct from the hitl part it came
      // from. Every reload grew one phantom row per gate. Only consult
      // activity when there are no parts to derive it from, which is the
      // legacy row this branch exists for.
      var hadParts = Array.isArray(ev.parts) && ev.parts.length;
      if (!hadParts && Array.isArray(ev.activity) && ev.activity.length) {
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
    if (type === 'turn_heartbeat') {
      // The one frame that carries a server-measured elapsed. Monotone:
      // a replayed older heartbeat must not walk the clock backwards.
      var hb = Number(ev.elapsed_s);
      if (isFinite(hb) && hb >= next.elapsedS) {
        next.elapsedS = hb;
        next.elapsedAtMs = nowMs();
      }
      return next;
    }
    if (type === 'done' || type === 'turn_complete') {
      var doneMs = Number(ev.duration_ms);
      if (isFinite(doneMs) && doneMs > 0) {
        var doneS = doneMs / 1000;
        if (doneS >= next.elapsedS) next.elapsedS = doneS;
        next.elapsedAtMs = nowMs();
      }
      var finalText = String(ev.content || next.stream || '');
      next.parts = mergeParts(next.parts, partsFromStream(next.stream, finalText));
      next.stream = finalText || next.stream;
      next.status = ev.interrupted ? 'paused' : 'done';
      // A PAUSED turn has not answered yet.
      //
      // The pause frame carries `content: content_acc` — the narration so
      // far — and without this it lands as a `text` part, putting back
      // under the CoT block exactly what the gate just folded away. The
      // server agrees at the other end (`_hitl_persist_parts`), so live
      // and hydrated say the same thing about a paused turn.
      if (ev.interrupted) next = foldNarration(next);
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
      // Only a NEW request starts a boundary. Claims, settlement, and repeat
      // announcements describe the earlier request, potentially while the
      // next response is already streaming. Folding on those erased both
      // the answer region and its token prefix until another token arrived.
      if (hitlState === 'pending' && iid && !hasPart(next, hitlPart)) {
        next = foldNarration(next);
      }
      next.parts = mergeParts(next.parts, [hitlPart]);
      // Any pending gate pauses the turn — not just the newest one. With one
      // part per gate, "the last hitl part" is the gate asked most recently,
      // which after a sequential approve is the SETTLED one; reading status
      // off it reported "streaming" while the graph sat blocked.
      var mergedHitl = hitlPartOf(next.parts);
      var resolved = String((mergedHitl && mergedHitl.state) || hitlState);
      if (doc.status === 'done' || doc.status === 'error') return next;
      if (resolved === 'pending') next.status = 'paused';
      else if (resolved === 'approved' || resolved === 'denied' || resolved === 'inflight') {
        next.status = 'streaming';
      } else if (next.status === 'paused') {
        // A settled gate is not a completed turn. Only the turn's terminal
        // event can close it; further model output may still be in flight.
        next.status = 'streaming';
      }
      return next;
    }
    var extra = eventToParts(ev);
    if (extra.length) {
      var toolish = extra.length === 1 && extra[0].type === 'tool';
      // A tool STARTING is the same signal a gate is: the model said
      // something and then asked to act, so what it said was thinking.
      // Only on start -- a tool_result arriving must not fold whatever
      // the next leg has already begun streaming.
      if (toolish && String(extra[0].state || '') === 'running'
          && toolCallIdOf(extra[0]) && !hasPart(next, extra[0])) {
        next = foldNarration(next);
      }
      next.parts = toolish
        ? replaceToolPart(next.parts, extra[0])
        : mergeParts(next.parts, extra);
    }
    return next;
  }

  /**
   * Stable id for an assistant row that never got a turn_id.
   *
   * MUST equal turn_document.py:legacy_turn_id byte for byte. It did not:
   * Python hashed with sha256[:16] and this used a 32-bit string hash, so
   * the same stored row was "legacy-879abd24dca7291f" on the server and
   * "legacy-9587b3b6" in the browser. Both suites were green because each
   * had its own examples — the drift shared fixtures exist to catch
   * (UNIFIED_TURN_BLOCK_PHASE0.md §6.1).
   *
   * Two FNV-1a-32 passes with different offset bases give 64 bits without
   * 64-bit arithmetic, which JavaScript cannot do exactly. sha256 was the
   * other option and would have meant shipping a hash implementation to
   * the browser for an id nobody verifies.
   */
  function legacyTurnId(msg) {
    msg = msg || {};
    var raw = String(msg.ts || '') + '|' + String(msg.content || '');
    return 'legacy-' + hex8(fnv1a32(raw, 0x811c9dc5))
      + hex8(fnv1a32(raw, 0x9e3779b1));
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
      // Carried so a later resync can tell this snapshot's age. A row from
      // before revisions existed reads as 0, which is older than anything.
      rev: msg.rev,
      schema: msg.schema,
    });
  }

  root.KazmaTurnDocument = {
    textOf: textOf,
    activityOf: activityOf,
    activityForMessage: activityForMessage,
    mergeParts: mergeParts,
    mergeHitlPart: mergeHitlPart,
    mergeReasoningPart: mergeReasoningPart,
    mergeToolPart: mergeToolPart,
    activityToParts: activityToParts,
    toolCallIdOf: toolCallIdOf,
    toolRank: toolRank,
    legacyTurnId: legacyTurnId,
    fnv1a32: fnv1a32,
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
    nowMs: nowMs,
    applyEvent: applyEvent,
    fromMessage: fromMessage,
    hydrateMessage: hydrateMessage,
    replaceToolPart: replaceToolPart,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
