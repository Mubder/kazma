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
  var unavailable = false;  // set when the replay API is missing (404/503)

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
    return window.KazmaFormat ? (window.KazmaFormat.relative(iso) || iso) : iso;
  }
  /* A catalog string in the page's language, {name} filled from *vars*. */
  function tx(key, fallback, vars) {
    var text = window.tOr ? window.tOr(key, fallback) : fallback;
    for (var k in (vars || {})) text = text.split('{' + k + '}').join(String(vars[k]));
    return text;
  }

  // ── Public API ──
  window.KazmaReplay = {
    /** A thread named by the address (/replay?thread=...), opened once.
     *  The chat's /replay and /fork answers link here (2026-09-28). */
    _linked: '',

    init: function () {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      try {
        this._linked = new URLSearchParams(window.location.search).get('thread') || '';
      } catch (e) { this._linked = ''; }
      this.loadThreads();
      pollTimer = setInterval(this.loadThreads.bind(this), 10000);
      _registerSoftNavTeardown();
    },

    destroy: function () {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    },

    /** Stop polling + mark the panel as backend-unavailable (404/503). */
    _markUnavailable: function (status) {
      unavailable = true;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      var sel = $('replay-thread-select');
      if (sel) {
        sel.innerHTML = '';
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = tx('replay.unavailable_option', 'Time travel unavailable (API {status})', { status: status });
        sel.appendChild(opt);
      }
      var listEl = $('replay-timeline-list');
      if (listEl) {
        listEl.innerHTML =
          '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' +
          esc(tx('replay.unavailable_body', 'Time travel is unavailable on this server (replay API returned {status}). Check the server log for "[Replay] snapshot recorder creation failed" and restart the server.', { status: status })) + ' ' +
          '<button class="btn btn-sm btn-primary" style="margin-top:12px" onclick="KazmaReplay.retry()">' + esc(tx('common.retry', 'Retry')) + '</button>' +
          '</div>';
      }
    },

    /** Manual recovery after a server restart fixed the recorder. */
    retry: function () {
      unavailable = false;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
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
      if (unavailable) return;
      fetch('/api/replay/threads', { credentials: 'same-origin' })
        .then(function (r) {
          if (r.status === 404 || r.status === 503) {
            KazmaReplay._markUnavailable(r.status);
            return null;
          }
          return r.ok ? r.json() : { threads: [], count: 0 };
        })
        .then(function (data) {
          if (data === null) return;  // unavailable — already handled
          var sel = $('replay-thread-select');
          if (!sel) return;
          var prev = sel.value;
          sel.innerHTML = '<option value="">' + esc(tx('replay.select_thread', '— Select a thread —')) + '</option>';
          // Each thread named by its chat (title, newest first); a thread no
          // chat owns shows its id. The list used to be bare uuids.
          var items = data.items || (data.threads || []).map(function (id) {
            return { thread_id: id, title: '' };
          });
          items.forEach(function (it) {
            var opt = document.createElement('option');
            opt.value = it.thread_id;
            opt.textContent = it.title || it.thread_id;
            opt.setAttribute('translate', 'no');
            sel.appendChild(opt);
          });
          if (prev && (data.threads || []).indexOf(prev) !== -1) sel.value = prev;
          var linked = KazmaReplay._linked;
          if (linked) {
            KazmaReplay._linked = '';
            if ((data.threads || []).indexOf(linked) !== -1) {
              sel.value = linked;
              KazmaReplay.loadTimeline(linked);
            }
          }
        })
        .catch(function () { /* network blip — retry on next poll */ });
    },

    loadTimeline: function (threadId) {
      currentThread = threadId;
      currentIteration = null;
      $('replay-snapshot-detail').style.display = 'none';
      var listEl = $('replay-timeline-list');

      if (!threadId) {
        listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(tx('replay.pick_thread_hint', 'Select a thread above to see its snapshot timeline.')) + '</div>';
        $('replay-snapshot-count').textContent = '';
        return;
      }

      listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(tx('common.loading', 'Loading…')) + '</div>';

      fetch('/api/replay/snapshots/' + encodeURIComponent(threadId), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { snapshots: [], count: 0 }; })
        .then(function (data) {
          var snaps = data.snapshots || [];
          $('replay-snapshot-count').textContent = tx('replay.snapshot_count', 'Snapshots: {n}', { n: snaps.length });

          // Populate diff dropdowns too
          ['replay-diff-a', 'replay-diff-b'].forEach(function (id) {
            var dd = $(id);
            if (!dd) return;
            dd.innerHTML = '';
            snaps.forEach(function (s) {
              var opt = document.createElement('option');
              opt.value = s.iteration; opt.textContent = tx('replay.iteration_n', 'Iteration {n}', { n: s.iteration });
              dd.appendChild(opt);
            });
            if (snaps.length >= 2) { $(id).value = snaps[Math.max(0, snaps.length - 2)].iteration; }
            if (id === 'replay-diff-b' && snaps.length >= 1) { $(id).value = snaps[snaps.length - 1].iteration; }
          });

          if (!snaps.length) {
            listEl.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(tx('replay.no_snapshots', 'No snapshots for this thread yet. Snapshots are captured after each agent turn.')) + '</div>';
            return;
          }

          listEl.innerHTML = snaps.map(function (s) {
            return '<div class="card replay-snap-card" style="padding:12px 16px;cursor:pointer;" data-iteration="' + s.iteration + '">' +
              '<div style="display:flex;align-items:center;justify-content:space-between;">' +
                '<div>' +
                  '<span style="font-weight:600;color:var(--text-primary);">' + esc(tx('replay.iteration_n', 'Iteration {n}', { n: s.iteration })) + '</span>' +
                  '<span translate="no" style="margin-inline-start:8px;font-size:0.85rem;color:var(--text-muted);">' + esc(s.model || '—') + '</span>' +
                '</div>' +
                '<div style="display:flex;gap:12px;font-size:0.85rem;color:var(--text-muted);">' +
                  '<span>' + esc(tx('replay.messages_n', 'Messages: {n}', { n: s.message_count })) + '</span>' +
                  '<span>' + timeAgo(s.timestamp) + '</span>' +
                '</div>' +
              '</div>' +
            '</div>';
          }).join('');
          if (!listEl._boundClick) {
            listEl._boundClick = true;
            listEl.addEventListener('click', function (e) {
              var card = e.target.closest('.replay-snap-card');
              if (card && card.dataset.iteration) {
                KazmaReplay.viewSnapshot(parseInt(card.dataset.iteration, 10));
              }
            });
          }
        })
        .catch(function (err) {
          listEl.innerHTML = '<div style="padding:1rem;color:var(--error);">' + esc(tx('replay.load_failed', 'Failed to load: {error}', { error: err.message })) + '</div>';
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
          if (!data || data.error) { toast(tx('replay.snapshot_failed', 'Could not load snapshot'), 'error'); return; }
          $('replay-detail-title').textContent = tx('replay.iteration_n', 'Iteration {n}', { n: iteration });
          $('replay-detail-meta').innerHTML =
            '<span>' + esc(tx('replay.meta_model', 'Model')) + ': <strong translate="no">' + esc(data.model || '—') + '</strong></span> · ' +
            '<span>' + esc(tx('replay.meta_cost', 'Cost')) + ': $' + (data.cost_usd || 0).toFixed(4) + '</span> · ' +
            '<span>' + esc(tx('replay.messages_n', 'Messages: {n}', { n: data.message_count })) + '</span>';
          var msgs = data.messages || [];
          $('replay-detail-messages').innerHTML = msgs.map(function (m) {
            var role = m.role || '?';
            var content = m.content || '';
            if (typeof content !== 'string') content = JSON.stringify(content, null, 2);
            var cls = role === 'user' ? 'replay-msg-user' : (role === 'assistant' ? 'replay-msg-assistant' : 'replay-msg-tool');
            return '<div class="' + cls + '" style="padding:8px 12px;border-radius:6px;margin-bottom:4px;font-size:0.85rem;' +
              'background:' + (role === 'user' ? 'rgba(99,102,241,0.08)' : role === 'assistant' ? 'rgba(34,197,94,0.08)' : 'rgba(161,161,170,0.08)') + ';">' +
              '<strong>' + esc(tx('replay.role_' + role, role)) + ':</strong> <span translate="no">' + esc(content.slice(0, 500)) + (content.length > 500 ? '…' : '') + '</span>' +
              '</div>';
          }).join('');
        })
        .catch(function () { toast(tx('replay.detail_failed', 'Failed to load snapshot detail'), 'error'); });
    },

    restoreCurrent: async function () {
      if (!currentThread || currentIteration == null) { toast(tx('replay.pick_snapshot', 'Select a snapshot first'), 'error'); return; }
      if (!await confirm(tx('replay.confirm_restore', 'Rewind this thread to iteration {n}? Later turns will be lost (use Fork to preserve them).', { n: currentIteration }))) return;
      fetch('/api/replay/restore', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, iteration: currentIteration }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(tx('replay.restore_failed', 'Restore failed: {error}', { error: data.error }), 'error'); return; }
          toast(tx('replay.restored', 'Restored iteration {n} ({count} messages)', { n: currentIteration, count: data.message_count }), 'success');
        })
        .catch(function () { toast(tx('replay.restore_request_failed', 'Restore request failed'), 'error'); });
    },

    forkCurrent: function () {
      if (!currentThread || currentIteration == null) { toast(tx('replay.pick_snapshot', 'Select a snapshot first'), 'error'); return; }
      fetch('/api/replay/fork', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, iteration: currentIteration }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(tx('replay.fork_failed', 'Fork failed: {error}', { error: data.error }), 'error'); return; }
          toast(tx('replay.forked', 'Forked into {thread}', { thread: data.new_thread_id }), 'success');
        })
        .catch(function () { toast(tx('replay.fork_request_failed', 'Fork request failed'), 'error'); });
    },

    compare: function () {
      if (!currentThread) { toast(tx('replay.pick_thread_first', 'Select a thread first'), 'error'); return; }
      var a = $('replay-diff-a').value;
      var b = $('replay-diff-b').value;
      if (!a || !b) { toast(tx('replay.pick_two', 'Pick two iterations'), 'error'); return; }
      $('replay-diff-result').innerHTML = '<div style="padding:1rem;color:var(--text-muted);">' + esc(tx('replay.comparing', 'Comparing…')) + '</div>';
      fetch('/api/replay/compare', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: currentThread, a: parseInt(a), b: parseInt(b) }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { $('replay-diff-result').innerHTML = '<div translate="no" style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
          var d = data.diff;
          if (!d) { $('replay-diff-result').innerHTML = '<div>' + esc(tx('replay.no_diff', 'No diff available.')) + '</div>'; return; }
          function arrow(v) { return v > 0 ? '+' + v : String(v); }
          var changed = tx('replay.changed', 'changed');
          var same = tx('replay.same', 'same');
          $('replay-diff-result').innerHTML =
            '<table class="data-table" style="width:100%;border-collapse:collapse;font-size:0.9rem;">' +
              '<thead><tr><th style="text-align:start;padding:8px;border-bottom:1px solid var(--border);">' + esc(tx('replay.col_metric', 'Metric')) + '</th>' +
              '<th style="text-align:end;padding:8px;border-bottom:1px solid var(--border);">' + esc(tx('replay.iteration_n', 'Iteration {n}', { n: a })) + '</th>' +
              '<th style="text-align:end;padding:8px;border-bottom:1px solid var(--border);">' + esc(tx('replay.iteration_n', 'Iteration {n}', { n: b })) + '</th>' +
              '<th style="text-align:end;padding:8px;border-bottom:1px solid var(--border);">' + esc(tx('replay.col_delta', 'Delta')) + '</th></tr></thead>' +
              '<tbody>' +
                row(tx('replay.row_messages', 'Messages'), d.original_message_count, d.replayed_message_count, arrow(d.message_count_delta)) +
                row(tx('replay.row_iteration', 'Iteration #'), d.original_iteration, d.replayed_iteration, arrow(d.iteration_delta)) +
                row(tx('replay.meta_model', 'Model'), d.original_model || '—', d.replayed_model || '—', d.model_changed ? changed : same) +
                row(tx('replay.row_cost', 'Cost (USD)'), d.original_cost_usd.toFixed(4), d.replayed_cost_usd.toFixed(4), arrow(d.cost_delta_usd.toFixed(4))) +
                row(tx('replay.row_tool_calls', 'Tool calls'), d.original_tool_calls, d.replayed_tool_calls, arrow(d.tool_calls_delta)) +
                row(tx('replay.row_next_node', 'Next node'), d.original_next_node || '—', d.replayed_next_node || '—', d.routing_changed ? changed : same) +
              '</tbody>' +
            '</table>' +
            (d.identical ? '<p style="margin-top:1rem;color:var(--success);display:flex;align-items:center;gap:6px;">' +
              KazmaIcons.span('check-circle') + ' ' + esc(tx('replay.identical', 'States are identical.')) + '</p>' : '');
        })
        .catch(function () { toast(tx('replay.compare_failed', 'Compare failed'), 'error'); });
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
      '<td translate="no" style="text-align:end;padding:8px;border-bottom:1px solid var(--border);">' + esc(a) + '</td>' +
      '<td translate="no" style="text-align:end;padding:8px;border-bottom:1px solid var(--border);">' + esc(b) + '</td>' +
      '<td style="text-align:end;padding:8px;border-bottom:1px solid var(--border);font-weight:600;">' + esc(delta) + '</td></tr>';
  }

  function _registerSoftNavTeardown() {
    if (typeof window === 'undefined') return;
    var teardown = function () {
      if (window.KazmaReplay && typeof window.KazmaReplay.destroy === 'function') {
        window.KazmaReplay.destroy();
      }
    };
    if (Array.isArray(window.kazmaOnSoftNavLeave)) {
      window.kazmaOnSoftNavLeave.push(teardown);
    } else if (typeof window.kazmaOnSoftNavLeave === 'function') {
      window.kazmaOnSoftNavLeave = [window.kazmaOnSoftNavLeave, teardown];
    } else {
      window.kazmaOnSoftNavLeave = [teardown];
    }
  }

  // Auto-init on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.KazmaReplay.init(); });
  } else {
    window.KazmaReplay.init();
  }
})();
