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

    // Set up auto-refresh fallback (skipped while the tab is hidden —
    // memory_console.js already gates its polls this way)
    setInterval(function() {
      if (!document.hidden) fetchStatusFallback();
    }, 10000);

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

    // Render feature cards (static — native skills don't change at runtime)
    initFeatureCards();
  }

  // ── Feature Cards ────────────────────────────────────
  function initFeatureCards() {
    var grid = $('feature-cards-grid');
    if (!grid) return;

    var isAr = (document.documentElement.getAttribute('dir') || '') === 'rtl' ||
               (window.KAZMA_LANG === 'ar');

    var svgIcons = {
      doc: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
      web: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
      memory: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="15" x2="23" y2="15"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="15" x2="4" y2="15"/></svg>',
      swarm: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>',
      hitl: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      time: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 8 14"/></svg>',
      email: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
      culture: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8l6 6M4 14l6-6 2 2M2 5h12M7 2v3M22 22l-5-10-5 10M14 18h6"/></svg>',
      cua: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
      voice: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>',
      ide: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
      mcp: '<svg width="20" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>'
    };

    var fallback = [
      { id: 'doc', name: 'Document Intelligence', desc: 'Intake, OCR, index, redact, generate PDF/DOCX', color: '#3b82f6' },
      { id: 'web', name: 'Web research', desc: 'web_search, read_url, crawl + Firecrawl/Jina', color: '#0ea5e9' },
      { id: 'memory', name: 'Cognitive memory', desc: 'V2 beliefs, episodes, PPR, sqlite-vec / pgvector', color: '#38bdf8' },
      { id: 'swarm', name: 'Swarm engine', desc: 'Autoscaler, breakers, optional Temporal', color: '#f59e0b' },
      { id: 'hitl', name: 'HITL + commitment', desc: 'Three gates, semantic confirm, MCP sampling HITL', color: '#ef4444' },
      { id: 'time', name: 'Time travel', desc: 'Snapshot replay and fork', color: '#10b981' },
      { id: 'email', name: 'Email', desc: 'Gmail / Microsoft OAuth + sandbox', color: '#3b82f6' },
      { id: 'culture', name: 'Arabic & RTL', desc: 'Khaleeji dialect, RTL UI, Majlis, i18n', color: '#ec4899' },
      { id: 'cua', name: 'Computer use', desc: 'Screenshot→action; Anthropic CUA / Gemini when active', color: '#22d3ee' },
      { id: 'voice', name: 'Voice duplex', desc: 'LiveKit AEC + barge-in; graph still owns meaning', color: '#a78bfa' },
      { id: 'ide', name: 'IDE + code index', desc: 'Monaco, apply-patch, codebase_search', color: '#34d399' },
      { id: 'mcp', name: 'MCP + skills', desc: 'MCP tools/resources; sampling HITL; agentskills.io', color: '#fb7185' },
    ];
    var features = (window.DASH_CAPS && window.DASH_CAPS.length)
      ? window.DASH_CAPS
      : fallback;

    var dirAttr = isAr ? ' dir="rtl"' : '';
    var textAlign = isAr ? 'text-align:right;' : '';

    var html = features.map(function(f) {
      var icon = svgIcons[f.id] || svgIcons.doc;
      var color = f.color || '#3b82f6';
      return '<div' + dirAttr + ' class="feature-cap-card" style="background:var(--card-bg, var(--bg-panel));border:1px solid var(--border-color, var(--border));border-radius:10px;padding:14px;transition:border-color 0.2s;cursor:default;' + textAlign + '" onmouseover="this.style.borderColor=\'' + color + '\'"; onmouseout="this.style.borderColor=\'var(--border-color, var(--border))\'">' +
        '<div style="color:' + color + ';margin-bottom:8px;display:flex;align-items:center;">' + icon + '</div>' +
        '<div style="font-weight:600;font-size:0.85rem;margin-bottom:4px;">' + f.name + '</div>' +
        '<div style="font-size:0.72rem;color:var(--text-secondary);line-height:1.3;">' + f.desc + '</div>' +
        '</div>';
    }).join('');
    grid.innerHTML = html;
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
  /** Parse "$1.23" / "1,234" / 1234 into a finite number (legacy-safe). */
  function parseMetricNumber(value) {
    if (value == null || value === '') return 0;
    if (typeof value === 'number' && isFinite(value)) return value;
    var s = String(value).replace(/[$,\s]/g, '').trim();
    var n = Number(s);
    return isFinite(n) ? n : 0;
  }

  function formatUptime(seconds) {
    var secs = Math.max(0, Math.floor(Number(seconds) || 0));
    if (secs < 60) return secs + 's';
    var mins = Math.floor(secs / 60);
    if (mins < 60) return mins + 'm';
    var hours = Math.floor(mins / 60);
    var remM = mins % 60;
    if (hours < 48) return hours + 'h ' + remM + 'm';
    var days = Math.floor(hours / 24);
    return days + 'd ' + (hours % 24) + 'h';
  }

  function updateMetrics(data) {
    var metrics = data.metrics || data;

    var totalCost = parseMetricNumber(metrics.total_cost);
    var totalTokens = parseMetricNumber(metrics.total_tokens);
    var toolCalls = parseMetricNumber(metrics.total_tool_calls);
    var traces = parseMetricNumber(metrics.total_traces);
    var llmCalls = parseMetricNumber(metrics.total_llm_calls);

    // Cost: prefer max(trace-store total, cost-breaker current) so cards stay real
    var breakerCost = data.cost ? parseMetricNumber(data.cost.current) : 0;
    var displayCost = Math.max(totalCost, breakerCost);
    var headroom = data.cost ? parseMetricNumber(data.cost.headroom) : 0;
    if (data.cost && breakerCost < totalCost) {
      var maxBudget = parseMetricNumber(data.cost.max) || 0.5;
      headroom = Math.max(0, maxBudget - totalCost);
    }

    setMetric('metric-cost', '$' + displayCost.toFixed(4));
    // Keep trailing label from SSR when present (i18n "headroom")
    var headEl = $('metric-headroom');
    if (headEl) {
      var headLabel = (headEl.textContent || '').replace(/\$[\d.,]+/, '').trim() || 'headroom';
      headEl.textContent = '$' + headroom.toFixed(4) + (headLabel ? ' ' + headLabel : '');
    }
    setMetric('metric-tokens', KS.formatTokens(totalTokens));
    setMetric('metric-tools', String(Math.round(toolCalls)));
    // Sub-lines under tokens / tools cards — preserve SSR labels when possible
    var llmEl = $('metric-llm-calls');
    if (llmEl) {
      var llmLabel = (llmEl.textContent || '').replace(/^[\d,.\s]+/, '').trim() || 'LLM calls';
      llmEl.textContent = Math.round(llmCalls) + ' ' + llmLabel;
    }
    var tracesEl = $('metric-traces');
    if (tracesEl) {
      var trLabel = (tracesEl.textContent || '').replace(/^[\d,.\s]+/, '').trim() || 'traces';
      tracesEl.textContent = Math.round(traces) + ' ' + trLabel;
    }

    if (metrics.uptime) {
      setMetric('metric-uptime', metrics.uptime);
    } else if (metrics.uptime_seconds != null) {
      setMetric('metric-uptime', formatUptime(metrics.uptime_seconds));
    }

    // Cost circuit breaker (budget halt) — not swarm worker breakers
    if (data.circuit_breaker) {
      var cb = data.circuit_breaker;
      var breakerEl = $('metric-breaker');
      if (cb.is_halted) {
        if (breakerEl) { breakerEl.textContent = 'HALTED'; breakerEl.style.color = 'var(--danger)'; }
      } else if (displayCost > 0 && headroom < 0.01 && data.cost) {
        if (breakerEl) { breakerEl.textContent = 'WARNING'; breakerEl.style.color = 'var(--warning)'; }
      } else {
        if (breakerEl) { breakerEl.textContent = 'OK'; breakerEl.style.color = 'var(--success)'; }
      }
    }

    // Active model chip (registry SoT)
    if (data.active_model != null || data.active_provider != null) {
      setMetric('metric-active-model', data.active_model || '—');
      var provEl = $('metric-active-provider');
      if (provEl) provEl.textContent = data.active_provider || 'provider';
    }

    var costEl = $('metric-cost');
    if (costEl) {
      if (headroom < 0.01 && data.cost) costEl.style.color = 'var(--danger)';
      else if (headroom < 0.10 && data.cost) costEl.style.color = 'var(--warning)';
      else costEl.style.color = 'var(--text-primary)';
    }

    // Add to history for charts
    historyData.push({
      time: Date.now(),
      tokens: totalTokens,
      cost: displayCost,
      tools: toolCalls,
      traces: traces,
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
      gradient.addColorStop(0, 'rgba(59, 130, 246, 0.85)');
      gradient.addColorStop(1, 'rgba(56, 189, 248, 0.18)');
      ctx.fillStyle = gradient;
      ctx.fillRect(x, y, barW, barH);

      // Rounded top
      ctx.fillStyle = 'rgba(96, 165, 250, 1)';
      ctx.fillRect(x, y, barW, 2);
    });

    // Label — canvas can't resolve CSS vars; read the computed token
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#8b9bb8';
    ctx.font = '10px Inter, sans-serif';
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

    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#8b9bb8';
    ctx.font = '10px Inter, sans-serif';
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
    window.__kazmaEventSources = window.__kazmaEventSources || [];
    window.__kazmaEventSources.push(eventSource);
    window.kazmaOnSoftNavLeave = function () {
      try { eventSource.close(); } catch (e) {}
    };
    eventSource.onmessage = function(event) {
      try {
        var data = JSON.parse(event.data);
        if (data.cpu_percent !== undefined) {
          setMetric('res-cpu', data.cpu_percent.toFixed(1) + '%');
          var cpuBar = $('cpu-bar');
          if (cpuBar) cpuBar.style.width = Math.min(100, data.cpu_percent) + '%';
          var memLabel = data.memory_mb ? data.memory_mb.toFixed(0) + ' MB' : '–';
          if (data.memory_percent != null) memLabel += ' (' + Number(data.memory_percent).toFixed(0) + '%)';
          setMetric('res-memory', memLabel);
          var memBar = $('mem-bar');
          if (memBar) {
            var mp = data.memory_percent != null
              ? Number(data.memory_percent)
              : (data.memory_mb != null ? Math.min(100, (data.memory_mb / 8192) * 100) : 0);
            memBar.style.width = Math.min(100, Math.max(0, mp)) + '%';
          }
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
  var SESSION_PREVIEW = 5;
  var _sessionsExpanded = false;
  var _sessionsCache = [];

  function initSessionManagement() {
    var loadingEl = $('sessions-loading');
    var emptyEl = $('sessions-empty');
    var tableEl = $('sessions-table');
    var tbody = $('sessions-tbody');
    var cardsEl = $('sessions-cards');
    var clearBtn = $('clear-all-btn');
    var expandWrap = $('sessions-expand-wrap');
    var expandBtn = $('sessions-expand-btn');
    var summaryEl = $('sessions-summary');
    if (!loadingEl && !tableEl) return; // not on dashboard page

    function renderSessionRows() {
      if (!tbody) return;
      tbody.innerHTML = '';
      if (cardsEl) cardsEl.innerHTML = '';
      var list = _sessionsCache || [];
      var showAll = _sessionsExpanded || list.length <= SESSION_PREVIEW;
      var visible = showAll ? list : list.slice(0, SESSION_PREVIEW);
      var lists = window.KazmaDashLists;
      visible.forEach(function(s) {
        var tr = document.createElement('tr');
        tr.style.cssText = 'border-bottom:1px solid var(--border-subtle);transition:background 0.15s;';
        function makeTd(inner, style) {
          var td = document.createElement('td');
          td.style.cssText = style;
          td.textContent = inner;
          return td;
        }
        var tidTd = makeTd(s.thread_id || 'unknown', 'padding:10px 12px;font-family:var(--font-mono);font-size:0.75rem;color:var(--text-muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;');
        tidTd.title = s.thread_id || '';
        tr.appendChild(tidTd);
        var platTd = document.createElement('td');
        platTd.style.cssText = 'padding:10px 12px;';
        var badge = document.createElement('span');
        badge.className = 'badge badge-basic';
        badge.style.cssText = 'font-size:0.7rem;';
        badge.textContent = s.platform || 'unknown';
        platTd.appendChild(badge);
        tr.appendChild(platTd);
        tr.appendChild(makeTd(s.display_name || 'anonymous', 'padding:10px 12px;font-weight:500;'));
        tr.appendChild(makeTd(String(s.message_count || 0), 'padding:10px 12px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;'));
        tr.appendChild(makeTd(String(s.context_tokens || 0), 'padding:10px 12px;text-align:right;font-family:var(--font-mono);font-size:0.8rem;'));
        tr.appendChild(makeTd(s.created_at ? new Date(s.created_at).toLocaleString() : '—', 'padding:10px 12px;font-size:0.75rem;color:var(--text-muted);'));
        var delTd = document.createElement('td');
        delTd.style.cssText = 'padding:10px 12px;text-align:center;';
        var btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-danger';
        btn.style.cssText = 'padding:4px 8px;font-size:0.7rem;';
        btn.textContent = 'Delete';
        btn.onclick = function() { window._deleteSession && window._deleteSession(s.thread_id); };
        delTd.appendChild(btn);
        tr.appendChild(delTd);
        tbody.appendChild(tr);
        if (cardsEl && lists && lists.buildSessionCard) {
          cardsEl.insertAdjacentHTML('beforeend', lists.buildSessionCard(s));
        }
      });
      if (cardsEl) {
        cardsEl.querySelectorAll('.dash-session-delete').forEach(function(delBtn) {
          delBtn.addEventListener('click', function() {
            window._deleteSession && window._deleteSession(delBtn.getAttribute('data-thread-id'));
          });
        });
      }
      if (expandWrap) {
        if (list.length > SESSION_PREVIEW) {
          expandWrap.style.display = 'block';
          if (expandBtn) {
            var hidden = list.length - SESSION_PREVIEW;
            expandBtn.textContent = _sessionsExpanded
              ? 'Show less'
              : ('Show ' + hidden + ' more session' + (hidden === 1 ? '' : 's'));
          }
        } else {
          expandWrap.style.display = 'none';
        }
      }
      if (summaryEl) {
        summaryEl.textContent = list.length
          ? (list.length + ' session' + (list.length === 1 ? '' : 's')
            + (list.length > SESSION_PREVIEW && !_sessionsExpanded
              ? ' · showing first ' + SESSION_PREVIEW
              : ''))
          : 'No sessions';
      }
    }

    async function loadSessions() {
      try {
        var resp = await fetch('/api/sessions', { credentials: 'same-origin' });
        var data = {};
        try { data = await resp.json(); } catch (e) { data = {}; }
        if (loadingEl) loadingEl.style.display = 'none';
        if (!resp.ok || data.error || !data.sessions || data.sessions.length === 0) {
          _sessionsCache = [];
          if (emptyEl) emptyEl.style.display = 'block';
          if (tableEl) tableEl.style.display = 'none';
          if (cardsEl) cardsEl.innerHTML = '';
          if (expandWrap) expandWrap.style.display = 'none';
          if (summaryEl) summaryEl.textContent = 'No sessions';
          return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (tableEl) tableEl.style.display = 'table';
        _sessionsCache = data.sessions.slice();
        renderSessionRows();
      } catch (e) {
        if (loadingEl) loadingEl.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
        if (tableEl) tableEl.style.display = 'none';
        if (expandWrap) expandWrap.style.display = 'none';
      }
    }

    if (expandBtn && !expandBtn._bound) {
      expandBtn._bound = true;
      expandBtn.addEventListener('click', function() {
        _sessionsExpanded = !_sessionsExpanded;
        renderSessionRows();
      });
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
