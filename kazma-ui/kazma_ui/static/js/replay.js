/* Time Travel Replay panel — vanilla IIFE (mirrors swarm.js / hitl_approval.js).
 *
 * Exposes window.KazmaReplay with:
 *   init()                    — bootstrap on page load
 *   switchTab(name)           — tab switcher
 *   loadThreads()             — GET /api/replay/threads → populate picker
 *   loadTimeline(threadId)    — GET /api/replay/snapshots/{id} → render cards
 *   viewSnapshot(iteration)   — GET /api/replay/snapshots/{id}/{it} → detail
 *   restoreCurrent()          — POST /api/replay/restore (rewind live thread)
 *   forkCurrent()             — POST /api/replay/fork (branch into new thread)
 *   compare()                 — POST /api/replay/compare → diff table
 *   onLiveSnapshot(data)      — hook called by streaming.js on 'snapshot' events
 */
(function () {
  'use strict';

  var currentThread = '';
  var currentIteration = null;
  var pollTimer = null;

  // ── Helpers ──
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toast(msg, type) {
    if (window.KazmaStream && KazmaStream.toast) KazmaStream.toast(msg, type || 'info', 3000);
    else if (window.showToast) window.showToast(msg, type || 'info', 3000);
  }
  function timeAgo(iso) {
    if (!iso) return '—';
    try {
      var d = new Date(iso);
      var s = Math.floor((Date.now() - d.getTime()) / 1000);
      if (s < 60) return s + 's ago';
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      return d.toLocaleDateString();
    } catch (e) { return iso; }
  }

  // ── Public API ──
  window.KazmaReplay = {
    init: function () {
      this.loadThreads();
      pollTimer = setInterval(this.loadThreads.bind(this), 10000);
    },

    switchTab: function (name) {
      document.querySelectorAll('#panel-timeline, #panel-diff, #panel-about').forEach(function (p) {
        p.style.display = 'none';
      });
      document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
      var panel = $('panel-' + name);
      var btn = document.querySelector('.tab[data-tab="' + name + '"]');
      if (panel) panel.style.display = 'block';
      if (btn) btn.classList.add('active');
    },

    loadThreads: function () {
      fetch('/api/replay/threads', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { threads: [], count: 0 }; })
        .then(function (data) {
          var sel = $('replay-thread-select');
          if (!sel) return;
          var prev = sel.value;
          sel.innerHTML = '<option value="">— Select a thread —</option>';
          (data.threads || []).forEach(function (t) {
            var opt = document.createElement('option');
            opt.value = t; opt.textContent = t;
            sel.appendChild(opt);
          });
          if (prev && (data.threads || []).indexOf(prev) !== -1) sel.value = prev;
        })
        .catch(function () { /* silent — will retry on poll */ });
    },

    loadTimeline: function (threadId) {
      currentThread = threadId;
      currentIteration = null;
      $('replay-snapshot-detail').style.display = 'none';
      var listEl = $('replay-timeline-list');

      if (!threadId) {
        listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">Select a thread above to see its snapshot timeline.</div>';
        $('replay-snapshot-count').textContent = '';
        return;
      }

      listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">Loading…</div>';

      fetch('/api/replay/snapshots/' + encodeURIComponent(threadId), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { snapshots: [], count: 0 }; })
        .then(function (data) {
          var snaps = data.snapshots || [];
          $('replay-snapshot-count').textContent = snaps.length + ' snapshot' + (snaps.length !== 1 ? 's' : '');

          // Populate diff dropdowns too
          ['replay-diff-a', 'replay-diff-b'].forEach(function (id) {
            var dd = $(id);
            if (!dd) return;
            dd.innerHTML = '';
            snaps.forEach(function (s) {
              var opt = document.createElement('option');
              opt.value = s.iteration; opt.textContent = 'Iteration ' + s.iteration;
              dd.appendChild(opt);
            });
            if (snaps.length >= 2) { $(id).value = snaps[Math.max(0, snaps.length - 2)].iteration; }
            if (id === 'replay-diff-b' && snaps.length >= 1) { $(id).value = snaps[snaps.length - 1].iteration; }
          });

          if (!snaps.length) {
            listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">No snapshots for this thread yet. Snapshots are captured after each agent turn.</div>';
            return;
          }

          listEl.innerHTML = snaps.map(function (s) {
            return '<div class="card replay-snap-card" style="padding:12px 16px;cursor:pointer;" onclick="KazmaReplay.viewSnapshot(' + s.iteration + ')">' +
              '<div style="display:flex;align-items:center;justify-content:space-between;">' +
                '<div>' +
                  '<span style="font-weight:600;color:var(--text-primary);">Iteration ' + s.iteration + '</span>' +
                  '<span style="margin-left:8px;font-size:0.85rem;color:var(--text-muted);">' + esc(s.model || '—') + '</span>' +
                '</div>' +
                '<div style="display:flex;gap:12px;font-size:0.85rem;color:var(--text-muted);">' +
                  '<span>' + s.message_count + ' msgs</span>' +
                  '<span>' + timeAgo(s.timestamp) + '</span>' +
                '</div>' +
              '</div>' +
            '</div>';
          }).join('');
        })
        .catch(function (err) {
          listEl.innerHTML = '<div style="padding:1rem;color:var(--error);">Failed to load: ' + esc(err.message) + '</div>';
        });
    },

    viewSnapshot: function (iteration) {
      if (!currentThread) return;
      currentIteration = iteration;
      var detailEl = $('replay-snapshot-detail');
      detailEl.style.display = 'block';

      fetch('/api/replay/snapshots/' + encodeURIComponent(currentThread) + '/' + iteration, { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || data.error) { toast('Could not load snapshot', 'error'); return; }
          $('replay-detail-title').textContent = 'Iteration ' + iteration;
          $('replay-detail-meta').innerHTML =
            '<span>Model: <strong>' + esc(data.model || '—') + '</strong></span> · ' +
            '<span>Cost: $' + (data.cost_usd || 0).toFixed(4) + '</span> · ' +
            '<span>' + data.message_count + ' messages</span>';
          var msgs = data.messages || [];
          $('replay-detail-messages').innerHTML = msgs.map(function (m) {
            var role = m.role || '?';
            var content = m.content || '';
            if (typeof content !== 'string') content = JSON.stringify(content, null, 2);
            var cls = role === 'user' ? 'replay-msg-user' : (role === 'assistant' ? 'replay-msg-assistant' : 'replay-msg-tool');
            return '<div class="' + cls + '" style="padding:8px 12px;border-radius:6px;margin-bottom:4px;font-size:0.85rem;' +
              'background:' + (role === 'user' ? 'rgba(99,102,241,0.08)' : role === 'assistant' ? 'rgba(34,197,94,0.08)' : 'rgba(161,161,170,0.08)') + ';">' +
              '<strong>' + esc(role) + ':</strong> ' + esc(content.slice(0, 500)) + (content.length > 500 ? '…' : '') +
              '</div>';
          }).join('');
        })
        .catch(function () { toast('Failed to load snapshot detail', 'error'); });
    },

    restoreCurrent: async function () {
      if (!currentThread || currentIteration == null) { toast('Select a snapshot first', 'error'); return; }
      if (!await confirm('Rewind this thread to iteration ' + currentIteration + '? Later turns will be lost (use Fork to preserve them).')) return;
      fetch('/api/replay/restore', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, iteration: currentIteration }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Restore failed: ' + data.error, 'error'); return; }
          toast('Restored iteration ' + currentIteration + ' (' + data.message_count + ' msgs)', 'success');
        })
        .catch(function () { toast('Restore request failed', 'error'); });
    },

    forkCurrent: function () {
      if (!currentThread || currentIteration == null) { toast('Select a snapshot first', 'error'); return; }
      fetch('/api/replay/fork', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, iteration: currentIteration }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Fork failed: ' + data.error, 'error'); return; }
          toast('Forked into ' + data.new_thread_id, 'success');
        })
        .catch(function () { toast('Fork request failed', 'error'); });
    },

    compare: function () {
      if (!currentThread) { toast('Select a thread first', 'error'); return; }
      var a = $('replay-diff-a').value;
      var b = $('replay-diff-b').value;
      if (!a || !b) { toast('Pick two iterations', 'error'); return; }
      $('replay-diff-result').innerHTML = '<div style="padding:1rem;color:var(--text-muted);">Comparing…</div>';
      fetch('/api/replay/compare', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, a: parseInt(a), b: parseInt(b) }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { $('replay-diff-result').innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
          var d = data.diff;
          if (!d) { $('replay-diff-result').innerHTML = '<div>No diff available.</div>'; return; }
          function arrow(v) { return v > 0 ? '+' + v : String(v); }
          $('replay-diff-result').innerHTML =
            '<table class="data-table" style="width:100%;border-collapse:collapse;font-size:0.9rem;">' +
              '<thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);">Metric</th>' +
              '<th style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">Iter ' + a + '</th>' +
              '<th style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">Iter ' + b + '</th>' +
              '<th style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">Delta</th></tr></thead>' +
              '<tbody>' +
                row('Messages', d.original_message_count, d.replayed_message_count, arrow(d.message_count_delta)) +
                row('Iteration #', d.original_iteration, d.replayed_iteration, arrow(d.iteration_delta)) +
                row('Model', d.original_model || '—', d.replayed_model || '—', d.model_changed ? 'changed' : 'same') +
                row('Cost (USD)', d.original_cost_usd.toFixed(4), d.replayed_cost_usd.toFixed(4), arrow(d.cost_delta_usd.toFixed(4))) +
                row('Tool calls', d.original_tool_calls, d.replayed_tool_calls, arrow(d.tool_calls_delta)) +
                row('Next node', d.original_next_node || '—', d.replayed_next_node || '—', d.routing_changed ? 'changed' : 'same') +
              '</tbody>' +
            '</table>' +
            (d.identical ? '<p style="margin-top:1rem;color:var(--success);">✅ States are identical.</p>' : '');
        })
        .catch(function () { toast('Compare failed', 'error'); });
    },

    /** Hook for live snapshot events from the chat SSE stream. */
    onLiveSnapshot: function (data) {
      // If the panel is open and showing the current thread, refresh the timeline.
      if (currentThread && document.getElementById('panel-timeline') &&
          document.getElementById('panel-timeline').style.display !== 'none') {
        this.loadTimeline(currentThread);
      }
    },
  };

  function row(label, a, b, delta) {
    return '<tr><td style="padding:8px;border-bottom:1px solid var(--border);">' + esc(label) + '</td>' +
      '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(a) + '</td>' +
      '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(b) + '</td>' +
      '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);font-weight:600;">' + esc(delta) + '</td></tr>';
  }

  // Auto-init on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.KazmaReplay.init(); });
  } else {
    window.KazmaReplay.init();
  }
})();
