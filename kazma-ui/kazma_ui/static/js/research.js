/* Research Results panel — vanilla IIFE (mirrors replay.js / swarm.js).
 * Exposes window.KazmaResearch with:
 *   init()                — bootstrap on page load
 *   switchTab(name)       — tab switcher
 *   load()                — GET /api/research/tasks → render cards
 *   search(event)         — filter by search text
 *   viewDetail(id)        — GET /api/research/tasks/{id} → detail
 *   exportCurrent(fmt)    — POST /api/research/{id}/export
 *   compare()             — POST /api/research/compare → diff
 */
(function () {
  'use strict';

  var allTasks = [];
  var currentId = null;
  var pollTimer = null;

  function $(id) { return document.getElementById(id); }
  function i18n(key) { return (window.KAZMA_I18N && window.KAZMA_I18N[key]) || key; }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toast(msg, type) {
    if (window.KazmaStream && KazmaStream.toast) KazmaStream.toast(msg, type || 'info', 3000);
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

  window.KazmaResearch = {
    init: function () {
      this.load();
      pollTimer = setInterval(this.load.bind(this), 15000);
    },

    switchTab: function (name) {
      ['list', 'compare', 'about'].forEach(function (t) {
        var p = $('panel-' + t);
        if (p) p.style.display = 'none';
      });
      document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
      var panel = $('panel-' + name);
      var btn = document.querySelector('.tab[data-tab="' + name + '"]');
      if (panel) panel.style.display = 'block';
      if (btn) btn.classList.add('active');
    },

    load: function () {
      fetch('/api/research/tasks?page=1&page_size=50', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { tasks: [], count: 0 }; })
        .then(function (data) {
          allTasks = data.tasks || [];
          renderList(allTasks);
          populateCompareDropdowns(allTasks);
        })
        .catch(function () { /* silent — retry on poll */ });
    },

    search: function (e) {
      var q = (e.target.value || '').toLowerCase();
      var filtered = q ? allTasks.filter(function (t) {
        return (t.prompt || '').toLowerCase().indexOf(q) !== -1;
      }) : allTasks;
      renderList(filtered);
    },

    viewDetail: function (id) {
      currentId = id;
      fetch('/api/research/tasks/' + encodeURIComponent(id), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || data.error) { toast('Could not load', 'error'); return; }
          var t = data.task;
          $('research-detail').style.display = 'block';
          $('research-detail-title').textContent = (t.prompt || 'Research').slice(0, 80);
          $('research-detail-meta').innerHTML =
            '<span>Cost: <strong>$' + (t.cost || 0).toFixed(4) + '</strong></span> · ' +
            '<span>Tokens: ' + (t.tokens || 0) + '</span> · ' +
            '<span>Duration: ' + (t.duration || 0).toFixed(1) + 's</span> · ' +
            '<span>Workers: ' + (t.workers || []).join(', ') + '</span>';
          var output = t.aggregated_output || t.synthesized_output ||
            (t.worker_results && t.worker_results[0] ? t.worker_results[0].output : '') ||
            '(no output)';
          // Render as markdown (rich text) instead of plain text.
          if (window.KazmaStream && KazmaStream.markdown) {
            $('research-detail-output').innerHTML = KazmaStream.markdown(output);
          } else if (window.marked) {
            $('research-detail-output').innerHTML = window.marked(output);
          } else {
            $('research-detail-output').textContent = output;
          }
        });
    },

    exportCurrent: function (fmt) {
      if (!currentId) { toast('Select a research result first', 'error'); return; }
      toast('Exporting to ' + fmt + '…', 'info');
      fetch('/api/research/' + encodeURIComponent(currentId) + '/export', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: fmt }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Export failed: ' + data.error, 'error'); return; }
          toast('Exported: ' + (data.filename || fmt), 'success');
          // Trigger a file download so the user actually gets the file.
          if (data.path) {
            var a = document.createElement('a');
            a.href = '/api/research/download?path=' + encodeURIComponent(data.path);
            a.download = data.filename || 'research.' + fmt;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          }
        })
        .catch(function () { toast('Export request failed', 'error'); });
    },

    compare: function () {
      var a = $('research-cmp-a').value;
      var b = $('research-cmp-b').value;
      if (!a || !b) { toast('Pick two runs', 'error'); return; }
      $('research-cmp-result').innerHTML = '<div style="padding:1rem;color:var(--text-muted);">' + esc(i18n('research.comparing')) + '</div>';
      fetch('/api/research/compare', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ a: a, b: b }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { $('research-cmp-result').innerHTML = '<div style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
          var d = data.diff;
          function arrow(v) { return v > 0 ? '+' + v : String(v); }
          function row(label, a, b, delta) {
            return '<tr><td style="padding:8px;border-bottom:1px solid var(--border);">' + esc(label) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(a) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(b) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);font-weight:600;">' + esc(delta) + '</td></tr>';
          }
          var html = '<table class="data-table" style="width:100%;border-collapse:collapse;font-size:0.9rem;">' +
            '<thead><tr><th style="text-align:left;padding:8px;">' + esc(i18n('research.metric')) + '</th>' +
            '<th style="text-align:right;padding:8px;">' + esc(i18n('research.run_a')) + '</th>' +
            '<th style="text-align:right;padding:8px;">' + esc(i18n('research.run_b')) + '</th>' +
            '<th style="text-align:right;padding:8px;">' + esc(i18n('research.delta')) + '</th></tr></thead><tbody>' +
            row('Cost (USD)', d.a_cost.toFixed(4), d.b_cost.toFixed(4), arrow(d.cost_delta.toFixed(4))) +
            row('Tokens', d.a_tokens, d.b_tokens, arrow(d.token_delta)) +
            row('Duration (s)', d.a_duration.toFixed(1), d.b_duration.toFixed(1), arrow(d.duration_delta.toFixed(1))) +
            row('Workers', d.a_worker_count, d.b_worker_count, arrow(d.worker_count_delta)) +
            row('Output length', d.a_output_length, d.b_output_length, arrow(d.output_length_delta)) +
            row('Output changed', '', '', d.output_changed ? 'yes' : 'no') +
            '</tbody></table>';
          if (d.output_diff) {
            html += '<h4 style="margin-top:1.5rem;">' + esc(i18n('research.text_diff')) + '</h4><pre style="background:rgba(0,0,0,0.1);padding:12px;border-radius:6px;font-size:0.8rem;overflow-x:auto;max-height:300px;">' + esc(d.output_diff) + '</pre>';
          }
          if (d.identical) html += '<p style="color:var(--success);">✅ ' + esc(i18n('research.identical')) + '</p>';
          $('research-cmp-result').innerHTML = html;
        })
        .catch(function () { toast('Compare failed', 'error'); });
    },
  };

  function renderList(tasks) {
    var el = $('research-list');
    if (!tasks.length) {
      el.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(i18n('research.no_results')) + '</div>';
      return;
    }
    el.innerHTML = tasks.map(function (t) {
      return '<div class="card" style="padding:12px 16px;cursor:pointer;" onclick="KazmaResearch.viewDetail(\'' + t.id + '\')">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;">' +
          '<div style="flex:1;min-width:0;">' +
            '<div style="font-weight:600;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(t.prompt || '(no prompt)') + '</div>' +
            '<div style="font-size:0.85rem;color:var(--text-muted);margin-top:4px;">' +
              '<span>' + esc((t.workers || []).join(', ')) + '</span> · ' +
              '<span>$' + (t.cost || 0).toFixed(4) + '</span> · ' +
              '<span>' + (t.duration || 0).toFixed(1) + 's</span> · ' +
              '<span>' + timeAgo(t.completed_at || t.created_at) + '</span>' +
            '</div>' +
          '</div>' +
          '<span style="font-size:0.75rem;color:var(--text-muted);background:var(--surface-2);padding:2px 8px;border-radius:4px;">' + esc(t.status) + '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function populateCompareDropdowns(tasks) {
    ['research-cmp-a', 'research-cmp-b'].forEach(function (id) {
      var dd = $(id);
      if (!dd) return;
      dd.innerHTML = '';
      tasks.forEach(function (t) {
        var opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = (t.prompt || '').slice(0, 60) + ' (' + timeAgo(t.completed_at) + ')';
        dd.appendChild(opt);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.KazmaResearch.init(); });
  } else {
    window.KazmaResearch.init();
  }
})();
