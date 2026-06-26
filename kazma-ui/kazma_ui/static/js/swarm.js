/* ═══════════════════════════════════════════════════════
   Kazma Swarm — Worker management & task dispatch UI
   Real-time monitoring for multi-agent swarm
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';
  var KS = window.KazmaStream;
  var pollInterval = null;
  var workers = [];
  var logs = [];
  var selectedWorker = null;

  function $(id) { return document.getElementById(id); }

  // ── Initialize ────────────────────────────────────────
  function init() {
    // Load models/providers for dropdowns
    fetchModels();

    // Load worker list
    refreshStatus();

    // Start polling for real-time updates
    pollInterval = setInterval(refreshStatus, 5000);

    // Event delegation
    var dispatchForm = $('dispatch-form');
    if (dispatchForm) {
      dispatchForm.addEventListener('submit', function(e) {
        e.preventDefault();
        dispatchTask();
      });
    }

    var addForm = $('add-worker-form');
    if (addForm) {
      addForm.addEventListener('submit', function(e) {
        e.preventDefault();
        addWorker();
      });
    }

    // Start/Stop buttons
    var startBtn = $('swarm-start');
    var stopBtn = $('swarm-stop');
    if (startBtn) startBtn.addEventListener('click', swarmAction.bind(null, 'start'));
    if (stopBtn) stopBtn.addEventListener('click', swarmAction.bind(null, 'stop'));

    // Worker table click delegation
    var workerList = $('worker-list-body');
    if (workerList) {
      workerList.addEventListener('click', function(e) {
        var btn = e.target.closest('button');
        if (!btn) return;
        var workerName = btn.dataset.worker;
        if (btn.dataset.action === 'remove') removeWorker(workerName);
        else if (btn.dataset.action === 'logs') viewLogs(workerName);
        else if (btn.dataset.action === 'config') configWorker(workerName);
      });
    }
  }

  // ── Data Fetching ─────────────────────────────────────
  function fetchModels() {
    fetch('/api/swarm/models')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        populateSelect('add-model', data.models || []);
        populateSelect('add-provider', data.providers || []);
      })
      .catch(function() {});
  }

  function refreshStatus() {
    fetch('/api/swarm/status')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        workers = data.workers || [];
        updateWorkerTable();
        updateSwarmControls(data.started, data.count);
        updateMetrics(data);

        // Show setup banner if needed
        var banner = $('setup-banner');
        if (banner && data.setup_instructions) {
          banner.style.display = 'block';
          banner.querySelector('.setup-text').textContent = data.setup_instructions;
        } else if (banner) {
          banner.style.display = 'none';
        }
      })
      .catch(function(err) {
        console.error('Swarm status fetch failed:', err);
        showError('Failed to fetch swarm status');
      });
  }

  // ── Worker Table ─────────────────────────────────────
  function updateWorkerTable() {
    var tbody = $('worker-list-body');
    if (!tbody) return;

    if (workers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="padding:40px;text-align:center;color:var(--text-muted);">' +
        '<div style="font-size:2rem;margin-bottom:8px;">🐝</div>' +
        '<p>No workers registered</p>' +
        '<p style="font-size:0.8rem;color:var(--text-tertiary);">Add a worker using the form on the right</p>' +
      '</td></tr>';
      return;
    }

    tbody.innerHTML = workers.map(function(w) {
      var statusColors = {
        online: { color: 'var(--success)', bg: 'rgba(16,185,129,0.10)' },
        offline: { color: 'var(--danger)', bg: 'rgba(248,81,73,0.10)' },
        busy: { color: 'var(--warning)', bg: 'rgba(210,153,34,0.10)' },
      };
      var sc = statusColors[w.status] || statusColors.offline;

      return '<tr class="worker-row" data-worker="' + esc(w.name) + '">' +
        '<td style="padding:10px 16px;">' +
          '<div style="display:flex;align-items:center;gap:8px;">' +
            '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + sc.color + ';"></span>' +
            '<span style="font-weight:500;">' + esc(w.name) + '</span>' +
          '</div>' +
        '</td>' +
        '<td style="padding:10px 16px;font-family:var(--font-mono);font-size:0.8rem;">' + esc(w.model || '?') + '</td>' +
        '<td style="padding:10px 16px;">' +
          '<span style="font-size:0.75rem;padding:2px 8px;border-radius:10px;background:' + sc.bg + ';color:' + sc.color + ';">' +
            '● ' + w.status +
          '</span>' +
        '</td>' +
        '<td style="padding:10px 16px;font-size:0.8rem;color:var(--text-tertiary);">' + esc(w.provider || '?') + '</td>' +
        '<td style="padding:10px 16px;font-size:0.8rem;color:var(--text-tertiary);">' + esc(w.type || '?') + '</td>' +
        '<td style="padding:10px 16px;text-align:right;">' +
          '<button data-action="logs" data-worker="' + esc(w.name) + '" class="btn btn-sm btn-secondary" style="margin-right:4px;" title="View logs">📋</button>' +
          '<button data-action="remove" data-worker="' + esc(w.name) + '" class="btn btn-sm btn-danger" title="Remove worker">✕</button>' +
        '</td>' +
      '</tr>';
    }).join('');
  }

  function updateSwarmControls(started, count) {
    var statusEl = $('swarm-status-text');
    var startBtn = $('swarm-start');
    var stopBtn = $('swarm-stop');

    if (statusEl) {
      if (started) {
        statusEl.innerHTML = '<span style="color:var(--success);">● Swarm running — ' + count + ' worker(s) active</span>';
      } else {
        statusEl.innerHTML = '<span style="color:var(--text-muted);">● Swarm stopped — ' + count + ' worker(s) registered</span>';
      }
    }
    if (startBtn) startBtn.disabled = started;
    if (stopBtn) stopBtn.disabled = !started;
  }

  function updateMetrics(data) {
    setText('metric-worker-count', String(data.count || 0));
    setText('metric-swarm-status', data.started ? 'Running' : 'Stopped');
    var statusColor = $('metric-swarm-status');
    if (statusColor) statusColor.style.color = data.started ? 'var(--success)' : 'var(--text-muted)';

    // Count busy workers
    var busy = workers.filter(function(w) { return w.status === 'busy'; }).length;
    setText('metric-busy', String(busy));
  }

  // ── Worker Actions ────────────────────────────────────
  function addWorker() {
    var name = ($('add-name') || {}).value || '';
    var model = ($('add-model') || {}).value || 'deepseek-chat';
    var provider = ($('add-provider') || {}).value || 'deepseek';
    var type = ($('add-type') || {}).value || 'in-process';
    var token = ($('add-token') || {}).value || '';

    if (!name.trim()) {
      showError('Worker name is required');
      return;
    }

    var payload = { name: name.trim(), model: model, provider: provider, type: type };
    if (token) payload.bot_token = token;

    fetch('/api/swarm/workers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Failed to add worker'); });
        return r.json();
      })
      .then(function() {
        showToast('Worker "' + name + '" added', true);
        // Clear form
        var nameEl = $('add-name'); if (nameEl) nameEl.value = '';
        var tokenEl = $('add-token'); if (tokenEl) tokenEl.value = '';
        refreshStatus();
      })
      .catch(function(err) {
        showError(err.message);
      });
  }

  function removeWorker(name) {
    if (!confirm('Remove worker "' + name + '" from the swarm? This cannot be undone.')) return;

    fetch('/api/swarm/workers/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Failed to remove worker'); });
        return r.json();
      })
      .then(function() {
        showToast('Worker "' + name + '" removed', true);
        refreshStatus();
      })
      .catch(function(err) {
        showError(err.message);
      });
  }

  function swarmAction(action) {
    fetch('/api/swarm/' + action, { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'warning') {
          showToast(data.message, true);
        } else if (data.status === 'ok') {
          showToast(data.message, true);
        } else {
          showError(data.message || 'Action failed');
        }
        refreshStatus();
      })
      .catch(function(err) {
        showError(err.message);
      });
  }

  // ── Task Dispatch ─────────────────────────────────────
  function dispatchTask() {
    var workersInput = ($('dispatch-workers') || {}).value || '';
    var task = ($('dispatch-task') || {}).value || '';
    var context = ($('dispatch-context') || {}).value || '';

    if (!workersInput.trim()) { showError('Specify at least one worker'); return; }
    if (!task.trim()) { showError('Task description is required'); return; }

    var workerList = workersInput.split(',').map(function(w) { return w.trim(); }).filter(Boolean);

    fetch('/api/swarm/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workers: workerList, task: task, context: context })
    })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok' || data.status === 'warning') {
          var msg = 'Task dispatched to ' + (data.dispatched || []).length + ' worker(s)';
          if (data.missing && data.missing.length) {
            msg += ' (missing: ' + data.missing.join(', ') + ')';
          }
          showToast(msg, true);
          // Clear form
          var taskEl = $('dispatch-task'); if (taskEl) taskEl.value = '';
          var ctxEl = $('dispatch-context'); if (ctxEl) ctxEl.value = '';
        } else {
          showError(data.message || 'Dispatch failed');
        }
        refreshStatus();
      })
      .catch(function(err) {
        showError(err.message);
      });
  }

  // ── Logs Viewer ───────────────────────────────────────
  function viewLogs(workerName) {
    selectedWorker = workerName;
    var modal = $('logs-modal');
    if (!modal) return;

    var titleEl = $('logs-worker-name');
    var contentEl = $('logs-content');

    if (titleEl) titleEl.textContent = 'Logs: ' + workerName;
    if (contentEl) {
      contentEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">' +
        '<div class="skeleton" style="height:16px;margin-bottom:8px;"></div>' +
        '<div class="skeleton" style="height:16px;margin-bottom:8px;"></div>' +
        '<div class="skeleton" style="height:16px;"></div>' +
        '</div>';
    }

    modal.style.display = 'flex';

    // Fetch logs
    fetch('/api/swarm/workers/' + encodeURIComponent(workerName) + '/logs')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (contentEl) {
          var lines = data.logs || data.lines || [];
          if (lines.length === 0) {
            contentEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">No logs yet</div>';
          } else {
            contentEl.innerHTML = lines.map(function(l) {
              return '<div style="font-family:var(--font-mono);font-size:0.75rem;padding:2px 0;border-bottom:1px solid var(--border-subtle);">' +
                esc(String(l)) + '</div>';
            }).join('');
          }
        }
      })
      .catch(function() {
        if (contentEl) {
          contentEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">Failed to load logs</div>';
        }
      });
  }

  function closeLogs() {
    var modal = $('logs-modal');
    if (modal) modal.style.display = 'none';
  }

  function configWorker(name) {
    showToast('Worker configuration for "' + name + '" — coming soon', true);
  }

  // ── UI Helpers ────────────────────────────────────────
  function populateSelect(id, options) {
    var select = document.getElementById(id);
    if (!select) return;
    var currentValue = select.value;
    select.innerHTML = options.map(function(o) {
      return '<option value="' + esc(o) + '">' + esc(o) + '</option>';
    }).join('');
    if (currentValue && options.indexOf(currentValue) >= 0) select.value = currentValue;
  }

  function showToast(msg, ok) {
    KS.toast(msg, ok ? 'success' : 'error', 4000);
  }

  function showError(msg) {
    KS.toast(msg, 'error', 5000);
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Boot ──────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Role presets
  var rolePresets = {
    orchestrator: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'Supervisor that coordinates other workers'
    },
    observer: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'Monitors and reports on agent activity'
    },
    backend: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'Code execution, tools, and backend tasks'
    },
    frontend: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'UI/UX, adapters, and gateway work'
    },
    researcher: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'Research, analysis, and documentation'
    },
    reviewer: {
      model: 'deepseek-chat',
      provider: 'deepseek',
      description: 'Code review and quality assurance'
    }
  };

  function applyRole(role) {
    var preset = rolePresets[role];
    if (!preset) return;
    document.getElementById('add-model').value = preset.model;
    document.getElementById('add-provider').value = preset.provider;
    showToast('Applied ' + role + ' preset', true);
  }

  // Expose for onclick handlers
  window.KazmaSwarm = {
    refresh: refreshStatus,
    addWorker: addWorker,
    removeWorker: removeWorker,
    dispatch: dispatchTask,
    start: function() { swarmAction('start'); },
    stop: function() { swarmAction('stop'); },
    viewLogs: viewLogs,
    closeLogs: closeLogs,
    applyRole: applyRole,
  };
})();
