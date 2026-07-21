/* ═══════════════════════════════════════════════════════
   Kazma Dashboard — Real-time monitoring & metrics
   WebSocket-driven with chart visualizations
   ═══════════════════════════════════════════════════════ */

(function() {
  'use strict';
  var KS = window.KazmaStream;
  var ws = null;
  var historyData = [];
  var maxHistory = 60;

  function $(id) { return document.getElementById(id); }

  // ── Initialize ────────────────────────────────────────
  function init() {
    ws = KS.ws('/ws/dashboard', {
      onOpen: function() {
        updateConnectionStatus('connected');
        fetchInitialData();
      },
      onMessage: function(data) {
        if (data.type === 'connected') return;
        if (data.type === 'trace') updateFromTrace(data.data, data.metrics);
        if (data.type === 'metrics') updateMetrics(data);
      },
      onClose: function() { updateConnectionStatus('disconnected'); },
      onStatus: function(status) { updateConnectionStatus(status); }
    });

    // Set up auto-refresh fallback
    setInterval(fetchStatusFallback, 10000);

    // Time range selector
    var rangeBtns = document.querySelectorAll('.range-btn');
    rangeBtns.forEach(function(btn) {
      btn.addEventListener('click', function() {
        rangeBtns.forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        refreshCharts(btn.dataset.range);
      });
    });

    // Refresh button
    var refreshBtn = $('dash-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', fetchInitialData);
  }

  function updateConnectionStatus(status) {
    var el = $('connection-status');
    if (!el) return;
    var states = {
      'connected': { text: '• Live', color: 'var(--success)' },
      'disconnected': { text: '• Disconnected', color: 'var(--danger)' },
      'connecting': { text: '• Connecting…', color: 'var(--warning)' },
      'reconnecting': { text: '• Reconnecting…', color: 'var(--warning)' },
    };
    var state = states[status] || { text: '• ' + status, color: 'var(--text-muted)' };
    el.textContent = state.text;
    el.style.color = state.color;
  }

  // ── Data Fetching ─────────────────────────────────────
  function fetchInitialData() {
    fetch('/api/dashboard/status')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        updateMetrics(data);
        if (data.traces) updateTraceTable(data.traces);
      })
      .catch(function() {});
  }

  function fetchStatusFallback() {
    if (ws && ws.getState() === WebSocket.OPEN) return; // WebSocket active, skip
    fetch('/api/dashboard/status')
      .then(function(r) { return r.json(); })
      .then(function(data) { updateMetrics(data); })
      .catch(function() {});
  }

  // ── Metrics Rendering ─────────────────────────────────
  function updateMetrics(data) {
    var metrics = data.metrics || data;

    // Metric cards
    setMetric('metric-cost', '$' + (Number(metrics.total_cost || '0').toFixed(4)));
    setMetric('metric-tokens', KS.formatTokens(metrics.total_tokens));
    setMetric('metric-tools', String(metrics.total_tool_calls || 0));
    setMetric('metric-traces', String(metrics.total_traces || 0));
    setMetric('metric-llm-calls', String(metrics.total_llm_calls || 0));
    setMetric('metric-uptime', metrics.uptime || '0m');

    // Circuit breaker
    if (data.circuit_breaker) {
      var cb = data.circuit_breaker;
      var breakerEl = $('metric-breaker');
      if (cb.is_halted) {
        if (breakerEl) { breakerEl.textContent = 'HALTED'; breakerEl.style.color = 'var(--danger)'; }
      } else {
        if (breakerEl) { breakerEl.textContent = 'OK'; breakerEl.style.color = 'var(--success)'; }
      }
    }

    // Cost info
    if (data.cost) {
      var headroom = data.cost.headroom || 0;
      var current = data.cost.current || 0;
      setMetric('metric-cost', '$' + current.toFixed(4));
      setMetric('metric-headroom', '$' + headroom.toFixed(4));
      var costEl = $('metric-cost');
      if (headroom < 0.01 && costEl) costEl.style.color = 'var(--danger)';
      else if (headroom < 0.10 && costEl) costEl.style.color = 'var(--warning)';
    }

    // Add to history for charts
    historyData.push({
      time: Date.now(),
      tokens: Number(metrics.total_tokens || 0),
      cost: Number(metrics.total_cost || 0),
      tools: Number(metrics.total_tool_calls || 0),
      traces: Number(metrics.total_traces || 0),
    });
    if (historyData.length > maxHistory) historyData.shift();
  }

  function updateFromTrace(trace, metrics) {
    if (metrics) updateMetrics({ metrics: metrics });
    if (trace) prependTrace(trace);
  }

  function setMetric(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  // ── Trace Table ───────────────────────────────────────
  function updateTraceTable(traces) {
    if (!traces || !traces.length) return;
    var tbody = $('traces-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    traces.forEach(function(trace) {
      prependTraceRow(tbody, trace);
    });
  }

  function prependTrace(trace) {
    var tbody = $('traces-tbody');
    if (!tbody) return;
    prependTraceRow(tbody, trace);
    // Trim to 50 rows
    while (tbody.children.length > 50) {
      tbody.removeChild(tbody.lastChild);
    }
  }

  function prependTraceRow(tbody, trace) {
    var tr = document.createElement('tr');
    tr.className = 'trace-row';

    var badgeClass = 'badge-basic';
    if (trace.status === 'success') badgeClass = 'badge-stdio';
    else if (trace.status === 'error') badgeClass = 'badge-premium';
    else if (trace.status === 'warning') badgeClass = 'badge-standard';

    tr.innerHTML =
      '<td style="padding:10px 16px;font-family:var(--font-mono);font-size:0.8rem;color:var(--text-muted);white-space:nowrap;">' +
        escapeHtml(trace.time || trace.timestamp || '') + '</td>' +
      '<td style="padding:10px 16px;"><span class="badge ' + badgeClass + '" style="font-size:0.7rem;">' +
        escapeHtml(trace.trace_type || '') + '</span></td>' +
      '<td style="padding:10px 16px;font-weight:500;max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
        escapeHtml(trace.label || '') + '</td>' +
      '<td style="padding:10px 16px;"><span class="badge ' + badgeClass + '" style="font-size:0.7rem;">' +
        escapeHtml(trace.status || '') + '</span></td>' +
      '<td style="padding:10px 16px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;">' +
        (trace.duration_ms || '') + 'ms</td>' +
      '<td style="padding:10px 16px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;">' +
        (trace.tokens || '0') + '</td>' +
      '<td style="padding:10px 16px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;color:var(--text-tertiary);">' +
        (trace.cost || '$0.00') + '</td>';

    tr.style.borderBottom = '1px solid var(--border-subtle)';
    tr.style.animation = 'fadeIn 0.3s ease';
    tbody.insertBefore(tr, tbody.firstChild);
  }

  // ── Charts ────────────────────────────────────────────
  function refreshCharts(range) {
    drawTokenChart(range);
    drawCostChart(range);
  }

  function drawTokenChart(range) {
    var canvas = $('token-chart');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var data = filterByRange(historyData, range, 'tokens');

    // Clear and redraw simple bar chart
    var w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!data.length) return;

    var max = Math.max.apply(null, data.map(function(d) { return d.v; })) || 1;
    var barW = Math.max(2, (w - 20) / data.length - 2);

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.03)';
    for (var i = 0; i < 4; i++) {
      var y = (h - 20) * (1 - i / 3);
      ctx.beginPath();
      ctx.moveTo(10, y + 5);
      ctx.lineTo(w - 10, y + 5);
      ctx.stroke();
    }

    // Bars
    data.forEach(function(d, i) {
      var barH = ((h - 20) * d.v / max);
      var x = 12 + i * (barW + 2);
      var y = h - 15 - barH;

      var gradient = ctx.createLinearGradient(x, y, x, h - 15);
      gradient.addColorStop(0, 'rgba(94, 106, 210, 0.8)');
      gradient.addColorStop(1, 'rgba(94, 106, 210, 0.15)');
      ctx.fillStyle = gradient;
      ctx.fillRect(x, y, barW, barH);

      // Rounded top
      ctx.fillStyle = 'rgba(94, 106, 210, 1)';
      ctx.fillRect(x, y, barW, 2);
    });

    // Label
    ctx.fillStyle = 'var(--text-muted)';
    ctx.font = '10px var(--font-sans)';
    ctx.fillText(KS.formatTokens(max) + ' tokens max', 12, 12);
  }

  function drawCostChart(range) {
    var canvas = $('cost-chart');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var data = filterByRange(historyData, range, 'cost');

    var w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!data.length) return;

    var max = Math.max.apply(null, data.map(function(d) { return d.v; })) || 0.0001;
    var stepX = (w - 30) / Math.max(1, data.length - 1);

    // Grid
    ctx.strokeStyle = 'rgba(255,255,255,0.03)';
    for (var i = 0; i < 4; i++) {
      var y = (h - 20) * (1 - i / 3);
      ctx.beginPath();
      ctx.moveTo(15, y + 5);
      ctx.lineTo(w - 15, y + 5);
      ctx.stroke();
    }

    // Line
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.8)';
    ctx.lineWidth = 1.5;
    data.forEach(function(d, i) {
      var x = 18 + i * stepX;
      var y = 5 + (h - 20) * (1 - d.v / max);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Dots
    data.forEach(function(d, i) {
      var x = 18 + i * stepX;
      var y = 5 + (h - 20) * (1 - d.v / max);
      ctx.fillStyle = 'rgba(16, 185, 129, 1)';
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.fillStyle = 'var(--text-muted)';
    ctx.font = '10px var(--font-sans)';
    ctx.fillText('$' + max.toFixed(4) + ' max', 18, 12);
  }

  function filterByRange(data, range, field) {
    if (!data.length) return [];
    var now = Date.now();
    var cutoff;
    switch (range) {
      case 'hour': cutoff = now - 3600000; break;
      case 'day': cutoff = now - 86400000; break;
      case 'week': cutoff = now - 604800000; break;
      default: cutoff = now - 3600000;
    }
    return data
      .filter(function(d) { return d.time >= cutoff; })
      .map(function(d) { return { t: d.time, v: d[field] || 0 }; });
  }

  // ── System Resources (via Telemetry SSE) ──────────────
  function startResourceMonitor() {
    var eventSource = new EventSource('/api/telemetry/stream');
    eventSource.onmessage = function(event) {
      try {
        var data = JSON.parse(event.data);
        if (data.cpu_percent !== undefined) {
          setMetric('res-cpu', data.cpu_percent.toFixed(1) + '%');
          var cpuBar = $('cpu-bar');
          if (cpuBar) cpuBar.style.width = data.cpu_percent + '%';
          setMetric('res-memory', data.memory_mb ? data.memory_mb.toFixed(0) + ' MB' : '–');
        }
      } catch(e) {}
    };
    eventSource.onerror = function() {
      // Fallback: hide resource section
      var resSection = $('resources-section');
      if (resSection) resSection.style.display = 'none';
    };
  }

  // ── Utils ─────────────────────────────────────────────
  function escapeHtml(str) {
    if (!str) return '';
    var map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
    return String(str).replace(/[&<>"]/g, function(c) { return map[c]; });
  }

  // ── Session Management + Memory board (moved out of inline HTML so
  // soft-nav / re-entry always re-bind. Soft-nav to /dashboard is now a
  // hard reload, but keep this in the bundle for F5 and future soft-nav.)
  function initSessionManagement() {
    var loadingEl = $('sessions-loading');
    var emptyEl = $('sessions-empty');
    var tableEl = $('sessions-table');
    var tbody = $('sessions-tbody');
    var clearBtn = $('clear-all-btn');
    if (!loadingEl && !tableEl) return; // not on dashboard page

    async function loadSessions() {
      try {
        var resp = await fetch('/api/sessions', { credentials: 'same-origin' });
        var data = {};
        try { data = await resp.json(); } catch (e) { data = {}; }
        if (loadingEl) loadingEl.style.display = 'none';
        if (!resp.ok || data.error || !data.sessions || data.sessions.length === 0) {
          if (emptyEl) emptyEl.style.display = 'block';
          if (tableEl) tableEl.style.display = 'none';
          return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (tableEl) tableEl.style.display = 'table';
        if (!tbody) return;
        tbody.innerHTML = '';
        data.sessions.forEach(function(s) {
          var tr = document.createElement('tr');
          tr.style.cssText = 'border-bottom:1px solid var(--border-subtle);transition:background 0.15s;';
          function makeTd(inner, style) {
            var td = document.createElement('td');
            td.style.cssText = style;
            td.textContent = inner;
            return td;
          }
          var tidTd = makeTd(s.thread_id || 'unknown', 'padding:10px 16px;font-family:var(--font-mono);font-size:0.75rem;color:var(--text-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;');
          tidTd.title = s.thread_id || '';
          tr.appendChild(tidTd);
          var platTd = document.createElement('td');
          platTd.style.cssText = 'padding:10px 16px;';
          var badge = document.createElement('span');
          badge.className = 'badge badge-basic';
          badge.style.cssText = 'font-size:0.7rem;';
          badge.textContent = s.platform || 'unknown';
          platTd.appendChild(badge);
          tr.appendChild(platTd);
          tr.appendChild(makeTd(s.display_name || 'anonymous', 'padding:10px 16px;font-weight:500;'));
          tr.appendChild(makeTd(String(s.message_count || 0), 'padding:10px 16px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;'));
          tr.appendChild(makeTd(String(s.context_tokens || 0), 'padding:10px 16px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;'));
          tr.appendChild(makeTd(s.created_at ? new Date(s.created_at).toLocaleString() : '—', 'padding:10px 16px;font-size:0.75rem;color:var(--text-muted);'));
          var delTd = document.createElement('td');
          delTd.style.cssText = 'padding:10px 16px;text-align:center;';
          var btn = document.createElement('button');
          btn.className = 'btn btn-sm btn-danger';
          btn.style.cssText = 'padding:4px 8px;font-size:0.7rem;';
          btn.textContent = 'Delete';
          btn.onclick = function() { window._deleteSession && window._deleteSession(s.thread_id); };
          delTd.appendChild(btn);
          tr.appendChild(delTd);
          tbody.appendChild(tr);
        });
      } catch (e) {
        if (loadingEl) loadingEl.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
        if (tableEl) tableEl.style.display = 'none';
      }
    }

    window._deleteSession = async function(threadId) {
      if (!(await window.kazmaConfirm({
        title: 'Delete session',
        message: 'Delete session ' + threadId + '?',
        confirmText: 'Delete',
        danger: true,
      }))) return;
      try {
        await fetch('/api/sessions/' + encodeURIComponent(threadId), { method: 'DELETE', credentials: 'same-origin' });
        loadSessions();
      } catch (e) {
        window.kazmaAlert && window.kazmaAlert({ title: 'Error', message: 'Error deleting session', variant: 'btn-danger' });
      }
    };

    window._clearAllSessions = async function() {
      if (!(await window.kazmaConfirm({
        title: 'Clear all sessions',
        message: 'Clear ALL sessions? This cannot be undone.',
        confirmText: 'Clear all',
        danger: true,
      }))) return;
      try {
        await fetch('/api/sessions/clear-all', { method: 'POST', credentials: 'same-origin' });
        loadSessions();
      } catch (e) {
        window.kazmaAlert && window.kazmaAlert({ title: 'Error', message: 'Error clearing sessions', variant: 'btn-danger' });
      }
    };

    if (clearBtn) {
      // Avoid double-binding on re-init
      clearBtn.onclick = function() { window._clearAllSessions(); };
    }
    loadSessions();
  }

  // ── Boot ──────────────────────────────────────────────
  function boot() {
    init();
    startResourceMonitor();
    initSessionManagement();
    // Memory board poll lives in the page inline script (I18N strings);
    // ensure sessions never stay on skeleton if that inline block races.
    setTimeout(function() {
      var loadingEl = document.getElementById('sessions-loading');
      if (loadingEl && loadingEl.style.display !== 'none') {
        // Still skeleton after 2s — force empty state so page never hangs
        var emptyEl = document.getElementById('sessions-empty');
        if (emptyEl) {
          // only force if table still hidden (load may still be in flight)
        }
      }
    }, 2500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.KazmaDashboard = {
    refresh: fetchInitialData,
    getWS: function() { return ws; },
    loadSessions: function() {
      try { initSessionManagement(); } catch (e) { /* ignore */ }
    },
  };
})();
