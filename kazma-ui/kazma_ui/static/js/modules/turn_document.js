/* ═══════════════════════════════════════════════════════
   Kazma TurnDocument — parts projector (P1/P2)
   Pure logic, DOM-free. A turn is reasoning / tool / status / hitl / text.
   content + activity are derived. Works in the browser and under Node.
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

  root.KazmaTurnDocument = {
    textOf: textOf,
    activityOf: activityOf,
    activityForMessage: activityForMessage,
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
