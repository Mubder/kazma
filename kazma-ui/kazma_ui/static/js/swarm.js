/* ═══════════════════════════════════════════════════════
   Kazma Swarm — Full Orchestration UI
   Task Builder, Active Tasks (SSE), Results Dashboard,
   Worker Registry, Task History
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';
  var KS = window.KazmaStream || {};
  var pollInterval = null;
  var workers = [];
  var activeTasks = {};        // task_id -> {sse, events, data}
  var completedResults = [];   // array of task result objects
  var historyPage = 1;
  var historyPageSize = 20;
  var historyTotal = 0;
  var historyData = [];
  var modelOptions = {
    models: [],
    providers: [],
    providerEntries: [],
    profiles: [],
    defaults: {}
  };

  function $(id) { return document.getElementById(id); }
  function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function setText(id, text) { var el = $(id); if (el) el.textContent = text; }
  function showToast(msg, ok) { if (KS && KS.toast) KS.toast(msg, ok ? 'success' : 'error', 4000); }
  function showError(msg) { if (KS && KS.toast) KS.toast(msg, 'error', 5000); }

  // ── Pattern hints for worker selection ─────────────────
  var patternHints = {
    dispatch: 'Select a single worker for dispatch',
    broadcast: 'All registered workers will be targeted (selection optional)',
    pipeline: 'Select workers in execution order: first → middle → last',
    fan_out: 'Select workers for parallel execution',
    consult: 'Select workers to provide independent opinions',
    conditional: 'Select a router worker and destination workers'
  };

  // ══════════════════════════════════════════════════════
  // INITIALIZATION
  // ══════════════════════════════════════════════════════

  function init() {
    fetchModels();
    refreshStatus();
    pollInterval = setInterval(refreshStatus, 5000);
    loadOutputTarget();
    loadActiveTasks();

    // Form submissions
    var dispatchForm = $('dispatch-form');
    if (dispatchForm) dispatchForm.addEventListener('submit', function(e) { e.preventDefault(); dispatchTask(); });
    var addForm = $('add-worker-form');
    if (addForm) addForm.addEventListener('submit', function(e) { e.preventDefault(); addWorker(); });
    var addProfile = $('add-profile');
    if (addProfile) {
      addProfile.addEventListener('change', function(e) { applySavedProfile('add', e.target.value); });
    }

    // Start/Stop buttons
    var startBtn = $('swarm-start');
    var stopBtn = $('swarm-stop');
    if (startBtn) startBtn.addEventListener('click', function() { swarmAction('start'); });
    if (stopBtn) stopBtn.addEventListener('click', function() { swarmAction('stop'); });

    // Event delegation for worker actions (cards and tables)
    document.addEventListener('click', function(e) {
      var btn = e.target.closest('button[data-action]');
      if (!btn) return;
      var workerName = btn.dataset.worker;
      if (btn.dataset.action === 'remove') removeWorker(workerName);
      else if (btn.dataset.action === 'edit') editWorker(workerName);
      else if (btn.dataset.action === 'logs') viewLogs(workerName);
      else if (btn.dataset.action === 'start') startWorker(workerName);
      else if (btn.dataset.action === 'stop') stopWorker(workerName);
      else if (btn.dataset.action === 'approve') approveCheckpoint(btn.dataset.taskId);
      else if (btn.dataset.action === 'reject') rejectCheckpoint(btn.dataset.taskId);
      else if (btn.dataset.action === 'cancel') cancelTask(btn.dataset.taskId);
      else if (btn.dataset.action === 'retry') retryTask(btn.dataset.taskId);
      else if (btn.dataset.action === 'view-task') viewTaskDetail(btn.dataset.taskId);
    });
  }

  // ══════════════════════════════════════════════════════
  // TAB NAVIGATION
  // ══════════════════════════════════════════════════════

  function switchTab(tabId) {
    // Update tab buttons
    var tabs = document.querySelectorAll('#swarm-tabs .tab');
    tabs.forEach(function(t) { t.classList.toggle('active', t.dataset.tab === tabId); });
    // Update panels
    var panels = document.querySelectorAll('.tab-panel');
    panels.forEach(function(p) { p.style.display = 'none'; });
    var panel = $('panel-' + tabId);
    if (panel) panel.style.display = 'block';
    // Load data when switching to certain tabs
    if (tabId === 'task-history') loadTaskHistory();
    if (tabId === 'results-dashboard') loadResultsDashboard();
    if (tabId === 'worker-registry') loadWorkerMetrics();
    if (tabId === 'active-tasks') {
      loadActiveTasks();
      // Poll for updates while the tab is visible
      if (window._activeTasksPoll) clearInterval(window._activeTasksPoll);
      window._activeTasksPoll = setInterval(loadActiveTasks, 2000);
    } else {
      if (window._activeTasksPoll) { clearInterval(window._activeTasksPoll); window._activeTasksPoll = null; }
    }
  }

  // ══════════════════════════════════════════════════════
  // DATA FETCHING
  // ══════════════════════════════════════════════════════

  function fetchModels() {
    fetch('/api/swarm/models')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data || typeof data !== 'object') data = {};
        modelOptions.models = Array.isArray(data.models) ? data.models : [];
        modelOptions.providers = Array.isArray(data.providers) ? data.providers : [];
        modelOptions.providerEntries = Array.isArray(data.provider_entries) ? data.provider_entries : [];
        modelOptions.profiles = Array.isArray(data.profiles) ? data.profiles : [];
        modelOptions.defaults = data.defaults && typeof data.defaults === 'object' ? data.defaults : {};
        populateSwarmModelSelects();
      })
      .catch(function() {
        populateSwarmModelSelects();
      });
  }

  // Provider -> models map fetched from /api/providers (authoritative)
  var providerModelMap = []; // [{ name, label, models: [] }]

  function populateSwarmModelSelects() {
    // Fetch the authoritative provider list with discovered models
    fetch('/api/providers')
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(providers) {
        providerModelMap = [];
        if (Array.isArray(providers)) {
          providers.forEach(function(p) {
            var models = [];
            var visible = p.visible_models || [];
            var disc = p.discovered_models || [];
            var manual = p.models || [];
            if (Array.isArray(visible) && visible.length) {
              visible.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
            } else {
              if (Array.isArray(disc)) disc.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
              if (Array.isArray(manual)) manual.forEach(function(m) { if (m && models.indexOf(m) === -1) models.push(m); });
            }
            if (models.length > 0) {
              providerModelMap.push({ name: p.name, label: p.display_name || p.name, models: models });
            }
          });
        }
        renderSwarmModelSelect('add-model-select', defaultModelOption());
        renderSwarmModelSelect('edit-worker-model', '');
      })
      .catch(function() {
        renderSwarmModelSelect('add-model-select', defaultModelOption());
      });
  }

  function renderSwarmModelSelect(selectId, currentValue) {
    var sel = $(selectId);
    if (!sel) return;
    if (providerModelMap.length === 0) {
      sel.innerHTML = '<option value="">— no models available —</option>';
      return;
    }
    var html = '';
    providerModelMap.forEach(function(g) {
      html += '<optgroup label="' + esc(g.label) + '">';
      g.models.forEach(function(m) {
        var selAttr = (m === currentValue) ? ' selected' : '';
        html += '<option value="' + esc(m) + '"' + selAttr + '>' + esc(m) + '</option>';
      });
      html += '</optgroup>';
    });
    sel.innerHTML = html;
    if (currentValue) sel.value = currentValue;
  }

  function providerForModel(modelId) {
    if (!modelId) return '';
    for (var i = 0; i < providerModelMap.length; i++) {
      if (providerModelMap[i].models.indexOf(modelId) !== -1) return providerModelMap[i].name;
    }
    return '';
  }

  function defaultModelOption() {
    var llmModel = (((modelOptions || {}).defaults || {}).llm_model) || '';
    if (llmModel) return llmModel;
    if (modelOptions.models.length) return modelOptions.models[0];
    return 'deepseek-chat';
  }

  function defaultProviderOption() {
    if (modelOptions.providers.length) return modelOptions.providers[0];
    return 'deepseek';
  }

  function profileOptionLabel(profile) {
    if (!profile || typeof profile !== 'object') return '';
    var name = String(profile.name || '').trim();
    var provider = String(profile.provider || '').trim();
    var model = String(profile.model || '').trim();
    if (!name) return '';
    if (provider && model) return name + ' (' + provider + ' / ' + model + ')';
    if (model) return name + ' (' + model + ')';
    if (provider) return name + ' (' + provider + ')';
    return name;
  }

  function findProfileByName(profileName) {
    var target = String(profileName || '').trim();
    if (!target) return null;
    for (var i = 0; i < modelOptions.profiles.length; i++) {
      var profile = modelOptions.profiles[i] || {};
      if (String(profile.name || '').trim() === target) return profile;
    }
    return null;
  }

  function providerDisplayName(providerName) {
    var name = providerName || '';
    for (var i = 0; i < modelOptions.providerEntries.length; i++) {
      var entry = modelOptions.providerEntries[i] || {};
      if (entry.name === name && entry.display_name) {
        return String(entry.display_name);
      }
    }
    return name;
  }

  function ensureProviderOption(select, providerName) {
    if (!select) return;
    var cleanName = String(providerName || '').trim();
    if (!cleanName) return;
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === cleanName) return;
    }
    var option = document.createElement('option');
    option.value = cleanName;
    option.textContent = providerDisplayName(cleanName) || cleanName;
    select.appendChild(option);
  }

  function populateProviderSelect(selectId) {
    var select = $(selectId);
    if (!select) return;

    var providers = modelOptions.providers || [];
    if (!providers.length) {
      select.innerHTML = '<option value="">' + esc(defaultProviderOption()) + '</option>';
      return;
    }

    select.innerHTML = providers.map(function(providerName) {
      var label = providerDisplayName(providerName);
      return '<option value="' + esc(providerName) + '">' + esc(label) + '</option>';
    }).join('');
  }

  function refreshStatus() {
    fetch('/api/swarm/status')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        workers = data.workers || [];
        updateSwarmControls(data.started, data.count);
        updateMetrics(data);
        updateBreakerBadges(workers);
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
      });
  }

  function updateSwarmControls(started, count) {
    var statusEl = $('swarm-status-text');
    var startBtn = $('swarm-start');
    var stopBtn = $('swarm-stop');
    if (statusEl) {
      statusEl.innerHTML = started
        ? '<span style="color:var(--success);">● Swarm running — ' + count + ' worker(s) active</span>'
        : '<span style="color:var(--text-muted);">● Swarm stopped — ' + count + ' worker(s) registered</span>';
    }
    if (startBtn) startBtn.disabled = started;
    if (stopBtn) stopBtn.disabled = !started;
  }

  function updateMetrics(data) {
    setText('metric-worker-count', String(data.count || 0));
    setText('metric-swarm-status', data.started ? 'Running' : 'Stopped');
    var sc = $('metric-swarm-status');
    if (sc) sc.style.color = data.started ? 'var(--success)' : 'var(--text-muted)';
    var busy = workers.filter(function(w) { return w.status === 'busy'; }).length;
    setText('metric-busy', String(busy));
    // Fetch aggregated metrics for tasks-today and total-cost
    fetch('/api/swarm/workers/metrics/all')
      .then(function(r) { return r.ok ? r.json() : {}; })
      .then(function(metricsData) {
        var metrics = metricsData.metrics || [];
        var tasksToday = 0;
        var totalCost = 0;
        metrics.forEach(function(m) {
          tasksToday += (m.tasks_completed || 0) + (m.tasks_failed || 0);
          totalCost += parseFloat(m.total_cost || 0);
        });
        setText('metric-tasks-today', String(tasksToday));
        setText('metric-total-cost', '$' + totalCost.toFixed(2));
      })
      .catch(function() {});
  }

  function updateBreakerBadge(worker) {
    var cb = worker.circuit_breaker;
    var badge = $('cb-badge-' + worker.name);
    if (!badge) return;
    if (!cb || cb.state === 'closed') {
      badge.style.display = 'none';
      return;
    }
    badge.style.display = '';
    badge.className = 'badge ' + (cb.state === 'open' ? 'badge-danger' : 'badge-warning');
    badge.style.fontSize = '0.65rem';
    badge.textContent = '⚡ ' + cb.state;
    badge.title = 'Failures: ' + (cb.consecutive_failures || 0) + '/' + (cb.failure_threshold || 5);
  }

  function updateBreakerBadges(workerList) {
    (workerList || []).forEach(updateBreakerBadge);
  }

  // ══════════════════════════════════════════════════════
  // ORCHESTRATION PATTERN CHANGE (VAL-UI-001, VAL-ORCH-042)
  // ══════════════════════════════════════════════════════

  function onPatternChange(pattern) {
    // Update hint text
    var hint = $('pattern-hint');
    if (hint) hint.textContent = patternHints[pattern] || '';

    // Show/hide conditional routes
    var condGroup = $('conditional-routes-group');
    if (condGroup) condGroup.style.display = pattern === 'conditional' ? 'block' : 'none';

    // Update aggregation default for consult/fan_out
    var aggSelect = $('adv-aggregation');
    if (aggSelect) {
      if (pattern === 'consult') aggSelect.value = 'synthesize';
      else if (pattern === 'fan_out') aggSelect.value = 'collect';
      else aggSelect.value = 'collect';
    }
  }

  // ══════════════════════════════════════════════════════
  // TASK DISPATCH (VAL-UI-003, VAL-UI-004, VAL-UI-005, VAL-ORCH-044)
  // ══════════════════════════════════════════════════════

  function dispatchTask() {
    // Gather selected workers from checkboxes
    var checkboxes = document.querySelectorAll('input[name="selected_workers"]:checked');
    var workerList = [];
    checkboxes.forEach(function(cb) { workerList.push(cb.value); });

    var pattern = ($('pattern-select') || {}).value || 'dispatch';
    var task = ($('dispatch-task') || {}).value || '';
    var context = ($('dispatch-context') || {}).value || '';

    // Client-side validation: empty prompt blocked (VAL-ORCH-044)
    if (!task.trim()) {
      showError('Task prompt is required');
      return;
    }
    if (!workerList.length && pattern !== 'broadcast') {
      showError('Select at least one worker');
      return;
    }

    // Build payload
    var payload = {
      workers: workerList,
      task: task,
      context: context,
      pattern: pattern,
      timeout: parseFloat(($('adv-timeout') || {}).value) || 300,
      aggregation: ($('adv-aggregation') || {}).value || 'collect',
      max_retries: parseInt(($('adv-retries') || {}).value, 10) || 0,
    };

    // Add validation schema if provided
    var validationStr = ($('adv-validation') || {}).value || '';
    if (validationStr.trim()) {
      try {
        payload.validation_schema = JSON.parse(validationStr);
      } catch (e) { /* ignore parse errors, server will handle */ }
    }

    // Add conditional routes if applicable
    if (pattern === 'conditional') {
      var routesStr = ($('conditional-routes') || {}).value || '';
      if (routesStr.trim()) {
        try {
          payload.metadata = { routes: JSON.parse(routesStr) };
        } catch (e) {
          showError('Invalid routes JSON');
          return;
        }
      }
    }

    // Show pending state in the builder results preview
    appendPendingResult(workerList, task, pattern);

    // Disable dispatch button to prevent double-submit
    var dispatchBtn = $('btn-dispatch');
    if (dispatchBtn) { dispatchBtn.disabled = true; dispatchBtn.textContent = 'Dispatching...'; }

    // Show a pending card immediately (without SSE) so the user sees activity.
    var pendingTaskId = 'pending-' + Date.now();
    var pendingData = {
      task: task,
      pattern: pattern,
      workers: workerList,
      background: true,
    };
    // Create the card without connecting SSE yet — we'll connect after
    // the POST returns the real task_id.
    appendPendingResult(workerList, task, pattern);
    var pendingCard = createActiveTaskCard(pendingTaskId, pendingData);
    switchTab('active-tasks');

    fetch('/api/swarm/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok' || data.status === 'warning') {
          var dispatchedCount = (data.dispatched || []).length;
          showToast('Task dispatched to ' + dispatchedCount + ' worker(s)', true);

          // Connect SSE for live updates using the real task id.
          if (data.task_id) {
            upgradePendingTaskCard(pendingTaskId, data.task_id, data);
            connectSSE(data.task_id, data);
          }

          // Render results
          if (data.results && data.results.length) {
            renderTaskResults(data, task, pattern);
          }

          // Clear prompt
          var taskEl = $('dispatch-task'); if (taskEl) taskEl.value = '';
          var ctxEl = $('dispatch-context'); if (ctxEl) ctxEl.value = '';
        } else {
          // Dispatch failed: remove the optimistic card and notify the user.
          removeActiveTaskCard(pendingTaskId);
          showError(data.message || 'Dispatch failed');
        }
        refreshStatus();
      })
      .catch(function(err) {
        removeActiveTaskCard(pendingTaskId);
        showError(err.message);
      })
      .finally(function() {
        var btn = $('btn-dispatch');
        if (btn) { btn.disabled = false; btn.textContent = '⚡ Dispatch Task'; }
      });
  }

  // ══════════════════════════════════════════════════════
  // ACTIVE TASK CARD HELPERS
  // ══════════════════════════════════════════════════════

  function removeActiveTaskCard(taskId) {
    var card = $('active-task-' + taskId);
    if (card) card.remove();
    var state = activeTasks[taskId];
    if (state && state.timer) clearInterval(state.timer);
    if (state && state.sse) {
      try { state.sse.close(); } catch (e) {}
    }
    delete activeTasks[taskId];
    var listEl = $('active-tasks-list');
    var emptyEl = $('active-tasks-empty');
    if (listEl && emptyEl && !listEl.children.length) emptyEl.style.display = 'block';
  }

  function upgradePendingTaskCard(pendingTaskId, realTaskId, data) {
    if (pendingTaskId === realTaskId) return;
    var oldCard = $('active-task-' + pendingTaskId);
    if (!oldCard) return;
    oldCard.id = 'active-task-' + realTaskId;
    var statusEl = $('status-' + pendingTaskId);
    var timerEl = $('timer-' + pendingTaskId);
    var eventsEl = $('events-' + pendingTaskId);
    var checkpointEl = $('checkpoint-' + pendingTaskId);
    var handoffEl = $('handoff-' + pendingTaskId);
    if (statusEl) statusEl.id = 'status-' + realTaskId;
    if (timerEl) timerEl.id = 'timer-' + realTaskId;
    if (eventsEl) eventsEl.id = 'events-' + realTaskId;
    if (checkpointEl) checkpointEl.id = 'checkpoint-' + realTaskId;
    if (handoffEl) handoffEl.id = 'handoff-' + realTaskId;
    var oldState = activeTasks[pendingTaskId];
    if (oldState) {
      oldState.sse.close();
      clearInterval(oldState.timer);
      delete activeTasks[pendingTaskId];
    }
  }

  // ══════════════════════════════════════════════════════
  // SSE STREAMING (Active Tasks)
  // ══════════════════════════════════════════════════════

  function createActiveTaskCard(taskId, initialData) {
    var emptyEl = $('active-tasks-empty');
    if (emptyEl) emptyEl.style.display = 'none';
    var listEl = $('active-tasks-list');
    if (!listEl) return null;

    var cardId = 'active-task-' + taskId;
    // Don't create duplicate cards
    if ($(cardId)) return $(cardId);

    var card = document.createElement('div');
    card.className = 'card';
    card.id = cardId;
    card.style.marginBottom = '12px';
    card.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">' +
        '<div style="display:flex;align-items:center;gap:8px;">' +
          '<span style="font-weight:600;font-size:0.9rem;">' + esc(taskId.slice(0, 16)) + '…</span>' +
          '<span class="badge badge-warning" id="status-' + taskId + '">running</span>' +
        '</div>' +
        '<span style="font-size:0.75rem;color:var(--text-muted);" id="timer-' + taskId + '">0s</span>' +
      '</div>' +
      '<div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;">' + esc((initialData.task || '').slice(0, 100)) + '</div>' +
      '<div id="events-' + taskId + '" style="display:flex;flex-direction:column;gap:4px;"></div>' +
      '<!-- HITL Checkpoint area -->' +
      '<div id="checkpoint-' + taskId + '" style="display:none;margin-top:12px;padding:12px;border:1px solid var(--warning);border-radius:var(--radius);background:var(--warning-subtle);"></div>' +
      '<!-- Handoff chain area -->' +
      '<div id="handoff-' + taskId + '" style="display:none;margin-top:8px;font-size:0.8rem;"></div>';

    listEl.insertBefore(card, listEl.firstChild);
    return card;
  }

  function connectSSE(taskId, initialData) {
    // Create or reuse active task card
    var card = createActiveTaskCard(taskId, initialData);
    if (!card) return;

    // Start timer
    var startTime = Date.now();
    var timerInterval = setInterval(function() {
      var elapsed = Math.round((Date.now() - startTime) / 1000);
      setText('timer-' + taskId, elapsed + 's');
    }, 1000);

    // SSE connection
    var evtSource = new EventSource('/api/swarm/tasks/' + encodeURIComponent(taskId) + '/stream');
    activeTasks[taskId] = { sse: evtSource, events: [], timer: timerInterval };

    evtSource.addEventListener('task_started', function(e) {
      addEventLine(taskId, '🚀', 'Task started');
    });

    evtSource.addEventListener('worker_started', function(e) {
      var data = JSON.parse(e.data);
      addEventLine(taskId, '⚙️', 'Worker ' + esc(data.worker) + ' started (step ' + data.step + ')');
    });

    evtSource.addEventListener('worker_progress', function(e) {
      var data = JSON.parse(e.data);
      addEventLine(taskId, '📝', esc(data.worker) + ': ' + (data.tokens || 0) + ' tokens');
    });

    evtSource.addEventListener('worker_completed', function(e) {
      var data = JSON.parse(e.data);
      var icon = data.status === 'success' ? '✅' : '❌';
      addEventLine(taskId, icon, esc(data.worker) + ': ' + esc(data.status));
    });

    evtSource.addEventListener('checkpoint', function(e) {
      var data = JSON.parse(e.data);
      // HITL checkpoint (VAL-HITL-002)
      var cpEl = $('checkpoint-' + taskId);
      if (cpEl) {
        cpEl.style.display = 'block';
        cpEl.innerHTML =
          '<div style="font-weight:600;margin-bottom:8px;color:var(--warning);">⏸ HITL Checkpoint — Step ' + data.step + '</div>' +
          '<div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:8px;max-height:100px;overflow-y:auto;">' + esc(data.output_preview || 'No preview') + '</div>' +
          '<div style="display:flex;gap:8px;">' +
            '<button class="btn btn-primary btn-sm" data-action="approve" data-task-id="' + esc(taskId) + '">✓ Approve</button>' +
            '<button class="btn btn-danger btn-sm" data-action="reject" data-task-id="' + esc(taskId) + '">✕ Reject</button>' +
          '</div>';
      }
    });

    evtSource.addEventListener('handoff', function(e) {
      var data = JSON.parse(e.data);
      // Handoff chain visualization (VAL-HAND-007)
      var hfEl = $('handoff-' + taskId);
      if (hfEl) {
        hfEl.style.display = 'block';
        hfEl.innerHTML += '<span class="badge badge-info" style="margin-right:4px;">' + esc(data.from) + ' → ' + esc(data.to) + '</span>';
      }
    });

    evtSource.addEventListener('task_completed', function(e) {
      var data = JSON.parse(e.data);
      clearInterval(timerInterval);
      var statusEl = $('status-' + taskId);
      if (statusEl) {
        statusEl.textContent = data.result ? data.result.status : 'completed';
        statusEl.className = 'badge badge-' + ((data.result && data.result.status === 'success') ? 'success' : 'danger');
      }
      addEventLine(taskId, '🏁', 'Task completed');
      evtSource.close();
      // Keep the completed card visible for a few seconds so the user can see
      // it finished, then refresh the dashboard. The activeTasks entry is
      // removed without deleting the DOM card immediately.
      if (activeTasks[taskId]) {
        clearInterval(activeTasks[taskId].timer);
        delete activeTasks[taskId];
      }

      // Store result for dashboard
      if (data.result) {
        data.result._prompt = initialData.task || '';
        data.result._pattern = initialData.pattern || ($('pattern-select') || {}).value || 'dispatch';
        completedResults.unshift(data.result);
      }
      // Refresh the dashboard so the completed task appears immediately.
      if ($('panel-results-dashboard') && $('panel-results-dashboard').style.display === 'block') {
        loadResultsDashboard();
      }
      // Optionally prune the active card after a short delay so completed work
      // does not pile up indefinitely in the active panel.
      setTimeout(function() {
        var card = $('active-task-' + taskId);
        if (card) card.remove();
        var listEl = $('active-tasks-list');
        var emptyEl = $('active-tasks-empty');
        if (listEl && emptyEl && !listEl.children.length) emptyEl.style.display = 'block';
      }, 5000);
    });

    evtSource.onerror = function() {
      // SSE reconnect is handled automatically by EventSource
    };
  }

  function addEventLine(taskId, icon, text) {
    var eventsEl = $('events-' + taskId);
    if (!eventsEl) return;
    var line = document.createElement('div');
    line.style.cssText = 'font-size:0.8rem;color:var(--text-secondary);display:flex;align-items:center;gap:6px;';
    line.innerHTML = '<span>' + icon + '</span><span>' + text + '</span>';
    eventsEl.appendChild(line);
    eventsEl.scrollTop = eventsEl.scrollHeight;
  }

  // ══════════════════════════════════════════════════════
  // HITL CHECKPOINT (VAL-HITL-002)
  // ══════════════════════════════════════════════════════

  function approveCheckpoint(taskId) {
    fetch('/api/swarm/tasks/' + encodeURIComponent(taskId) + '/approve', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        showToast('Checkpoint approved, pipeline resuming', true);
        var cpEl = $('checkpoint-' + taskId);
        if (cpEl) cpEl.style.display = 'none';
      })
      .catch(function(err) { showError('Approve failed: ' + err.message); });
  }

  function rejectCheckpoint(taskId) {
    fetch('/api/swarm/tasks/' + encodeURIComponent(taskId) + '/reject', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        showToast('Checkpoint rejected, pipeline aborted', false);
        var cpEl = $('checkpoint-' + taskId);
        if (cpEl) cpEl.style.display = 'none';
      })
      .catch(function(err) { showError('Reject failed: ' + err.message); });
  }

  function cancelTask(taskId) {
    fetch('/api/swarm/tasks/' + encodeURIComponent(taskId) + '/cancel', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          showToast('Task cancelled', true);
          closeTaskDetail();
          loadActiveTasks();
        } else {
          showError(data.message || 'Failed to cancel task');
        }
      })
      .catch(function(err) { showError('Cancel failed: ' + err.message); });
  }

  function retryTask(taskId) {
    fetch('/api/swarm/tasks/' + encodeURIComponent(taskId) + '/retry', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          showToast('Task retried as ' + data.new_task_id, true);
          closeTaskDetail();
          loadActiveTasks();
        } else {
          showError(data.message || 'Failed to retry task');
        }
      })
      .catch(function(err) { showError('Retry failed: ' + err.message); });
  }

  // ══════════════════════════════════════════════════════
  // TASK RESULTS RENDERING
  // ══════════════════════════════════════════════════════

  function appendPendingResult(workerNames, task, pattern) {
    var emptyEl = $('task-results-empty');
    if (emptyEl) emptyEl.style.display = 'none';
    var listEl = $('task-results-list');
    if (!listEl) return;
    var card = document.createElement('div');
    card.className = 'task-result-card';
    card.style.cssText = 'padding:12px;margin-bottom:8px;border:1px solid var(--border-subtle);border-radius:6px;background:rgba(255,255,255,0.02);';
    card.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">' +
        '<span style="font-weight:600;font-size:0.85rem;color:var(--accent);">' + esc(workerNames.join(', ')) + '</span>' +
        '<span class="badge badge-warning">● pending</span>' +
      '</div>' +
      '<div style="font-size:0.8rem;color:var(--text-muted);">' + esc(task.slice(0, 120)) + '</div>' +
      '<div class="task-result-output" style="font-size:0.75rem;color:var(--text-tertiary);margin-top:4px;">Waiting…</div>';
    listEl.insertBefore(card, listEl.firstChild);
  }

  function renderTaskResults(data, task, pattern) {
    var emptyEl = $('task-results-empty');
    if (emptyEl) emptyEl.style.display = 'none';
    var listEl = $('task-results-list');
    if (!listEl) return;
    // Remove pending card
    var pendingCard = listEl.querySelector('.task-result-card');
    if (pendingCard && pendingCard.textContent.indexOf('pending') >= 0) pendingCard.remove();

    var results = data.results || [];
    results.forEach(function(r) {
      var statusColor = r.status === 'success' ? 'var(--success)' : r.status === 'error' ? 'var(--danger)' : 'var(--warning)';
      var statusBg = r.status === 'success' ? 'var(--success-subtle)' : r.status === 'error' ? 'var(--danger-subtle)' : 'var(--warning-subtle)';
      var card = document.createElement('div');
      card.className = 'task-result-card';
      card.style.cssText = 'padding:12px;margin-bottom:8px;border:1px solid var(--border-subtle);border-radius:6px;background:rgba(255,255,255,0.02);';
      card.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">' +
          '<span style="font-weight:600;font-size:0.85rem;color:var(--accent);">' + esc(r.worker || '?') + '</span>' +
          '<span class="badge" style="background:' + statusBg + ';color:' + statusColor + ';">● ' + esc(r.status || '?') + '</span>' +
        '</div>' +
        (r.output ? '<div style="padding:8px;background:rgba(0,0,0,0.15);border-radius:4px;font-family:var(--font-mono);font-size:0.75rem;color:var(--text-secondary);white-space:pre-wrap;max-height:150px;overflow-y:auto;">' + esc(r.output.slice(0, 500)) + '</div>' : '') +
        (r.error ? '<div style="padding:8px;background:var(--danger-subtle);border-radius:4px;font-size:0.75rem;color:var(--danger);margin-top:4px;">⚠ ' + esc(r.error) + '</div>' : '');
      listEl.insertBefore(card, listEl.firstChild);
    });

    // Show synthesized output for consult
    if (data.synthesized_output) {
      var synthCard = document.createElement('div');
      synthCard.className = 'task-result-card';
      synthCard.style.cssText = 'padding:12px;margin-bottom:8px;border:1px solid var(--accent-subtle);border-radius:6px;background:var(--accent-subtle);';
      synthCard.innerHTML =
        '<div style="font-weight:600;font-size:0.85rem;color:var(--accent-light);margin-bottom:6px;">🧠 Synthesized Answer</div>' +
        '<div style="font-size:0.8rem;color:var(--text-secondary);white-space:pre-wrap;">' + esc(data.synthesized_output) + '</div>';
      listEl.insertBefore(synthCard, listEl.firstChild);
    }
  }

  // ══════════════════════════════════════════════════════
  // RESULTS DASHBOARD (Pattern-specific views)
  // ══════════════════════════════════════════════════════

  function loadResultsDashboard() {
    // Load recent completed tasks from API
    fetch('/api/swarm/tasks?pageSize=20')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var tasks = data.tasks || [];
        // Merge with in-memory results
        var allResults = completedResults.slice();
        tasks.forEach(function(t) {
          if (!allResults.find(function(r) { return r.task_id === t.id; })) {
            allResults.push(t);
          }
        });
        renderResultsDashboard(allResults, 'all');
      })
      .catch(function() {
        renderResultsDashboard(completedResults, 'all');
      });
  }

  function filterResults(type) {
    // Update sub-tab buttons
    document.querySelectorAll('[data-result-tab]').forEach(function(btn) {
      btn.classList.toggle('active', btn.dataset.resultTab === type);
    });
    loadResultsDashboard();
    // Filter will be applied in renderResultsDashboard after loading
    setTimeout(function() {
      var cards = document.querySelectorAll('#results-dashboard-list .result-card');
      cards.forEach(function(c) {
        if (type === 'all') { c.style.display = ''; return; }
        c.style.display = (c.dataset.pattern === type) ? '' : 'none';
      });
    }, 100);
  }

  function renderResultsDashboard(results, filterType) {
    var container = $('results-dashboard-list');
    var emptyEl = $('results-dashboard-empty');
    if (!container) return;

    if (!results.length) {
      if (emptyEl) emptyEl.style.display = 'block';
      container.innerHTML = '';
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';

    container.innerHTML = results.map(function(r) {
      var pattern = r.type || r._pattern || 'dispatch';
      var status = r.status || 'unknown';
      var statusColor = status === 'success' ? 'var(--success)' : status === 'failed' ? 'var(--danger)' : 'var(--warning)';
      var patternLabel = {dispatch:'🎯 Dispatch',broadcast:'📢 Broadcast',pipeline:'🔗 Pipeline',fan_out:'🌀 Fan-Out',consult:'💬 Consult',conditional:'🔀 Conditional'}[pattern] || pattern;

      var html = '<div class="card result-card" data-pattern="' + pattern + '" style="margin-bottom:12px;cursor:pointer;" onclick="KazmaSwarm.viewTaskDetail(\'' + esc(r.task_id || r.id) + '\')">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">';
      html += '<div style="display:flex;align-items:center;gap:8px;">';
      html += '<span style="font-weight:600;font-size:0.85rem;">' + patternLabel + '</span>';
      html += '<span class="badge" style="color:' + statusColor + ';">' + status + '</span>';
      html += '</div>';
      html += '<span style="font-size:0.75rem;color:var(--text-muted);">' + (r.duration_seconds ? r.duration_seconds.toFixed(1) + 's' : '') + '</span>';
      html += '</div>';

      // Pattern-specific rendering
      if (pattern === 'pipeline' && r.worker_results && r.worker_results.length) {
        // VAL-ORCH-002: Pipeline step view
        html += '<div style="display:flex;gap:4px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">';
        r.worker_results.forEach(function(wr, idx) {
          var stepColor = wr.status === 'success' ? 'var(--success)' : 'var(--danger)';
          html += '<span style="padding:4px 8px;border-radius:var(--radius-xs);background:rgba(255,255,255,0.04);font-size:0.75rem;border:1px solid var(--border-subtle);">';
          html += '<span style="color:' + stepColor + ';">' + esc(wr.worker) + '</span>';
          html += '</span>';
          if (idx < r.worker_results.length - 1) html += '<span style="color:var(--text-muted);">→</span>';
        });
        html += '</div>';
      } else if (pattern === 'fan_out' && r.worker_results && r.worker_results.length) {
        // VAL-ORCH-021: Fan-out per-worker cards
        html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px;margin-bottom:8px;">';
        r.worker_results.forEach(function(wr) {
          var wColor = wr.status === 'success' ? 'var(--success)' : 'var(--danger)';
          html += '<div style="padding:6px 8px;border-radius:var(--radius-xs);background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);font-size:0.75rem;">';
          html += '<span style="color:' + wColor + ';">●</span> ' + esc(wr.worker);
          html += '</div>';
        });
        html += '</div>';
      } else if (pattern === 'consult') {
        // VAL-ORCH-024, VAL-ORCH-025: Consult comparison view
        var opinions = r.individual_opinions || r.worker_results || [];
        if (opinions.length) {
          html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px;margin-bottom:8px;">';
          opinions.forEach(function(op) {
            html += '<div style="padding:6px 8px;border-radius:var(--radius-xs);background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);font-size:0.75rem;max-height:60px;overflow:hidden;">';
            html += '<div style="font-weight:500;color:var(--accent);margin-bottom:2px;">' + esc(op.worker) + '</div>';
            html += '<div style="color:var(--text-tertiary);">' + esc((op.output || '').slice(0, 80)) + '</div>';
            html += '</div>';
          });
          html += '</div>';
        }
        if (r.synthesized_output) {
          html += '<div style="padding:6px 8px;background:var(--accent-subtle);border-radius:var(--radius-xs);font-size:0.75rem;color:var(--accent-light);">';
          html += '🧠 ' + esc(r.synthesized_output.slice(0, 120));
          html += '</div>';
        }
      } else if (pattern === 'conditional' && r.metadata && r.metadata.route_taken) {
        // VAL-ORCH-034: Conditional routing decision
        html += '<div style="font-size:0.8rem;color:var(--info);margin-bottom:4px;">🔀 Routed to: <strong>' + esc(r.metadata.route_taken) + '</strong></div>';
      }

      // Aggregated output
      if (r.aggregated_output && pattern !== 'consult') {
        html += '<div style="font-size:0.75rem;color:var(--text-tertiary);margin-top:4px;max-height:40px;overflow:hidden;">' + esc(r.aggregated_output.slice(0, 150)) + '</div>';
      }

      html += '</div>';
      return html;
    }).join('');
  }

  // ══════════════════════════════════════════════════════
  // WORKER REGISTRY (VAL-UI-006, VAL-UI-007, VAL-UI-008, VAL-UI-009)
  // ══════════════════════════════════════════════════════

  function addWorker() {
    var name = ($('add-name') || {}).value || '';
    var modelSel = $('add-model-select');
    var model = modelSel ? modelSel.value : '';
    var type = 'in-process';
    var role = ($('add-role') || {}).value || '';
    var specialty = ($('add-specialty') || {}).value || '';
    var expertiseStr = ($('add-expertise') || {}).value || '';
    var toolsStr = ($('add-tools') || {}).value || '';
    var systemPrompt = ($('add-system-prompt') || {}).value || '';

    if (!name.trim()) { showError('Worker name is required'); return; }
    if (!model) { showError('Please select a model'); return; }

    var provider = providerForModel(model);
    var expertise = expertiseStr.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
    var tools = toolsStr.split(',').map(function(s) { return s.trim(); }).filter(Boolean);

    var payload = {
      name: name.trim(),
      model: model,
      provider: provider,
      type: type,
      role: role,
      system_prompt: systemPrompt,
      capabilities: {
        role: role,
        expertise: expertise,
        tools: tools,
        model_specialty: specialty,
      },
    };

    fetch('/api/swarm/workers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Failed'); });
        return r.json();
      })
      .then(function() {
        showToast('Worker "' + name + '" added', true);
        // Clear form fields
        ['add-name', 'add-expertise', 'add-tools', 'add-system-prompt'].forEach(function(id) {
          var el = $(id); if (el) el.value = '';
        });
        var roleEl = $('add-role'); if (roleEl) roleEl.value = '';
        var specEl = $('add-specialty'); if (specEl) specEl.value = '';
        // Refresh worker list without page reload
        refreshStatus();
      })
      .catch(function(err) { showError(err.message); });
  }

  function removeWorker(name) {
    if (!confirm('Remove worker "' + name + '" from the swarm?')) return;
    fetch('/api/swarm/workers/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Failed'); });
        return r.json();
      })
      .then(function() {
        showToast('Worker "' + name + '" removed', true);
        var card = $('worker-card-' + name);
        if (card) card.remove();
        refreshStatus();
      })
      .catch(function(err) { showError(err.message); });
  }

  function startWorker(name) {
    fetch('/api/swarm/workers/' + encodeURIComponent(name) + '/start', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.status === 'ok') { showToast('Worker "' + name + '" started', true); refreshStatus(); }
        else showError(d.message || 'Failed to start worker');
      })
      .catch(function() { showError('Failed to start worker'); });
  }

  function stopWorker(name) {
    fetch('/api/swarm/workers/' + encodeURIComponent(name) + '/stop', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.status === 'ok') { showToast('Worker "' + name + '" stopped', true); refreshStatus(); }
        else showError(d.message || 'Failed to stop worker');
      })
      .catch(function() { showError('Failed to stop worker'); });
  }

  function editWorker(name) {
    // Find the worker card to read current values
    var card = $('worker-card-' + name);
    if (!card) return;
    // Extract current model and role from the card's data attributes / text
    var modelEl = card.querySelector('[data-model]');
    var roleBadge = card.querySelector('.badge.badge-accent');
    var currentModel = modelEl ? modelEl.getAttribute('data-model') : '';
    var currentRole = roleBadge ? roleBadge.textContent.trim() : '';

    // Populate the edit modal
    var nameInput = $('edit-worker-name');
    if (nameInput) nameInput.value = name;
    var roleSelect = $('edit-worker-role');
    if (roleSelect) roleSelect.value = currentRole;
    // Render the model dropdown with the current model pre-selected
    renderSwarmModelSelect('edit-worker-model', currentModel);

    // Show the modal
    var modal = $('edit-worker-modal');
    if (modal) modal.style.display = 'flex';
  }

  function closeEditWorker() {
    var modal = $('edit-worker-modal');
    if (modal) modal.style.display = 'none';
  }

  function saveEditWorker() {
    var name = ($('edit-worker-name') || {}).value || '';
    var role = ($('edit-worker-role') || {}).value || '';
    var modelSel = $('edit-worker-model');
    var model = modelSel ? modelSel.value : '';
    if (!name) { showError('Worker name is missing'); return; }
    if (!model) { showError('Please select a model'); return; }
    var provider = providerForModel(model);

    var payload = { model: model, provider: provider };
    if (role) payload.role = role;

    fetch('/api/swarm/workers/' + encodeURIComponent(name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Failed'); });
        return r.json();
      })
      .then(function() {
        showToast('Worker "' + name + '" updated', true);
        closeEditWorker();
        refreshStatus();
      })
      .catch(function(err) { showError(err.message); });
  }

  function loadWorkerMetrics() {
    // VAL-UI-010: Per-worker metrics
    fetch('/api/swarm/workers/metrics/all')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var metrics = data.metrics || [];
        metrics.forEach(function(m) {
          var successEl = $('metric-success-' + m.worker);
          var latencyEl = $('metric-latency-' + m.worker);
          var costEl = $('metric-cost-' + m.worker);
          if (successEl) {
            var total = (m.tasks_completed || 0) + (m.tasks_failed || 0);
            var rate = total > 0 ? Math.round((m.tasks_completed / total) * 100) : 0;
            successEl.textContent = rate + '%';
          }
          if (latencyEl) latencyEl.textContent = m.avg_latency ? m.avg_latency.toFixed(1) + 's' : '—';
          if (costEl) costEl.textContent = m.total_cost ? '$' + m.total_cost.toFixed(4) : '—';
        });
      })
      .catch(function() {});
  }

  function applyRole(role) {
    var presets = {
      orchestrator: { model: defaultModelOption(), provider: defaultProviderOption() },
      observer: { model: defaultModelOption(), provider: defaultProviderOption() },
      backend: { model: defaultModelOption(), provider: defaultProviderOption() },
      frontend: { model: defaultModelOption(), provider: defaultProviderOption() },
      researcher: { model: defaultModelOption(), provider: defaultProviderOption() },
      reviewer: { model: defaultModelOption(), provider: defaultProviderOption() },
    };
    var p = presets[role];
    if (!p) return;
    var m = $('add-model'); if (m) m.value = p.model;
    var pr = $('add-provider'); if (pr) pr.value = p.provider;
    var profile = $('add-profile'); if (profile) profile.value = '';
  }

  // ══════════════════════════════════════════════════════
  // TASK HISTORY (VAL-ORCH-053)
  // ══════════════════════════════════════════════════════

  function loadTaskHistory() {
    var typeFilter = ($('history-filter-type') || {}).value || '';
    var statusFilter = ($('history-filter-status') || {}).value || '';
    var searchQuery = ($('history-search') || {}).value || '';

    var url = '/api/swarm/tasks?page=' + historyPage + '&pageSize=' + historyPageSize;
    if (typeFilter) url += '&type=' + encodeURIComponent(typeFilter);
    if (statusFilter) url += '&status=' + encodeURIComponent(statusFilter);

    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        historyData = data.tasks || [];
        historyTotal = data.total || historyData.length;
        var filtered = historyData;
        if (searchQuery) {
          var q = searchQuery.toLowerCase();
          filtered = historyData.filter(function(t) {
            return (t.id || '').toLowerCase().indexOf(q) >= 0 ||
                   (t.prompt || '').toLowerCase().indexOf(q) >= 0;
          });
        }
        renderHistoryTable(filtered);
        updateHistoryPagination();
      })
      .catch(function() {
        renderHistoryTable([]);
      });
  }

  function filterHistory() {
    historyPage = 1;
    loadTaskHistory();
  }

  function historyPrev() {
    if (historyPage > 1) { historyPage--; loadTaskHistory(); }
  }

  function historyNext() {
    var maxPage = Math.ceil(historyTotal / historyPageSize) || 1;
    if (historyPage < maxPage) { historyPage++; loadTaskHistory(); }
  }

  function updateHistoryPagination() {
    var maxPage = Math.ceil(historyTotal / historyPageSize) || 1;
    setText('history-count', historyTotal + ' tasks');
    setText('history-page-info', 'Page ' + historyPage + ' of ' + maxPage);
    var prevBtn = $('history-prev'); if (prevBtn) prevBtn.disabled = historyPage <= 1;
    var nextBtn = $('history-next'); if (nextBtn) nextBtn.disabled = historyPage >= maxPage;
  }

  function renderHistoryTable(tasks) {
    var tbody = $('history-table-body');
    if (!tbody) return;
    if (!tasks.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="padding:40px;text-align:center;color:var(--text-muted);">No tasks found</td></tr>';
      return;
    }

    var patternIcons = {dispatch:'🎯',broadcast:'📢',pipeline:'🔗',fan_out:'🌀',consult:'💬',conditional:'🔀'};
    tbody.innerHTML = tasks.map(function(t) {
      var icon = patternIcons[t.type] || '📋';
      var statusColor = t.status === 'completed' ? 'var(--success)' : (t.status === 'failed' || t.status === 'cancelled') ? 'var(--danger)' : 'var(--warning)';
      var prompt = (t.prompt || '').slice(0, 80);
      var workers = (t.workers || []).join(', ');
      var dur = t.duration_seconds ? t.duration_seconds.toFixed(1) + 's' : '—';
      var cost = t.total_cost ? '$' + t.total_cost.toFixed(4) : '—';

      return '<tr style="cursor:pointer;border-bottom:1px solid var(--border-subtle);" onclick="KazmaSwarm.viewTaskDetail(\'' + esc(t.id) + '\')">' +
        '<td style="padding:8px 12px;font-family:var(--font-mono);font-size:0.75rem;color:var(--text-tertiary);">' + esc((t.id || '').slice(0, 16)) + '</td>' +
        '<td style="padding:8px 12px;">' + icon + ' ' + esc(t.type || '?') + '</td>' +
        '<td style="padding:8px 12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(prompt) + '</td>' +
        '<td style="padding:8px 12px;font-size:0.8rem;">' + esc(workers) + '</td>' +
        '<td style="padding:8px 12px;"><span style="color:' + statusColor + ';">● ' + esc(t.status || '?') + '</span></td>' +
        '<td style="padding:8px 12px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;">' + dur + '</td>' +
        '<td style="padding:8px 12px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;">' + cost + '</td>' +
      '</tr>';
    }).join('');
  }

  // ══════════════════════════════════════════════════════
  // TASK DETAIL VIEW
  // ══════════════════════════════════════════════════════

  function viewTaskDetail(taskId) {
    var modal = $('task-detail-modal');
    var titleEl = $('task-detail-title');
    var bodyEl = $('task-detail-body');
    if (!modal || !bodyEl) return;

    if (titleEl) titleEl.textContent = 'Task: ' + taskId.slice(0, 20) + '…';
    bodyEl.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);">Loading…</div>';
    modal.style.display = 'flex';

    fetch('/api/swarm/tasks/' + encodeURIComponent(taskId))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var task = data.task;
        if (!task) { bodyEl.innerHTML = '<div style="padding:24px;color:var(--text-muted);">Task not found</div>'; return; }
        bodyEl.innerHTML = renderTaskDetailHTML(task);
      })
      .catch(function(err) {
        bodyEl.innerHTML = '<div style="padding:24px;color:var(--danger);">Error loading task: ' + esc(err.message) + '</div>';
      });
  }

  function closeTaskDetail(event) {
    if (event && event.target !== event.currentTarget) return;
    var modal = $('task-detail-modal');
    if (modal) modal.style.display = 'none';
  }

  function renderTaskDetailHTML(task) {
    var html = '';
    // Header info
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">';
    html += '<div><strong>Type:</strong> ' + esc(task.type) + '</div>';
    html += '<div><strong>Status:</strong> <span class="badge">' + esc(task.status) + '</span></div>';
    html += '<div><strong>Workers:</strong> ' + esc((task.workers || []).join(', ')) + '</div>';
    html += '<div><strong>Duration:</strong> ' + (task.duration_seconds ? task.duration_seconds.toFixed(2) + 's' : '—') + '</div>';
    html += '<div style="grid-column:1/-1;"><strong>Prompt:</strong> ' + esc(task.prompt) + '</div>';
    if (task.context) html += '<div style="grid-column:1/-1;"><strong>Context:</strong> ' + esc(task.context) + '</div>';
    html += '</div>';

    // Worker Results
    var results = task.worker_results || [];
    if (results.length) {
      html += '<h4 style="margin-bottom:8px;">Worker Results</h4>';
      html += '<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;">';
      results.forEach(function(wr, idx) {
        var statusColor = wr.status === 'success' ? 'var(--success)' : 'var(--danger)';
        html += '<div style="padding:10px;border:1px solid var(--border-subtle);border-radius:var(--radius);">';
        html += '<div style="display:flex;justify-content:space-between;margin-bottom:4px;">';
        html += '<span style="font-weight:500;">Step ' + (idx + 1) + ': ' + esc(wr.worker) + '</span>';
        html += '<span style="color:' + statusColor + ';">● ' + esc(wr.status) + '</span>';
        html += '</div>';
        if (wr.output) html += '<div style="font-family:var(--font-mono);font-size:0.75rem;color:var(--text-secondary);white-space:pre-wrap;max-height:200px;overflow-y:auto;background:rgba(0,0,0,0.15);padding:8px;border-radius:4px;">' + esc(wr.output) + '</div>';
        if (wr.error) html += '<div style="color:var(--danger);font-size:0.8rem;margin-top:4px;">⚠ ' + esc(wr.error) + '</div>';
        // Handoffs
        if (wr.handoffs && wr.handoffs.length) {
          html += '<div style="margin-top:6px;font-size:0.8rem;color:var(--info);">Handoff: ' + wr.handoffs.map(function(h) { return esc(h.from_worker) + ' → ' + esc(h.to_worker); }).join(', ') + '</div>';
        }
        html += '</div>';
      });
      html += '</div>';
    }

    // Consult opinions (VAL-ORCH-024, VAL-ORCH-025)
    var opinions = task.individual_opinions || [];
    if (opinions.length) {
      html += '<h4 style="margin-bottom:8px;">💬 Individual Opinions (Side-by-Side)</h4>';
      html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px;margin-bottom:16px;">';
      opinions.forEach(function(op) {
        html += '<div style="padding:10px;border:1px solid var(--border-subtle);border-radius:var(--radius);">';
        html += '<div style="font-weight:500;color:var(--accent);margin-bottom:4px;">' + esc(op.worker) + '</div>';
        html += '<div style="font-size:0.8rem;color:var(--text-secondary);white-space:pre-wrap;max-height:200px;overflow-y:auto;">' + esc(op.output || '') + '</div>';
        html += '</div>';
      });
      html += '</div>';
    }

    // Synthesized output
    if (task.synthesized_output) {
      html += '<h4 style="margin-bottom:8px;">🧠 Synthesized Answer</h4>';
      html += '<div style="padding:12px;background:var(--accent-subtle);border:1px solid var(--accent-subtle);border-radius:var(--radius);color:var(--accent-light);white-space:pre-wrap;">' + esc(task.synthesized_output) + '</div>';
    }

    // Aggregated output
    if (task.aggregated_output && !task.synthesized_output) {
      html += '<h4 style="margin-bottom:8px;">📊 Aggregated Output</h4>';
      html += '<div style="padding:12px;background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);border-radius:var(--radius);white-space:pre-wrap;">' + esc(task.aggregated_output) + '</div>';
    }

    // Metadata
    if (task.metadata && Object.keys(task.metadata).length) {
      html += '<h4 style="margin-top:16px;margin-bottom:8px;">📋 Metadata</h4>';
      html += '<pre style="font-family:var(--font-mono);font-size:0.75rem;padding:8px;background:rgba(0,0,0,0.15);border-radius:4px;overflow-x:auto;">' + esc(JSON.stringify(task.metadata, null, 2)) + '</pre>';
    }

    // Action buttons (cancel for running, retry for failed/timeout/cancelled)
    html += '<div style="display:flex;gap:8px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border-subtle);">';
    if (task.status === 'running' || task.status === 'pending' || task.status === 'paused') {
      html += '<button class="btn btn-danger" data-action="cancel" data-task-id="' + esc(task.id || task.task_id || '') + '" onclick="event.stopPropagation()">🚫 Cancel Task</button>';
    }
    if (task.status === 'failed' || task.status === 'timeout' || task.status === 'cancelled' || task.status === 'error') {
      html += '<button class="btn btn-primary" data-action="retry" data-task-id="' + esc(task.id || task.task_id || '') + '" onclick="event.stopPropagation()">🔄 Retry Task</button>';
    }
    html += '</div>';

    return html;
  }

  // ══════════════════════════════════════════════════════
  // LOGS VIEWER
  // ══════════════════════════════════════════════════════

  function viewLogs(workerName) {
    var modal = $('logs-modal');
    var titleEl = $('logs-worker-name');
    var contentEl = $('logs-content');
    if (!modal) return;
    if (titleEl) titleEl.textContent = 'Logs: ' + workerName;
    if (contentEl) contentEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">Loading…</div>';
    modal.style.display = 'flex';

    fetch('/api/swarm/workers/' + encodeURIComponent(workerName) + '/logs')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!contentEl) return;
        var lines = data.logs || [];
        contentEl.innerHTML = lines.length
          ? lines.map(function(l) { return '<div style="font-family:var(--font-mono);font-size:0.75rem;padding:2px 0;border-bottom:1px solid var(--border-subtle);">' + esc(String(l)) + '</div>'; }).join('')
          : '<div style="text-align:center;padding:40px;color:var(--text-muted);">No logs yet</div>';
      })
      .catch(function() {
        if (contentEl) contentEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">Failed to load logs</div>';
      });
  }

  function closeLogs(event) {
    if (event && event.target !== event.currentTarget) return;
    var modal = $('logs-modal');
    if (modal) modal.style.display = 'none';
  }

  // ══════════════════════════════════════════════════════
  // LIFECYCLE
  // ══════════════════════════════════════════════════════

  function swarmAction(action) {
    fetch('/api/swarm/' + action, { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        showToast(data.message || 'Done', data.status === 'ok' || data.status === 'warning');
        refreshStatus();
      })
      .catch(function(err) { showError(err.message); });
  }

  function applySavedProfile(target, profileName) {
    var scope = profileName === undefined ? 'add' : String(target || '').trim();
    var selectedName = profileName === undefined ? target : profileName;
    if (scope !== 'add' && scope !== 'spawn') scope = 'add';

    var modelInput = $(scope + '-model');
    var providerSelect = $(scope + '-provider');
    if (!modelInput || !providerSelect) return;

    var cleanName = String(selectedName || '').trim();
    if (!cleanName) {
      var fallbackModel = defaultModelOption();
      var fallbackProvider = defaultProviderOption();
      modelInput.value = fallbackModel;
      ensureProviderOption(providerSelect, fallbackProvider);
      providerSelect.value = fallbackProvider;
      return;
    }

    var profile = findProfileByName(cleanName);
    if (!profile) return;

    var profileModel = String(profile.model || '').trim();
    var profileProvider = String(profile.provider || '').trim();
    if (profileModel) modelInput.value = profileModel;
    if (profileProvider) {
      ensureProviderOption(providerSelect, profileProvider);
      providerSelect.value = profileProvider;
    }
  }

  // ══════════════════════════════════════════════════════
  // ACTIVE TASKS (live in-flight task tracking)
  // ══════════════════════════════════════════════════════

  function loadActiveTasks() {
    fetch('/api/swarm/tasks/active')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        var container = $('active-tasks-list');
        var emptyEl = $('active-tasks-empty');
        if (!container) return;
        var tasks = data.tasks || [];
        if (emptyEl) emptyEl.style.display = tasks.length ? 'none' : 'block';
        // Remove cards for tasks no longer active
        var activeIds = tasks.map(function(t) { return t.id; });
        container.querySelectorAll('[id^="active-task-"]').forEach(function(el) {
          var tid = el.id.replace('active-task-', '');
          if (activeIds.indexOf(tid) === -1) el.remove();
        });
        // Add/update cards for active tasks
        tasks.forEach(function(t) {
          var cardId = 'active-task-' + t.id;
          var existing = $(cardId);
          if (existing) {
            // Update status text
            var statusEl = existing.querySelector('.active-task-status');
            if (statusEl) {
              statusEl.textContent = t.status || 'running';
              statusEl.className = 'active-task-status status-' + (t.status || 'running');
            }
          } else {
            var card = document.createElement('div');
            card.className = 'card';
            card.id = cardId;
            card.style.cssText = 'margin-bottom:12px;padding:12px;border-left:3px solid var(--warning);';
            var workers = (t.workers || []).join(', ') || 'all';
            var statusColor = t.status === 'paused' ? 'var(--info)' : 'var(--warning)';
            card.innerHTML =
              '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                '<div>' +
                  '<strong>' + esc(t.prompt || t.id).substring(0, 60) + '</strong>' +
                  '<div style="font-size:0.8rem;color:var(--text-muted);margin-top:4px;">' +
                    'Workers: ' + esc(workers) + ' · Type: ' + esc(t.type || 'dispatch') +
                  '</div>' +
                '</div>' +
                '<span class="active-task-status status-' + esc(t.status || 'running') + '" ' +
                  'style="padding:2px 8px;border-radius:4px;background:' + statusColor + ';color:#fff;font-size:0.75rem;">' +
                  esc(t.status || 'running') +
                '</span>' +
              '</div>';
            container.appendChild(card);
          }
        });
      })
      .catch(function() { /* tab may not be visible */ });
  }

  // ══════════════════════════════════════════════════════
  // OUTPUT ROUTING (Phase 5)
  // ══════════════════════════════════════════════════════

  function loadOutputTarget() {
    fetch('/api/swarm/output-target')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data || !data.output_target) return;
        var t = data.output_target;
        var cb = $('output-routing-enabled');
        var id = $('output-routing-chat-id');
        var st = $('output-routing-status');
        if (cb) cb.checked = !!t.enabled;
        if (id) id.value = t.chat_id != null ? String(t.chat_id) : '';
        var bt = $('output-routing-bot-token');
        if (bt) bt.value = t.bot_token || '';
        if (st) {
          if (t.chat_id != null && t.enabled) {
            var mode = t.bot_token ? ' (swarm bot)' : ' (gateway)';
            st.textContent = '● Active → ' + t.chat_id + mode;
            st.style.color = 'var(--success)';
          } else if (t.chat_id != null) {
            st.textContent = '● Disabled';
            st.style.color = 'var(--text-muted)';
          } else {
            st.textContent = '';
          }
        }
      })
      .catch(function() { /* card may not be present */ });
  }

  function saveOutputTarget() {
    var cb = $('output-routing-enabled');
    var id = $('output-routing-chat-id');
    var bt = $('output-routing-bot-token');
    var st = $('output-routing-status');
    var enabled = cb ? cb.checked : false;
    var chatId = id ? id.value.trim() : '';
    var botToken = bt ? bt.value.trim() : '';
    if (enabled && !chatId) {
      showError('Enter a chat ID to enable routing.');
      return;
    }
    var payload;
    if (!chatId) {
      payload = { clear: true };
    } else {
      // Send chat_id as a string to avoid Number precision loss on large
      // Telegram supergroup IDs (>2^53). The server parses it via int().
      payload = { platform: 'telegram', chat_id: chatId, enabled: enabled };
      if (botToken) payload.bot_token = botToken;
    }
    fetch('/api/swarm/output-target', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.status === 'ok') {
          showToast('Output routing saved.', true);
          loadOutputTarget();
        } else {
          showError(data.message || 'Failed to save output routing.');
        }
      })
      .catch(function() { showError('Network error saving output routing.'); });
  }

  function clearOutputTarget() {
    var cb = $('output-routing-enabled');
    var id = $('output-routing-chat-id');
    var bt = $('output-routing-bot-token');
    var st = $('output-routing-status');
    fetch('/api/swarm/output-target', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clear: true }),
    })
      .then(function(r) { return r.json(); })
      .then(function() {
        if (cb) cb.checked = false;
        if (id) id.value = '';
        if (bt) bt.value = '';
        if (st) st.textContent = '';
        showToast('Output routing cleared.', true);
      })
      .catch(function() { showError('Network error clearing output routing.'); });
  }

  // ══════════════════════════════════════════════════════
  // BOOT
  // ══════════════════════════════════════════════════════

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ══════════════════════════════════════════════════════
  // PUBLIC API
  // ══════════════════════════════════════════════════════

  window.KazmaSwarm = {
    switchTab: switchTab,
    refresh: refreshStatus,
    addWorker: addWorker,
    removeWorker: removeWorker,
    startWorker: startWorker,
    stopWorker: stopWorker,
    editWorker: editWorker,
    closeEditWorker: closeEditWorker,
    saveEditWorker: saveEditWorker,
    dispatch: dispatchTask,
    start: function() { swarmAction('start'); },
    stop: function() { swarmAction('stop'); },
    onPatternChange: onPatternChange,
    approveCheckpoint: approveCheckpoint,
    rejectCheckpoint: rejectCheckpoint,
    cancelTask: cancelTask,
    retryTask: retryTask,
    viewLogs: viewLogs,
    closeLogs: closeLogs,
    viewTaskDetail: viewTaskDetail,
    closeTaskDetail: closeTaskDetail,
    loadTaskHistory: loadTaskHistory,
    filterHistory: filterHistory,
    historyPrev: historyPrev,
    historyNext: historyNext,
    filterResults: filterResults,
    applyRole: applyRole,
    applySavedProfile: applySavedProfile,
    saveOutputTarget: saveOutputTarget,
    clearOutputTarget: clearOutputTarget,
  };
})();
