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

  function activityOf(parts) {
    var rows = [];
    if (!Array.isArray(parts)) return rows;
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
        rows.push({
          kind: 'status',
          title: 'Waiting for approval',
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
    if (kind === 'hitl') return 'hitl:' + String(part.tool || part.detail || '');
    return kind + ':' + JSON.stringify(part).slice(0, 80);
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

    function add(part) {
      if (part.type === 'text') return;
      var key = partKey(part);
      if (seen[key]) return;
      seen[key] = 1;
      out.push(part);
    }
    for (var i = 0; i < existing.length; i++) add(existing[i]);
    for (var j = 0; j < incoming.length; j++) add(incoming[j]);

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
    var c = String((ev && (ev.content || ev.reply || (ev.step && ev.step.title) || '')) || '');
    return 'h:' + t + ':' + c.length + ':' + c.slice(0, 48);
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
        next.parts = ev.parts.slice();
      }
      if (ev.content) {
        next.parts = mergeParts(next.parts, [{ type: 'text', text: String(ev.content) }]);
        next.stream = String(ev.content);
      }
      if (Array.isArray(ev.activity) && ev.activity.length) {
        next.parts = mergeParts(next.parts, activityToParts(ev.activity));
      }
      next.status = (ev.open || ev.pending) ? 'paused' : 'done';
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
    if (type === 'hitl' || type === 'approval_needed' || type === 'paused_for_approval') {
      next.parts = mergeParts(next.parts, [{
        type: 'hitl',
        tool: String(ev.tool || (ev.step && ev.step.title) || ''),
      }]);
      next.status = 'paused';
      return next;
    }
    var extra = eventToParts(ev);
    if (extra.length) next.parts = mergeParts(next.parts, extra);
    return next;
  }

  function fromMessage(msg) {
    msg = msg || {};
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
    partsFromStream: partsFromStream,
    splitStreamAndFinal: splitStreamAndFinal,
    empty: empty,
    applyEvent: applyEvent,
    fromMessage: fromMessage,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
