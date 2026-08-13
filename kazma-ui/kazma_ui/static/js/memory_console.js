/**
 * Memory console — health, V2 KPIs, belief list, topology graph, probe, maintenance.
 * Moved from dashboard.html so /memory is the single memory hub.
 * Expects window.__DASH_MEM_I18N for labels (optional).
 *
 * CUT_UI_BUILD: bump when shipping cut/hub-shortcut inspect changes so we can
 * verify cache-bust (console: window.__KAZMA_MEMORY_CONSOLE_BUILD).
 */
(function () {
  "use strict";
  var I18N = window.__DASH_MEM_I18N || window.I18N || {};
  // Visible build stamp — if missing in browser console, JS is stale/cached
  window.__KAZMA_MEMORY_CONSOLE_BUILD = 'comp-collapse-2026-08-04';
  // Memory & Governance Polling
  const memoryBadge = document.getElementById('memory-status-badge');
  const memoryDesc = document.getElementById('memory-status-desc');
  const installBtn = document.getElementById('install-ml-btn');

  function formatBytes(bytes, decimals = 2) {
    if (bytes === null || bytes === undefined || bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  function _chipStyle(status) {
    if (status === 'ok') {
      return {
        border: '1px solid rgba(46,213,115,0.35)',
        bg: 'rgba(46,213,115,0.08)',
        dot: '#2ed573',
        label: '#2ed573',
      };
    }
    if (status === 'warn') {
      return {
        border: '1px solid rgba(245,158,11,0.4)',
        bg: 'rgba(245,158,11,0.08)',
        dot: '#f59e0b',
        label: '#fbbf24',
      };
    }
    if (status === 'off') {
      return {
        border: '1px solid rgba(148,163,184,0.3)',
        bg: 'rgba(148,163,184,0.06)',
        dot: '#94a3b8',
        label: '#94a3b8',
      };
    }
    return {
      border: '1px solid rgba(255,71,87,0.4)',
      bg: 'rgba(255,71,87,0.08)',
      dot: '#ff4757',
      label: '#ff6b7a',
    };
  }

  function _memCardHtml(c) {
    const s = _chipStyle(c.status || (c.ok ? 'ok' : 'error'));
    const statusLabel = (c.status || (c.ok ? 'ok' : 'error')).toUpperCase();
    const detail = (c.detail || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const locName = (I18N.compNames && c.id && I18N.compNames[c.id]) || c.name || c.id || 'component';
    const name = String(locName).replace(/</g, '&lt;');
    return (
      // min-width:0 + overflow-wrap so long details (e.g. Postgres DSNs in
      // the ConfigStore card) wrap INSIDE the card instead of widening the
      // grid track and drifting past the border.
      '<div style="min-width:0;overflow-wrap:break-word;padding:10px 12px;border-radius:8px;border:' + s.border + ';background:' + s.bg + ';">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px;min-width:0;">' +
          '<span style="min-width:0;overflow-wrap:break-word;font-size:0.78rem;font-weight:600;color:var(--text-primary);">' + name + '</span>' +
          '<span style="flex-shrink:0;display:inline-flex;align-items:center;gap:5px;font-size:0.65rem;font-weight:700;letter-spacing:0.04em;color:' + s.label + ';">' +
            '<span style="width:7px;height:7px;border-radius:50%;background:' + s.dot + ';box-shadow:0 0 6px ' + s.dot + ';"></span>' +
            statusLabel +
          '</span>' +
        '</div>' +
        '<div style="font-size:0.7rem;line-height:1.4;color:var(--text-secondary);overflow-wrap:break-word;">' + detail + '</div>' +
      '</div>'
    );
  }

  function renderMemoryPipeline(data) {
    var el = document.getElementById('memory-pipeline');
    if (!el) return;
    var flags = data.flags || {};
    // V1 layer chips (layer_l1..layer_l4) removed during V1 memory retirement.
    // The V2 KPI grid (v2-kpi-grid) shows beliefs/episodes/entities/queue natively.
    var chips = [
      { id: 'memory_enabled', label: I18N.pipeEnabled || 'Memory' },
      { id: 'per_turn_retrieval', label: I18N.pipePerTurn || 'Per-turn RAG' },
      { id: 'auto_store', label: I18N.pipeAutoStore || 'Auto-store' },
      { id: 'consolidation', label: I18N.pipeConsolidate || 'Consolidator' },
    ];
    el.innerHTML = chips.map(function(ch) {
      var f = flags[ch.id] || {};
      var st = f.status || (f.ok ? 'ok' : 'off');
      var s = _chipStyle(st);
      return (
        '<span title="' + String(f.detail || ch.label).replace(/"/g, '&quot;') + '" style="display:inline-flex;align-items:center;gap:5px;font-size:0.68rem;font-weight:600;padding:3px 8px;border-radius:999px;border:' + s.border + ';background:' + s.bg + ';color:' + s.label + ';">' +
          '<span style="width:6px;height:6px;border-radius:50%;background:' + s.dot + ';"></span>' + ch.label +
        '</span>'
      );
    }).join('');
  }

  function renderMemoryKpis(data) {
    // V2 cognitive engine KPIs. The legacy L1 Chroma / L3 BM25 / L2 graph
    // labels are replaced with V2-native metrics (beliefs, episodes, entities).
    var v2 = data.v2 || {};
    var beliefs = v2.beliefs || {};
    var episodes = v2.episodes || {};
    var entitiesTotal = typeof v2.entities === 'number' ? v2.entities : 0;
    
    var activeBeliefs = beliefs.active != null ? beliefs.active : 0;
    var supersededBeliefs = beliefs.superseded != null ? beliefs.superseded : 0;
    var episodicCount = episodes.episodic != null ? episodes.episodic : 0;
    var workingCount = episodes.working != null ? episodes.working : 0;
    
    var set = function(id, text) {
      var n = document.getElementById(id);
      if (n) n.textContent = text;
    };
    var okLbl = I18N.kpiOk || 'OK';
    
    // Beliefs card: show active count, subtitle shows superseded
    set('kpi-vector-count', data.v2 ? String(activeBeliefs) : '–');
    set('kpi-vector-size', data.v2 ? (supersededBeliefs + ' superseded') : '–');
    
    // Episodes card: show episodic count, subtitle shows working
    set('kpi-fts-count', data.v2 ? String(episodicCount) : '–');
    set('kpi-fts-size', data.v2 ? (workingCount + ' working') : '–');
    
    // Entities card: show total count
    set('kpi-graph-nodes', data.v2 ? String(entitiesTotal) : '0');
    set('kpi-graph-edges', data.v2 ? 'total entities' : '–');
    
    var comps = Array.isArray(data.components) ? data.components : [];
    var okN = comps.filter(function(c) { return c.status === 'ok'; }).length;
    set('kpi-health-summary', comps.length ? (okN + '/' + comps.length + ' ' + okLbl) : (data.summary || '–'));
    set('graph-size-metric', data.v2 ? String(activeBeliefs) + ' beliefs' : '–');
    set('graph-count-metric', (data.v2 ? entitiesTotal : 0) + ' entities');
  }

  // Component-health group open state (survives poll re-renders). Default: collapsed.
  var _memCompOpen = {};
  var _memCompToggleAllWired = false;

  function _memGroupKey(title) {
    return String(title || 'group').toLowerCase().replace(/\s+/g, '_');
  }

  function _memGroupStatusLine(cards) {
    var okN = 0, warnN = 0, errN = 0, offN = 0;
    (cards || []).forEach(function(c) {
      var st = c.status || (c.ok ? 'ok' : 'error');
      if (st === 'ok') okN++;
      else if (st === 'warn') warnN++;
      else if (st === 'off') offN++;
      else errN++;
    });
    var parts = [okN + '/' + cards.length + ' OK'];
    if (warnN) parts.push(warnN + ' warn');
    if (errN) parts.push(errN + ' err');
    if (offN) parts.push(offN + ' off');
    return parts.join(' · ');
  }

  function _memGroupDotColor(cards) {
    var hasErr = false, hasWarn = false, hasOff = false;
    (cards || []).forEach(function(c) {
      var st = c.status || (c.ok ? 'ok' : 'error');
      if (st === 'error') hasErr = true;
      else if (st === 'warn') hasWarn = true;
      else if (st === 'off') hasOff = true;
    });
    if (hasErr) return '#ff4757';
    if (hasWarn) return '#f59e0b';
    if (hasOff) return '#94a3b8';
    return '#2ed573';
  }

  function _memCompSyncToggleAllLabel() {
    var btn = document.getElementById('memory-components-toggle-all');
    var grid = document.getElementById('memory-components-grid');
    if (!btn || !grid) return;
    var panels = grid.querySelectorAll('.mem-comp-group');
    if (!panels.length) {
      btn.textContent = 'Expand all';
      return;
    }
    var openN = 0;
    panels.forEach(function(p) {
      if (p.getAttribute('data-open') === '1') openN++;
    });
    // If any open → offer collapse all; if all closed → expand all
    btn.textContent = openN > 0 ? 'Collapse all' : 'Expand all';
  }

  function _memCompSetGroupOpen(groupEl, open) {
    if (!groupEl) return;
    var key = groupEl.getAttribute('data-group-key') || '';
    var body = groupEl.querySelector('.mem-comp-group-body');
    var chev = groupEl.querySelector('.mem-comp-chevron');
    groupEl.setAttribute('data-open', open ? '1' : '0');
    if (key) _memCompOpen[key] = !!open;
    if (body) body.style.display = open ? 'grid' : 'none';
    if (chev) chev.textContent = open ? '▾' : '▸';
    groupEl.style.borderColor = open ? 'var(--border-subtle)' : 'var(--border-subtle)';
    groupEl.style.background = open ? 'rgba(0,0,0,0.14)' : 'rgba(255,255,255,0.02)';
  }

  function _memCompWireGroups(grid) {
    if (!grid) return;
    grid.querySelectorAll('.mem-comp-group-head').forEach(function(head) {
      if (head._memWired) return;
      head._memWired = true;
      head.addEventListener('click', function() {
        var group = head.closest('.mem-comp-group');
        if (!group) return;
        var open = group.getAttribute('data-open') === '1';
        _memCompSetGroupOpen(group, !open);
        _memCompSyncToggleAllLabel();
      });
      head.addEventListener('keydown', function(ev) {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          head.click();
        }
      });
    });
    var allBtn = document.getElementById('memory-components-toggle-all');
    if (allBtn && !_memCompToggleAllWired) {
      _memCompToggleAllWired = true;
      allBtn.addEventListener('click', function() {
        var panels = grid.querySelectorAll('.mem-comp-group');
        if (!panels.length) return;
        var anyOpen = false;
        panels.forEach(function(p) {
          if (p.getAttribute('data-open') === '1') anyOpen = true;
        });
        // Toggle: if any open → collapse all; else expand all
        var next = !anyOpen;
        panels.forEach(function(p) { _memCompSetGroupOpen(p, next); });
        _memCompSyncToggleAllLabel();
      });
    }
    _memCompSyncToggleAllLabel();
  }

  function _memGroupHtml(title, cards) {
    if (!cards || !cards.length) return '';
    var key = _memGroupKey(title);
    // Default collapsed unless user previously expanded this group
    var open = _memCompOpen[key] === true;
    var statusLine = _memGroupStatusLine(cards);
    var dot = _memGroupDotColor(cards);
    return (
      '<div class="mem-comp-group" data-group-key="' + key.replace(/"/g, '') + '" data-open="' + (open ? '1' : '0') + '" ' +
        'style="border:1px solid var(--border-subtle);border-radius:10px;background:' + (open ? 'rgba(0,0,0,0.14)' : 'rgba(255,255,255,0.02)') + ';overflow:hidden;">' +
        '<button type="button" class="mem-comp-group-head" aria-expanded="' + (open ? 'true' : 'false') + '" ' +
          'style="width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:none;background:transparent;cursor:pointer;text-align:left;color:inherit;font:inherit;">' +
          '<span style="display:inline-flex;align-items:center;gap:8px;min-width:0;">' +
            '<span class="mem-comp-chevron" style="font-size:0.75rem;color:var(--text-muted);width:0.9rem;flex-shrink:0;">' + (open ? '▾' : '▸') + '</span>' +
            '<span style="font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-secondary);">' + String(title).replace(/</g, '&lt;') + '</span>' +
          '</span>' +
          '<span style="display:inline-flex;align-items:center;gap:6px;flex-shrink:0;font-size:0.68rem;font-family:var(--font-mono);color:var(--text-muted);">' +
            '<span style="width:7px;height:7px;border-radius:50%;background:' + dot + ';box-shadow:0 0 6px ' + dot + ';"></span>' +
            statusLine +
          '</span>' +
        '</button>' +
        '<div class="mem-comp-group-body" style="display:' + (open ? 'grid' : 'none') + ';grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;padding:0 12px 12px;">' +
          cards.map(_memCardHtml).join('') +
        '</div>' +
      '</div>'
    );
  }

  function renderMemoryComponents(data) {
    const grid = document.getElementById('memory-components-grid');
    const summaryEl = document.getElementById('memory-health-summary');
    const issuesEl = document.getElementById('memory-issues-list');
    if (!grid) return;

    const components = Array.isArray(data.components) ? data.components : [];
    if (summaryEl) {
      summaryEl.textContent = data.summary || (components.length ? `${components.filter(c => c.status === 'ok').length}/${components.length} OK` : '');
    }

    renderMemoryPipeline(data);
    renderMemoryKpis(data);

    if (!components.length) {
      grid.innerHTML = '<div style="padding:10px 12px;border-radius:8px;border:1px solid var(--border-subtle);background:rgba(255,255,255,0.02);font-size:0.75rem;color:var(--text-muted);">' + (I18N.noComponentData || 'No component data yet.') + '</div>';
    } else {
      var groups = [
        { title: I18N.groupFeatures || 'Features', ids: ['memory_enabled', 'per_turn_retrieval', 'auto_store', 'consolidation', 'kb_merge'] },
        { title: I18N.groupLayers || 'V2 cognitive stack', ids: ['embedder', 'vector_memory', 'layer_l1', 'layer_l2', 'layer_l3', 'layer_l4', 'graph_neo4j'] },
        { title: I18N.groupPackages || 'Packages & stores', ids: ['pkg_st', 'pkg_sqlite_vec', 'pkg_neo4j', 'pkg_chromadb', 'pkg_psycopg', 'pkg_lg_pg', 'store_config', 'store_checkpoints'] },
      ];
      var byId = {};
      components.forEach(function(c) { byId[c.id] = c; });
      var used = {};
      var html = groups.map(function(g) {
        var cards = g.ids.map(function(id) { return byId[id]; }).filter(Boolean);
        cards.forEach(function(c) { used[c.id] = true; });
        return _memGroupHtml(g.title, cards);
      }).join('');
      var rest = components.filter(function(c) { return !used[c.id]; });
      if (rest.length) {
        html += _memGroupHtml('Other', rest);
      }
      grid.innerHTML = html;
      _memCompWireGroups(grid);
    }

    const issues = Array.isArray(data.issues) ? data.issues : [];
    if (issuesEl) {
      if (issues.length) {
        issuesEl.style.display = 'block';
        issuesEl.innerHTML =
          '<div style="font-weight:700;margin-bottom:4px;">' + (I18N.issuesHeading || 'Needs attention') + '</div>' +
          '<ul style="margin:0;padding-left:1.1rem;">' +
          issues.map(function(i) {
            return '<li style="margin-bottom:3px;">' + String(i).replace(/</g, '&lt;') + '</li>';
          }).join('') +
          '</ul>';
      } else {
        issuesEl.style.display = 'none';
        issuesEl.innerHTML = '';
      }
    }
  }

  async function pollMemoryStatus() {
    try {
      const resp = await fetch('/api/system/status');
      const data = await resp.json();
      const status = data.status || 'ACTIVE';

      if (status === 'ACTIVE') {
        memoryBadge.textContent = I18N.memoryActive;
        memoryBadge.style.cssText = 'font-size:0.75rem;padding:4px 10px;border-radius:12px;font-weight:600;background:rgba(46, 213, 115, 0.15);color:#2ed573;border:1px solid rgba(46, 213, 115, 0.3);';
        // Prefer live headline (Postgres + vector status) over static Chroma-only copy
        if (data.headline) {
          memoryDesc.textContent = data.headline + (data.summary ? ' · ' + data.summary : '');
        } else {
          memoryDesc.textContent = data.summary
            ? (I18N.memoryDescActive + ' ' + data.summary)
            : I18N.memoryDescActive;
        }
        installBtn.style.display = 'none';
        installBtn.disabled = false;
        installBtn.textContent = I18N.installMl;
      } else if (status === 'DEMO') {
        memoryBadge.textContent = 'DEMO';
        memoryBadge.style.cssText = 'font-size:0.75rem;padding:4px 10px;border-radius:12px;font-weight:600;background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.3);';
        memoryDesc.textContent = 'Demo mode — RAG memory is disabled. The full version includes ChromaDB vector search and sentence-transformers.';
        installBtn.style.display = 'none';
      } else if (status === 'INSTALLING') {
        memoryBadge.textContent = I18N.memoryInstalling;
        memoryBadge.style.cssText = 'font-size:0.75rem;padding:4px 10px;border-radius:12px;font-weight:600;background:rgba(245, 158, 11, 0.15);color:#f59e0b;border:1px solid rgba(245, 158, 11, 0.3);';
        memoryDesc.textContent = I18N.memoryDescInstalling;
        installBtn.style.display = 'inline-block';
        installBtn.disabled = true;
        installBtn.textContent = I18N.installing;
      } else {
        memoryBadge.textContent = I18N.memoryDegraded;
        memoryBadge.style.cssText = 'font-size:0.75rem;padding:4px 10px;border-radius:12px;font-weight:600;background:rgba(255, 71, 87, 0.15);color:#ff4757;border:1px solid rgba(255, 71, 87, 0.3);';
        // Prefer first live issue as the headline reason.
        const firstIssue = (data.issues && data.issues[0]) || I18N.memoryDescDegraded;
        memoryDesc.textContent = firstIssue;
        installBtn.style.display = 'inline-block';
        installBtn.disabled = false;
        installBtn.textContent = I18N.installMl;
      }

      renderMemoryComponents(data);

      // Update metrics inside collapsible deck
      const ftsSizeEl = document.getElementById('fts5-size-metric');
      const ftsCountEl = document.getElementById('fts5-count-metric');
      if (ftsSizeEl && data.fts5_size !== undefined) {
        ftsSizeEl.textContent = formatBytes(data.fts5_size);
        if (ftsCountEl) ftsCountEl.textContent = `${data.fts5_count || 0} ${I18N.records}`;
      }

      const vecSizeEl = document.getElementById('vector-size-metric');
      const vecCountEl = document.getElementById('vector-count-metric');
      if (vecSizeEl && data.vector_size !== undefined) {
        vecSizeEl.textContent = formatBytes(data.vector_size);
        if (vecCountEl) vecCountEl.textContent = `${data.vector_count || 0} ${I18N.vectors}`;
      }

    } catch (e) {
      console.error('Failed to poll memory status:', e);
    }
  }

  var memoryRefreshBtn = document.getElementById('memory-refresh-btn');
  if (memoryRefreshBtn) {
    memoryRefreshBtn.addEventListener('click', function() {
      pollMemoryStatus();
    });
  }

  if (installBtn) {
    installBtn.addEventListener('click', async function() {
      installBtn.disabled = true;
      installBtn.textContent = I18N.installing;
      try {
        // Prefer full [rag] extra (chromadb + sentence-transformers + sqlite-vec)
        await fetch('/api/system/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ extra: 'rag' })
        });
      } catch (e) {
        window.kazmaAlert({ title: 'Install failed', message: I18N.installFailed, variant: 'btn-danger' });
        installBtn.disabled = false;
        installBtn.textContent = I18N.installMl;
      }
    });
  }

  // Backup & Maintenance Collapsible Deck Interaction
  const toggleBtn = document.getElementById('toggle-maintenance-deck');
  const deck = document.getElementById('maintenance-deck');
  const arrow = document.getElementById('deck-toggle-arrow');

  async function loadBackups() {
    const tbody = document.getElementById('backups-tbody');
    try {
      const resp = await fetch('/api/system/memory/backups');
      const data = await resp.json();
      const backups = data.backups || [];
      
      if (backups.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-muted);">${I18N.noBackups}</td></tr>`;
        return;
      }

      tbody.innerHTML = '';
      backups.forEach(backup => {
        const ftsSize = backup.fts5_size ? formatBytes(backup.fts5_size) : 'None';
        const vecSize = backup.vector_size ? formatBytes(backup.vector_size) : 'None';
        const created = new Date(backup.timestamp).toLocaleString();
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid var(--border-subtle)';
        tr.style.transition = 'background 0.2s';
        tr.onmouseover = function() { this.style.background = 'rgba(255,255,255,0.02)'; };
        tr.onmouseout = function() { this.style.background = 'transparent'; };

        const tdName = document.createElement('td');
        tdName.style.padding = '10px 16px';
        tdName.style.fontFamily = 'var(--font-mono)';
        tdName.textContent = backup.name;

        const tdFts = document.createElement('td');
        tdFts.style.padding = '10px 16px';
        tdFts.textContent = `${ftsSize} (${backup.fts5_count || 0} docs)`;

        const tdVec = document.createElement('td');
        tdVec.style.padding = '10px 16px';
        tdVec.textContent = vecSize;

        const tdCreated = document.createElement('td');
        tdCreated.style.padding = '10px 16px';
        tdCreated.style.color = 'var(--text-tertiary)';
        tdCreated.textContent = created;

        const tdAction = document.createElement('td');
        tdAction.style.padding = '10px 16px';
        tdAction.style.textAlign = 'center';
        const btn = document.createElement('button');
        btn.className = 'btn btn-sm btn-primary restore-btn';
        btn.dataset.name = backup.name;
        btn.style.padding = '2px 8px';
        btn.style.fontSize = '0.75rem';
        btn.style.borderRadius = '4px';
        btn.textContent = I18N.restore;
        tdAction.appendChild(btn);

        tr.appendChild(tdName);
        tr.appendChild(tdFts);
        tr.appendChild(tdVec);
        tr.appendChild(tdCreated);
        tr.appendChild(tdAction);
        tbody.appendChild(tr);
      });

      // Bind restore button action
      document.querySelectorAll('.restore-btn').forEach(btn => {
        btn.addEventListener('click', async function() {
          const name = this.getAttribute('data-name');
          if (await window.kazmaConfirm({ title: 'Restore backup', message: I18N.confirmRestore + ` "${name}"? ` + I18N.restoreWarning, confirmText: 'Restore', danger: true })) {
            this.disabled = true;
            this.textContent = I18N.restoring;
            try {
              const r = await fetch('/api/system/memory/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backup_name: name })
              });
              const res = await r.json();
              if (res.status === 'success') {
                window.kazmaAlert({ title: 'Restore complete', message: I18N.restoreSuccess });
                pollMemoryStatus();
                loadBackups();
              } else {
                window.kazmaAlert({ title: 'Error', message: 'Restoration error: ' + (res.detail || 'unknown error'), variant: 'btn-danger' });
              }
            } catch (e) {
              window.kazmaAlert({ title: 'Error', message: 'Restoration failed: ' + e, variant: 'btn-danger' });
            } finally {
              this.disabled = false;
              this.textContent = I18N.restore;
            }
          }
        });
      });
      
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:20px;color:#ff4757;">Failed to load backups.</td></tr>`;
    }
  }

  toggleBtn.addEventListener('click', () => {
    if (deck.style.display === 'none') {
      deck.style.display = 'block';
      arrow.innerHTML = (window.KazmaIcons ? KazmaIcons.get('chevron-down') : '');
      loadBackups();
    } else {
      deck.style.display = 'none';
      arrow.innerHTML = (window.KazmaIcons ? KazmaIcons.get('play') : '');
    }
  });

  // Action Buttons Triggers
  const backupBtn = document.getElementById('trigger-backup-btn');
  const backupSpinner = document.getElementById('backup-spinner');

  backupBtn.addEventListener('click', async () => {
    backupBtn.disabled = true;
    backupSpinner.style.display = 'inline-block';
    try {
      const resp = await fetch('/api/system/memory/backup', { method: 'POST' });
      const res = await resp.json();
      if (res.status === 'success') {
        window.kazmaAlert({ title: 'Backup complete', message: I18N.backupSuccess + `: ${res.manifest.name}` });
        loadBackups();
        pollMemoryStatus();
      } else {
        window.kazmaAlert({ title: 'Error', message: I18N.backupFailed + ': ' + (res.detail || 'unknown error'), variant: 'btn-danger' });
      }
    } catch (e) {
      window.kazmaAlert({ title: 'Error', message: I18N.backupFailed + ': ' + e, variant: 'btn-danger' });
    } finally {
      backupBtn.disabled = false;
      backupSpinner.style.display = 'none';
    }
  });

  const optimizeBtn = document.getElementById('trigger-optimize-btn');
  const optimizeSpinner = document.getElementById('optimize-spinner');

  optimizeBtn.addEventListener('click', async () => {
    optimizeBtn.disabled = true;
    optimizeSpinner.style.display = 'inline-block';
    try {
      const resp = await fetch('/api/system/memory/maintenance', { method: 'POST' });
      const res = await resp.json();
      if (res.status === 'success') {
        let msg = I18N.optimizeSuccess + '\n\n';
        if (res.details.fts5) {
          msg += `• FTS5 keyword index optimized (VACUUM & ANALYZE completed).\n  Reclaimed space: ${formatBytes(res.details.fts5.reclaimed_bytes)}\n\n`;
        }
        if (res.details.vector) {
          msg += `• Vector index optimized.\n  Reclaimed space: ${formatBytes(res.details.vector.reclaimed_bytes)}\n`;
        }
        window.kazmaAlert({ title: 'Optimization complete', message: msg });
        pollMemoryStatus();
      } else {
        window.kazmaAlert({ title: 'Error', message: I18N.optimizeFailed + ': ' + (res.detail || 'unknown error'), variant: 'btn-danger' });
      }
    } catch (e) {
      window.kazmaAlert({ title: 'Error', message: I18N.optimizeFailed + ': ' + e, variant: 'btn-danger' });
    } finally {
      optimizeBtn.disabled = false;
      optimizeSpinner.style.display = 'none';
    }
  });

  // Snapshot cleanup: TTL prune + VACUUM of snapshots.db (replay/fork history).
  const snapshotsBtn = document.getElementById('trigger-snapshots-btn');
  const snapshotsSpinner = document.getElementById('snapshots-spinner');

  if (snapshotsBtn) {
    snapshotsBtn.addEventListener('click', async () => {
      const ok = await window.kazmaConfirm({
        title: I18N.snapshotMaintainTitle || 'Clean up time-travel snapshots?',
        message: I18N.snapshotMaintainConfirm || 'Snapshots older than the retention window will be deleted and the database vacuumed to reclaim disk space. Replay history inside the window is kept.',
        confirmText: 'Clean up',
        danger: true,
      });
      if (!ok) return;
      snapshotsBtn.disabled = true;
      snapshotsSpinner.style.display = 'inline-block';
      try {
        const resp = await fetch('/api/system/snapshots/maintain', { method: 'POST' });
        const res = await resp.json();
        if (res.status === 'success') {
          const s = res.stats || {};
          const parts = [];
          if (s.deleted != null) parts.push(`${s.deleted} snapshot${s.deleted === 1 ? '' : 's'} older than ${s.retention_days}d`);
          if (s.reclaimed != null) parts.push(`${formatBytes(s.reclaimed)} reclaimed`);
          if (s.prune && s.prune !== 'ok') parts.push(`prune: ${s.prune}`);
          if (s.vacuum && s.vacuum !== 'ok') parts.push(`vacuum: ${s.vacuum}`);
          window.kazmaAlert({
            title: 'Snapshot cleanup complete',
            message: (parts.length ? parts.join('\n') : 'Nothing to clean — no snapshots outside the retention window.') + (res.auto_maintain ? '\n\nAuto-maintenance is ON (daily).' : '\n\nAuto-maintenance is OFF — run manually here.'),
          });
        } else {
          window.kazmaAlert({ title: 'Error', message: 'Cleanup failed: ' + (res.detail || 'unknown error'), variant: 'btn-danger' });
        }
      } catch (e) {
        window.kazmaAlert({ title: 'Error', message: 'Cleanup failed: ' + e, variant: 'btn-danger' });
      } finally {
        snapshotsBtn.disabled = false;
        snapshotsSpinner.style.display = 'none';
      }
    });
  }

  // Poll immediately and then every 5 seconds
  pollMemoryStatus();
  // Skip the poll tick while the tab is hidden (long-lived interval — audit).
  setInterval(function() { if (!document.hidden) pollMemoryStatus(); }, 5000);

  // ── V2 Cognitive Engine panel ─────────────────────────────────
  function _v2ChipStyle(status) {
    const map = {
      ACTIVE:      { bg:'rgba(46,213,115,0.12)', color:'#2ed573', label:'Active' },
      DUAL_WRITE:  { bg:'rgba(59,130,246,0.12)', color:'#60a5fa', label:'Dual-write' },
      DEGRADED:    { bg:'rgba(245,158,11,0.12)', color:'#fbbf24', label:'Degraded' },
      OFF:         { bg:'rgba(148,163,184,0.12)', color:'#94a3b8', label:'Off' },
    };
    return map[status] || map.OFF;
  }

  function fmtNum(n) { return (n === null || n === undefined) ? '–' : String(n); }

  async function pollV2Health() {
    try {
      const resp = await fetch('/api/memory/v2/health');
      const h = await resp.json();
      // Status badge
      const chip = _v2ChipStyle(h.status);
      const badge = document.getElementById('v2-status-badge');
      if (badge) {
        badge.textContent = chip.label + (h.use_new_stack ? ' · LIVE' : '');
        badge.style.background = chip.bg;
        badge.style.color = chip.color;
      }
      const desc = document.getElementById('v2-status-desc');
      if (desc) {
        if (!h.db_available) {
          desc.textContent = 'V2 database not initialized yet. Beliefs will populate after the first turn.';
        } else if (h.use_new_stack) {
          desc.textContent = 'V2 is the active read path (use_new_stack=true). Recall serves bi-temporal beliefs + tiered episodes.';
        } else {
          desc.textContent = 'Dual-write mode (use_new_stack=false). V2 receives writes; legacy RRF serves reads. Flip the flag to cut over.';
        }
      }
      // KPIs
      const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
      setEl('v2-kpi-beliefs-active', fmtNum(h.beliefs.active));
      setEl('v2-kpi-beliefs-meta', (h.beliefs.superseded||0) + ' superseded · ' + (h.beliefs.archived||0) + ' archived');
      setEl('v2-kpi-episodes-recall', fmtNum(h.episodes.recall));
      setEl('v2-kpi-episodes-meta', (h.episodes.episodic||0) + ' episodic · ' + (h.episodes.archived||0) + ' archived');
      setEl('v2-kpi-entities', fmtNum(h.entities));
      setEl('v2-kpi-procedural-meta', (h.procedural_dags.active||0) + ' active · ' + (h.procedural_dags.quarantine||0) + ' quarantined');
      const qPending = (h.queue.pending||0) + (h.queue.processing||0);
      setEl('v2-kpi-queue', fmtNum(qPending));
      setEl('v2-kpi-queue-meta', (h.queue.failed||0) + ' failed · ' + (h.recent_audits||0) + ' audits/24h');
      // Post-turn / embedder strip
      const pt = h.post_turn || {};
      const okEl = document.getElementById('v2-post-turn-ok');
      if (okEl) okEl.textContent = 'ok: ' + (pt.ok || 0) + ' · fail m/e/q: ' +
        (pt.mirror_fail||0) + '/' + (pt.extract_fail||0) + '/' + (pt.enqueue_fail||0);
      const errEl = document.getElementById('v2-post-turn-err');
      if (errEl) {
        const le = pt.last_error || h.last_error;
        errEl.textContent = le ? ('last error: ' + String(le).slice(0, 80)) : 'last error: none';
        errEl.style.color = le ? '#f87171' : 'var(--text-muted)';
      }
      const embEl = document.getElementById('v2-embedder-ready');
      if (embEl) embEl.textContent = 'embedder: ' + (h.embedder_ready ? 'ready' : 'unavailable');
      const rc = document.getElementById('v2-reconsol-meta');
      if (rc) {
        const lr = h.last_reconsolidation;
        if (lr && lr.finished_at) {
          rc.textContent = 'reconsol: merged ' + (lr.duplicate_beliefs_merged||0) +
            ' · emb ' + ((lr.episodes_embedded||0)+(lr.beliefs_embedded||0));
        } else {
          rc.textContent = 'reconsol: never';
        }
      }
      const gEl = document.getElementById('v2-graph-backend');
      if (gEl) {
        const g = h.graph || {};
        const prov = g.provider || 'sqlite';
        if (prov === 'neo4j') {
          const on = g.online ? 'online' : 'offline→sqlite fallback';
          gEl.textContent = 'graph: neo4j dual-write · ' + on + ' · paint sqlite';
          gEl.style.color = g.online ? '#2ed573' : '#fbbf24';
        } else {
          gEl.textContent = 'graph: sqlite';
          gEl.style.color = 'var(--text-muted)';
        }
      }
      const bmEl = document.getElementById('v2-backends-mode');
      if (bmEl) {
        bmEl.textContent = 'backends: ' + (h.backends_mode || 'local');
        const vc = h.vector_capability || {};
        if (vc.provider || vc.status) {
          bmEl.textContent += ' · vector ' + (vc.provider || vc.status || '');
        }
      }
      const strip = document.getElementById('v2-post-turn-strip');
      const failedQ = (h.queue && h.queue.failed) || 0;
      const hasErr = !!(pt.last_error || h.last_error || h.status_detail);
      if (strip && (failedQ > 0 || hasErr)) {
        strip.style.borderColor = 'rgba(248,113,113,0.35)';
      }
      // Sticky alert banner (P0-2)
      const banner = document.getElementById('v2-alert-banner');
      const bannerText = document.getElementById('v2-alert-banner-text');
      if (banner) {
        if (failedQ > 0 || hasErr || h.status === 'DEGRADED') {
          banner.style.display = 'block';
          const parts = [];
          if (failedQ > 0) parts.push(failedQ + ' failed queue task(s)');
          if (pt.last_error || h.last_error) parts.push('last post-turn error recorded');
          if (h.status === 'DEGRADED') parts.push('status DEGRADED');
          if (bannerText) bannerText.textContent = ' ' + (parts.join(' · ') || 'Check queue and post-turn strip.');
        } else {
          banner.style.display = 'none';
        }
      }
      // Tier breakdown bars
      const tb = document.getElementById('v2-tier-breakdown');
      if (tb) {
        const tiers = [
          ['Working', h.episodes.working, '#60a5fa'],
          ['Episodic', h.episodes.episodic, '#2ed573'],
          ['Recall', h.episodes.recall, '#fbbf24'],
          ['Archived', h.episodes.archived, '#94a3b8'],
        ];
        const total = tiers.reduce((s,t) => s + (t[1]||0), 0) || 1;
        tb.innerHTML = tiers.map(([name, count, color]) => {
          const pct = Math.round(((count||0) / total) * 100);
          return '<span title="' + name + ': ' + (count||0) + ' (' + pct + '%)" style="display:inline-flex;align-items:center;gap:4px;font-size:0.65rem;padding:2px 6px;border-radius:999px;border:1px solid var(--border-subtle);background:rgba(255,255,255,0.03);">' +
                 '<span style="width:7px;height:7px;border-radius:50%;background:' + color + ';flex-shrink:0;"></span>' +
                 '<span style="color:var(--text-muted);">' + name.slice(0, 4) + '</span>' +
                 '<span style="font-family:var(--font-mono);color:var(--text-primary);">' + (count||0) + '</span>' +
                 '</span>';
        }).join('');
      }
      const pb = document.getElementById('v2-procedural-breakdown');
      if (pb) {
        pb.textContent = (h.procedural_dags.active||0) + ' active skills · ' + (h.procedural_dags.quarantine||0) + ' quarantined';
      }
    } catch (e) {
      // silent — panel just stays stale
    }
  }

  var _v2gLastQuerySeeds = []; // entity/id strings from last probe/federated hit
  function _v2gCollectSeedsFromHits(hits) {
    var seeds = [];
    (hits || []).forEach(function(h) {
      var c = String(h.content || h.preview || '');
      // belief format often "subject predicate object" or SPO fields
      if (h.subject) seeds.push(String(h.subject));
      if (h.object) seeds.push(String(h.object));
      if (h.entity_id) seeds.push(String(h.entity_id));
      // tokenize content for slug match
      c.split(/[\s\|·\[\]:,]+/).forEach(function(tok) {
        if (tok && tok.length > 2 && tok.length < 48) seeds.push(tok);
      });
    });
    _v2gLastQuerySeeds = seeds;
  }
  async function runV2Probe() {
    const input = document.getElementById('v2-probe-input');
    const out = document.getElementById('v2-probe-results');
    if (!input || !out) return;
    const q = (input.value || '').trim();
    if (!q) { out.textContent = 'Enter a query.'; return; }
    out.textContent = 'Probing…';
    try {
      const resp = await fetch('/api/memory/v2/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: 5 }),
      });
      const data = await resp.json();
      if (!data.ok) { out.textContent = data.error || 'Probe failed'; return; }
      _v2gCollectSeedsFromHits([].concat(data.beliefs || [], data.episodes || []));
      // also seed from query words
      q.split(/\s+/).forEach(function(w) { if (w.length > 2) _v2gLastQuerySeeds.push(w); });
      const lines = [];
      function _srcChips(h) {
        var arr = (h.sources && h.sources.length) ? h.sources : (h.source ? [h.source] : []);
        if (!arr.length) return '';
        return arr.map(function(s) {
          var color = '#94a3b8';
          var key = String(s || '').toLowerCase();
          if (key.indexOf('ppr') >= 0 || key.indexOf('belief_ppr') >= 0) color = '#93c5fd';
          else if (key.indexOf('dense') >= 0) color = '#38bdf8';
          else if (key.indexOf('fts') >= 0 || key.indexOf('belief') >= 0) color = '#34d399';
          else if (key.indexOf('session') >= 0) color = '#fbbf24';
          return '<span style="display:inline-block;margin-right:3px;padding:1px 6px;border-radius:999px;font-size:0.62rem;font-weight:600;background:rgba(255,255,255,0.06);color:' + color + ';">' + _esc(s) + '</span>';
        }).join('');
      }
      (data.beliefs || []).forEach(function(h) {
        lines.push('<div style="margin-bottom:6px;padding:6px 8px;border-radius:6px;background:rgba(46,213,115,0.06);"><span style="color:#2ed573;font-size:0.65rem;font-weight:700;">BELIEF</span> ' +
          _esc(h.content || '') +
          '<div style="margin-top:3px;">' + _srcChips(h) +
          ' <span style="color:var(--text-muted);font-size:0.68rem;">score ' +
          (h.score != null ? Number(h.score).toFixed(3) : '') + '</span></div></div>');
      });
      (data.episodes || []).forEach(function(h) {
        lines.push('<div style="margin-bottom:6px;padding:6px 8px;border-radius:6px;background:rgba(96,165,250,0.06);"><span style="color:#60a5fa;font-size:0.65rem;font-weight:700;">EPISODE</span> ' +
          _esc((h.content || '').slice(0, 200)) +
          '<div style="margin-top:3px;">' + _srcChips(h) + '</div></div>');
      });
      if (!lines.length) {
        const hints = (data.hints || []).map(function(hh) {
          return '<div style="color:#fbbf24;margin-top:4px;">• ' + _esc(hh) + '</div>';
        }).join('');
        out.innerHTML = '<span style="color:var(--text-muted);">No hits.</span>' + hints;
      } else {
        out.innerHTML = lines.join('') +
          '<div style="margin-top:6px;font-size:0.68rem;color:var(--text-muted);">Channels: fts5 · dense · belief_ppr · session_boost (enable Explain recall in Settings → Memory)</div>' +
          '<button type="button" class="btn btn-sm" id="v2-probe-path-btn" style="margin-top:8px;font-size:0.72rem;">Show path on graph →</button>' +
          '<button type="button" class="btn btn-sm" id="v2-eval-golden-btn" style="margin-top:8px;margin-left:6px;font-size:0.72rem;">Run golden eval</button>';
        document.getElementById('v2-probe-path-btn')?.addEventListener('click', function() { _v2gApplyPathFromQuery(); });
        document.getElementById('v2-eval-golden-btn')?.addEventListener('click', runGoldenEval);
      }
    } catch (e) {
      out.textContent = 'Probe error: ' + e;
    }
  }

  var _openBeliefId = null;
  async function openBeliefDrawer(beliefId) {
    _openBeliefId = beliefId;
    const drawer = document.getElementById('v2-belief-drawer');
    const body = document.getElementById('v2-belief-drawer-body');
    if (!drawer || !body) return;
    drawer.style.display = 'block';
    body.textContent = 'Loading…';
    try {
      const resp = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId));
      const data = await resp.json();
      if (!data.ok || !data.belief) {
        body.textContent = data.error || 'Not found';
        return;
      }
      const b = data.belief;
      const chain = (data.chain || []).slice(1).map(function(c) {
        return _esc(c.subject) + ' ' + _esc(c.predicate) + ' ' + _esc(c.object);
      }).join(' ← ');

      // Recall history — fetch in parallel, render best-effort. Answers the
      // operator's "when/where was this used?" without a separate panel.
      let trailHtml = '';
      try {
        const tr = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId) + '/recall-trail');
        const td = await tr.json();
        if (td && td.ok) {
          const last = td.last_accessed ? new Date((td.last_accessed||0) * 1000).toLocaleString() : 'never';
          const ep = td.origin && td.origin.episode;
          const originTxt = ep
            ? ' · from <span style="color:var(--text-secondary);">' + _esc(ep.preview) + '</span>'
            : (td.origin && td.origin.session ? ' · session ' + _esc(td.origin.session) : '');
          trailHtml =
            '<div style="margin-top:6px;color:var(--text-muted);">' +
              'recalled <b style="color:var(--text-secondary);">' + (td.access_count||0) + '×</b>' +
              ' · last ' + _esc(last) +
              ' · via ' + _esc(td.extraction_method || '?') +
              originTxt +
            '</div>';
        }
      } catch (_) { /* trail is optional */ }

      // "Probe from this belief" — seeds the probe box with the object text so
      // the operator can see what else this belief recalls alongside.
      const probeBtn = '<button type="button" id="v2-belief-probe" style="margin-top:6px;font-size:0.7rem;padding:2px 8px;border:1px solid var(--border-subtle);background:transparent;color:var(--text-secondary);cursor:pointer;">Probe from this belief →</button>';

      body.innerHTML =
        '<div><b>' + _esc(b.subject) + '</b> ' + _esc((b.predicate||'').replace(/_/g,' ')) +
        ' <b>' + _esc(b.object) + '</b></div>' +
        '<div style="color:var(--text-muted);margin-top:4px;">id: ' + _esc(b.id) +
        ' · conf ' + Math.round((b.confidence||0)*100) + '%' +
        ' · imp ' + (b.structural_importance||'?') +
        ' · access ' + (b.access_count||0) + '</div>' +
        trailHtml +
        (chain ? '<div style="margin-top:6px;color:var(--text-muted);">supersedes chain: ' + chain + '</div>' : '') +
        probeBtn;

      // Wire the probe button.
      const pb = document.getElementById('v2-belief-probe');
      if (pb) {
        pb.addEventListener('click', function() {
          const probeInput = document.getElementById('v2g-probe-input') || document.getElementById('v2-probe-input');
          if (probeInput) {
            probeInput.value = b.object || b.subject || '';
            probeInput.dispatchEvent(new Event('input', { bubbles: true }));
          }
          const probeBtn2 = document.getElementById('v2-probe-btn') || document.getElementById('v2g-probe-btn');
          if (probeBtn2) probeBtn2.click();
          document.getElementById('v2g-canvas-wrap')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
    } catch (e) {
      body.textContent = 'Load failed';
    }
  }
  document.getElementById('v2-belief-drawer-close')?.addEventListener('click', function() {
    const d = document.getElementById('v2-belief-drawer');
    if (d) d.style.display = 'none';
  });
  document.getElementById('v2-belief-invalidate')?.addEventListener('click', async function() {
    if (!_openBeliefId) return;
    const ok = window.kazmaConfirm
      ? await window.kazmaConfirm({ title: 'Unlink belief?', message: 'Soft-invalidate this edge from active memory.' })
      : confirm('Unlink (invalidate) belief?');
    if (!ok) return;
    await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(_openBeliefId) + '/invalidate', { method: 'POST' });
    const d = document.getElementById('v2-belief-drawer');
    if (d) d.style.display = 'none';
    loadV2Beliefs();
    pollV2Health();
    if (typeof window._v2gForceReload === 'function') window._v2gForceReload();
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'unlink', beliefId: _openBeliefId } }));
    } catch (e) { /* ignore */ }
  });

  async function loadV2Queue() {
    const el = document.getElementById('v2-queue-table');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/queue?limit=20');
      const data = await resp.json();
      const tasks = data.tasks || [];
      if (!tasks.length) { el.textContent = 'Queue empty.'; return; }
      el.innerHTML = tasks.map(function(t) {
        const st = t.status || '';
        const retry = st === 'failed'
          ? ' <button type="button" data-retry="' + _esc(t.id) + '" class="v2-queue-retry" style="font-size:0.65rem;padding:1px 6px;cursor:pointer;">retry</button>'
          : '';
        return '<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);">' +
          _esc(t.task_type) + ' · ' + _esc(st) + ' · a' + (t.attempts||0) + retry + '</div>';
      }).join('');
      el.querySelectorAll('.v2-queue-retry').forEach(function(btn) {
        btn.addEventListener('click', async function() {
          const id = btn.getAttribute('data-retry');
          await fetch('/api/memory/v2/queue/' + encodeURIComponent(id) + '/retry', { method: 'POST' });
          loadV2Queue();
        });
      });
    } catch (e) { el.textContent = 'Queue load failed'; }
  }
  document.getElementById('v2-queue-clear-failed')?.addEventListener('click', async function() {
    const ok = window.kazmaConfirm
      ? await window.kazmaConfirm({ title: 'Clear failed tasks?', message: 'Permanently delete dead-letter queue rows.' })
      : confirm('Clear all failed queue tasks?');
    if (!ok) return;
    try {
      const r = await fetch('/api/memory/v2/queue/clear-failed', { method: 'POST' });
      const d = await r.json();
      if (window.showToast) window.showToast(d.ok ? ('Cleared ' + (d.deleted || 0) + ' failed') : (d.error || 'Failed'), d.ok ? 'success' : 'error');
      loadV2Queue();
      pollV2Health();
    } catch (e) { /* silent */ }
  });

  async function loadV2Merges() {
    const el = document.getElementById('v2-merges-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/entity-merges?limit=20');
      const data = await resp.json();
      const merges = data.merges || [];
      if (!merges.length) { el.textContent = 'No pending merges.'; return; }
      el.innerHTML = merges.map(function(m) {
        return '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
          '<span>' + _esc(m.source_entity_id) + ' → ' + _esc(m.target_entity_id) +
          ' <span style="color:var(--text-muted);">(' + _esc(m.merge_tier) + ' · ' +
          (m.confidence != null ? Number(m.confidence).toFixed(2) : '') + ')</span></span>' +
          '<button type="button" data-mid="' + _esc(m.id) + '" data-act="approve" class="v2-merge-act btn btn-sm" style="font-size:0.65rem;padding:1px 6px;" aria-label="Approve">' + KazmaIcons.span('check') + '</button>' +
          '<button type="button" data-mid="' + _esc(m.id) + '" data-act="reject" class="v2-merge-act btn btn-sm" style="font-size:0.65rem;padding:1px 6px;" aria-label="Reject">' + KazmaIcons.span('x') + '</button>' +
          '</div>';
      }).join('');
      el.querySelectorAll('.v2-merge-act').forEach(function(btn) {
        btn.addEventListener('click', async function() {
          const id = btn.getAttribute('data-mid');
          const act = btn.getAttribute('data-act');
          await fetch('/api/memory/v2/entity-merges/' + encodeURIComponent(id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: act }),
          });
          loadV2Merges();
        });
      });
    } catch (e) { el.textContent = 'Merges load failed'; }
  }

  async function runFederatedSearch() {
    const input = document.getElementById('v2-probe-input');
    const out = document.getElementById('v2-probe-results');
    const sum = document.getElementById('v2-fed-summary');
    if (!input || !out) return;
    const q = (input.value || '').trim();
    if (!q) { out.textContent = 'Enter a query.'; return; }
    out.textContent = 'Searching memory + knowledge…';
    if (sum) sum.textContent = '';
    const includeKb = !!(document.getElementById('v2-fed-include-kb') || {}).checked;
    try {
      const resp = await fetch('/api/memory/v2/federated-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: q,
          limit_memory: 5,
          limit_kb: 5,
          include_memory: true,
          include_knowledge: includeKb,
        }),
      });
      const data = await resp.json();
      if (!data.ok) { out.textContent = data.error || 'Search failed'; return; }
      const s = data.summary || {};
      if (sum) {
        sum.textContent = 'memory: ' + (s.memory || 0) + ' · knowledge: ' + (s.knowledge || 0) +
          ' · total: ' + (s.total || 0) + '  (stores stay separate — labels only)';
      }
      const lines = (data.hits || []).map(function(h) {
        const store = h.store === 'knowledge' ? 'KB' : 'MEM';
        const color = h.store === 'knowledge' ? '#fbbf24' : '#2ed573';
        const kind = (h.kind || '').toUpperCase();
        const prov = h.provenance || {};
        const extra = h.store === 'knowledge'
          ? (prov.document_title || prov.source_url || prov.library_id || '')
          : ((h.sources && h.sources.join(',')) || h.source || '');
        return '<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,0.04);">' +
          '<span style="color:' + color + ';font-size:0.65rem;font-weight:700;">' + store + '/' + kind + '</span> ' +
          _esc((h.content || '').slice(0, 220)) +
          (extra ? ' <span style="color:var(--text-muted);font-size:0.68rem;">[' + _esc(String(extra).slice(0, 80)) + ']</span>' : '') +
          '</div>';
      });
      _v2gCollectSeedsFromHits(data.hits || []);
      const qWords = (document.getElementById('v2-probe-input') || {}).value || '';
      qWords.split(/\s+/).forEach(function(w) { if (w.length > 2) _v2gLastQuerySeeds.push(w); });
      if (lines.length) {
        out.innerHTML = lines.join('') +
          '<button type="button" class="btn btn-sm" id="v2-fed-path-btn" style="margin-top:8px;font-size:0.72rem;">Show path on graph →</button>';
        document.getElementById('v2-fed-path-btn')?.addEventListener('click', function() { _v2gApplyPathFromQuery(); });
      } else {
        out.innerHTML = '<span style="color:var(--text-muted);">No hits in either store.</span>';
      }
    } catch (e) {
      out.textContent = 'Federated search error: ' + e;
    }
  }
  async function runGoldenEval() {
    const out = document.getElementById('v2-probe-results');
    if (out) out.textContent = 'Running golden eval…';
    try {
      const resp = await fetch('/api/memory/v2/eval/golden', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ include_optional: false }),
      });
      const data = await resp.json();
      if (!out) return;
      if (!data.ok && data.error && !data.total) {
        out.textContent = data.error || 'Eval failed';
        return;
      }
      const lines = [
        '<div style="margin-bottom:6px;"><strong>Golden eval</strong> — pass ' +
        (data.passed || 0) + '/' + (data.total || 0) +
        ' (rate ' + ((data.pass_rate != null) ? data.pass_rate : '—') + ')' +
        (data.skipped ? ' · skipped ' + data.skipped : '') + '</div>'
      ];
      (data.cases || []).forEach(function(c) {
        const color = c.status === 'pass' ? '#2ed573' : (c.status === 'skipped' ? '#94a3b8' : '#f87171');
        lines.push('<div style="font-size:0.72rem;margin-bottom:2px;"><span style="color:' + color + ';font-weight:700;">' +
          _esc(c.status || '?').toUpperCase() + '</span> ' + _esc(c.id || '') +
          (c.query ? ' — ' + _esc(c.query) : '') + '</div>');
      });
      out.innerHTML = lines.join('');
    } catch (e) {
      if (out) out.textContent = 'Eval error: ' + e;
    }
  }
  document.getElementById('v2-probe-btn')?.addEventListener('click', runV2Probe);
  document.getElementById('v2-federated-btn')?.addEventListener('click', runFederatedSearch);
  document.getElementById('v2-probe-input')?.addEventListener('keydown', function(ev) {
    if (ev.key === 'Enter') {
      if (ev.shiftKey) runV2Probe();
      else runFederatedSearch();
    }
  });
  document.getElementById('v2-queue-refresh')?.addEventListener('click', loadV2Queue);
  document.getElementById('v2-merges-refresh')?.addEventListener('click', loadV2Merges);
  document.getElementById('v2-reconsolidate-btn')?.addEventListener('click', async function() {
    try {
      const r = await fetch('/api/memory/v2/reconsolidate', { method: 'POST' });
      const d = await r.json();
      if (window.showToast) window.showToast(d.ok ? 'Reconsolidation enqueued' : (d.error || 'Failed'), d.ok ? 'success' : 'error');
      loadV2Queue();
    } catch (e) { /* silent */ }
  });
  async function loadV2Procedural() {
    const el = document.getElementById('v2-procedural-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/procedural?limit=15');
      const data = await resp.json();
      const dags = data.dags || [];
      if (!dags.length) { el.textContent = 'No active skills yet.'; return; }
      el.innerHTML = dags.map(function(d) {
        return '<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);">' +
          '<b style="color:var(--text-primary);">' + _esc(d.name || d.id) + '</b> · C=' +
          (d.confidence != null ? Number(d.confidence).toFixed(2) : '?') +
          ' · n=' + (d.total_trials || 0) +
          '<div style="color:var(--text-muted);">' + _esc((d.description || '').slice(0, 80)) + '</div></div>';
      }).join('');
    } catch (e) { el.textContent = 'Skills load failed'; }
  }
  async function loadV2Quality() {
    const scoreEl = document.getElementById('v2-quality-score');
    const detEl = document.getElementById('v2-quality-detail');
    if (!scoreEl) return;
    try {
      const resp = await fetch('/api/memory/v2/quality');
      const data = await resp.json();
      if (!data.ok) { scoreEl.textContent = '–'; return; }
      scoreEl.textContent = (data.grade || '') + ' ' + (data.score != null ? data.score + '%' : '');
      if (detEl) {
        detEl.textContent = (data.passed || 0) + '/' + (data.total || 0) + ' checks · ' +
          (data.checks || []).filter(function(c) { return !c.ok; }).map(function(c) { return c.name; }).join(', ') || 'all green';
      }
    } catch (e) { if (scoreEl) scoreEl.textContent = '–'; }
  }
  document.getElementById('v2-procedural-refresh')?.addEventListener('click', loadV2Procedural);
  loadV2Queue();
  loadV2Merges();
  loadV2Procedural();
  loadV2Quality();
  setInterval(function() { if (document.hidden) return; loadV2Queue(); loadV2Merges(); loadV2Procedural(); loadV2Quality(); }, 15000);

  async function loadV2Beliefs(q) {
    try {
      const url = '/api/memory/v2/beliefs' + (q ? ('?q=' + encodeURIComponent(q)) : '');
      const resp = await fetch(url);
      const data = await resp.json();
      const list = document.getElementById('v2-belief-list');
      if (!list) return;
      const beliefs = data.beliefs || [];
      const emptyCta = document.getElementById('v2-belief-empty-cta');
      if (!beliefs.length) {
        list.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:0.78rem;">No active beliefs yet.</div>';
        if (emptyCta) emptyCta.style.display = 'block';
        return;
      }
      if (emptyCta) emptyCta.style.display = 'none';
      const ptypeColor = { functional:'#2ed573', set:'#60a5fa', state:'#fbbf24' };
      list.innerHTML = beliefs.map((b, idx) => {
        const conf = Math.round((b.confidence||0) * 100);
        const pc = ptypeColor[b.predicate_type] || '#94a3b8';
        const objRaw = String(b.object || '');
        const objFull = _esc(objRaw);
        const objShown = objRaw.length > 160 ? objFull.slice(0, 160) + '…' : objFull;
        return '<div class="v2-belief-row" data-id="' + _esc(b.id) + '" data-subject="' + _esc(b.subject) + '" data-object="' + _esc(b.object) + '" data-predicate="' + _esc(b.predicate) + '" style="padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.04);display:flex;align-items:flex-start;gap:8px;cursor:pointer;transition:background 0.15s;" onmouseover="this.style.background=\'rgba(99,102,241,0.08)\'" onmouseout="this.style.background=\'transparent\'">' +
               '<span style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:' + pc + '22;color:' + pc + ';text-transform:uppercase;font-weight:600;flex-shrink:0;margin-top:2px;">' + (b.predicate_type||'?') + '</span>' +
               '<div style="flex:1;color:var(--text-primary);min-width:0;word-break:break-word;line-height:1.4;"><b>' + _esc(b.subject) + '</b> ' + _esc(b.predicate.replace(/_/g,' ')) + ' <b title="' + objFull + '">' + objShown + '</b></div>' +
               '<span style="font-size:0.68rem;color:var(--text-muted);font-family:var(--font-mono);flex-shrink:0;margin-top:2px;">i' + b.structural_importance + ' · ' + conf + '%</span>' +
               '</div>';
      }).join('');
      // Make each belief row clickable — drawer + highlight graph
      list.querySelectorAll('.v2-belief-row').forEach(function(row) {
        row.addEventListener('click', function() {
          var subj = row.getAttribute('data-subject');
          var obj = row.getAttribute('data-object');
          var bid = row.getAttribute('data-id');
          if (bid) openBeliefDrawer(bid);
          _v2gSelectByBelief(subj, obj);
          list.querySelectorAll('.v2-belief-row').forEach(function(r) { r.style.background = 'transparent'; r.style.borderLeft = ''; });
          row.style.background = 'rgba(99,102,241,0.12)';
          row.style.borderLeft = '3px solid #3b82f6';
        });
      });
    } catch (e) { /* silent */ }
  }

  // Select a belief's OBJECT node in the graph canvas + zoom to it.
  // The object is the interesting entity (e.g. "teal", "Paris"), not
  // the subject (usually "user"). Highlights both endpoints + the edge.
  var _v2gHighlightSubj = null, _v2gHighlightObj = null;
  function _v2gFindNodeIndex(key) {
    if (key == null || key === '') return -1;
    var k = String(key);
    var kLow = k.toLowerCase();
    var slug = _v2gSlugify(k);
    for (var i = 0; i < _v2gPts.length; i++) {
      var pid = String(_v2gPts[i].id || '');
      var plabel = String(_v2gPts[i].fullLabel || _v2gPts[i].label || '').toLowerCase();
      var pname = String(_v2gPts[i].name || '').toLowerCase();
      if (pid === k || pid.toLowerCase() === kLow || pid === slug) return i;
      if (plabel === kLow || pname === kLow) return i;
    }
    return -1;
  }

  function _v2gZoomToIndex(idx) {
    if (idx < 0 || !_v2gPts[idx]) return;
    var p = _v2gPts[idx];
    var size = _v2gCanvasSize();
    if (size) {
      _v2gView.scale = 2.5;
      _v2gView.ox = size.w / 2 - p.x * 2.5;
      _v2gView.oy = size.h / 2 - p.y * 2.5;
    }
    _v2gHeated();
    _v2gRepaint();
  }

  function _v2gNotifyList(detail) {
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-graph-select', { detail: detail || {} }));
    } catch (e) { /* ignore */ }
  }

  function _v2gSelectByBelief(subj, obj, beliefId) {
    if (!_v2gPts.length) return false;
    var objIdx = _v2gFindNodeIndex(obj);
    var subjIdx = _v2gFindNodeIndex(subj);
    var targetIdx = objIdx >= 0 ? objIdx : subjIdx;
    if (targetIdx < 0) return false;
    var p = _v2gPts[targetIdx];
    _v2gSelectedId = p.id;
    _v2gHighlightSubj = subjIdx >= 0 ? _v2gPts[subjIdx].id : null;
    _v2gHighlightObj = objIdx >= 0 ? _v2gPts[objIdx].id : null;
    _v2gInspect(p);
    _v2gZoomToIndex(targetIdx);
    return true;
  }

  /** Focus a node by entity id (from Entities table). Returns true if found. */
  function _v2gSelectEntity(entityId, opts) {
    opts = opts || {};
    var id = String(entityId || opts.graphId || '').trim();
    if (!id) return false;
    // Self person shells (ent_* User/Mubder) map to hub id=user on the canvas
    var focusId = String(opts.graphId || id).trim();
    var tryIds = [focusId, id];
    if (opts.isSelf || opts.graphId === 'user') {
      tryIds.unshift('user');
    }
    // Clear search filter that may hide the node
    var searchEl = document.getElementById('v2g-search');
    if (searchEl && searchEl.value) {
      searchEl.value = '';
      _v2gApplyFilters();
    }
    var idx = -1;
    for (var t = 0; t < tryIds.length && idx < 0; t++) {
      idx = _v2gFindNodeIndex(tryIds[t]);
    }
    // Match by display name (e.g. list says Mubder, hub labeled Mubder)
    if (idx < 0 && opts.name) {
      var want = String(opts.name).toLowerCase();
      for (var i = 0; i < _v2gPts.length; i++) {
        var dn = _v2gDisplayName(_v2gPts[i]).toLowerCase();
        if (dn === want) { idx = i; break; }
      }
    }
    if (idx < 0) {
      // Node may be outside current filter — clear entity-type filters once
      var hadFilter = Object.keys(_v2gFilters.entity || {}).length > 0;
      if (hadFilter) {
        _v2gFilters.entity = {};
        _v2gRenderFilters();
        _v2gApplyFilters();
        for (var t2 = 0; t2 < tryIds.length && idx < 0; t2++) {
          idx = _v2gFindNodeIndex(tryIds[t2]);
        }
      }
    }
    // Soft-inject hub/self node if still missing (empty person shell)
    if (idx < 0 && (opts.isSelf || focusId === 'user' || id === 'user')) {
      var label = opts.name || 'You';
      _v2gRawNodes = _v2gRawNodes || [];
      var exists = false;
      for (var r = 0; r < _v2gRawNodes.length; r++) {
        if (_v2gRawNodes[r] && String(_v2gRawNodes[r].id) === 'user') {
          _v2gRawNodes[r].name = label;
          _v2gRawNodes[r].isHub = true;
          exists = true;
          break;
        }
      }
      if (!exists) {
        _v2gRawNodes.push({
          id: 'user',
          name: label,
          type: 'person',
          beliefCount: 0,
          isHub: true,
          isHighStakes: true,
        });
      }
      _v2gStructSig = '';
      _v2gLabelSig = '';
      _v2gApplyFilters();
      idx = _v2gFindNodeIndex('user');
    }
    if (idx < 0) return false;
    var p = _v2gPts[idx];
    _v2gSelectedId = p.id;
    _v2gHighlightSubj = null;
    _v2gHighlightObj = null;
    _v2gInspect(p);
    _v2gZoomToIndex(idx);
    if (opts.notify !== false) {
      _v2gNotifyList({ type: 'entity', id: p.id, name: _v2gDisplayName(p) });
    }
    return true;
  }

  function _v2gSlugify(s) {
    return String(s || '').trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  }

  function _esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // Wire V2 controls + boot
  try {
    const v2Refresh = document.getElementById('v2-refresh-btn');
    if (v2Refresh) v2Refresh.addEventListener('click', () => { pollV2Health(); loadV2Beliefs(document.getElementById('v2-belief-search').value); });
    const v2Search = document.getElementById('v2-belief-search');
    if (v2Search) {
      let _v2STo;
      v2Search.addEventListener('input', () => { clearTimeout(_v2STo); _v2STo = setTimeout(() => loadV2Beliefs(v2Search.value), 250); });
    }
    pollV2Health();
    loadV2Beliefs('');
    setInterval(function() { if (!document.hidden) pollV2Health(); }, 5000);
  } catch (e) { /* V2 panel optional */ }

  // ══ V2 BELIEF TOPOLOGY GRAPH ══════════════════════════════════
  // Self-contained force-directed canvas for the V2 belief graph.
  // Separate _v2g* namespace — does NOT touch the L2 _kg* state.
  // Features: entity nodes (colored by type, sized by belief count),
  // belief edges (colored by predicate_type, dashed if superseded),
  // high-stakes red halo, bi-temporal time slider, filter toggles.

  var _v2gPts = [], _v2gEdges = [], _v2gIds = {}, _v2gStructSig = '', _v2gLabelSig = '';
  var _v2gView = { scale: 1, ox: 0, oy: 0 };
  // User-dragged positions survive layout rebuilds / 30s refresh / filter retune.
  // pinned: physics does not pull the node back after the user places it.
  var _v2gPosCache = {};
  var _v2gAlpha = 0, _v2gAnim = null, _v2gDrag = null, _v2gHover = -1, _v2gSelectedId = null;
  var _v2gCap = 80, _v2gNodeBaseR = 7;
  var _v2gMinScale = 0.3, _v2gMaxScale = 4;
  var _v2gTimeRange = { min: 0, max: 0 };
  var _v2gFilters = { entity: {}, predicate: {} };
  // Cache the last full dataset so client-side filters don't need a re-fetch
  var _v2gRawNodes = [], _v2gRawLinks = [];
  // F2: count of nodes that survived the entity-type filter but lost all their
  // links (dimmed, badged, not dropped). Exposed for the ops-bar indicator.
  var _v2gIsolatedCount = 0;
  // P2: view-only groupings (member → parent + tier) fetched at render so the
  // tree layout's group-spring has data. Populated by _v2gLoad.
  var _v2gGroups = [];
  // Graph-native ops: source/target slots + pick modes (link | merge)
  // Shared with the Entities list via kazma:memory-ops-slots events.
  var _v2gOps = {
    sourceId: null,
    targetId: null,
    mode: null, // null | 'link' | 'merge'
    selectedEdgeIdx: -1,
  };

  // Palette follows site tokens (cyan accent + blue secondary).
  // The "user" node is intentionally warm amber so it stands out.
  function _v2gCss(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (e) { return fallback; }
  }
  function _v2gTheme() {
    var accent = _v2gCss('--accent', '#22d3ee');
    var accentLight = _v2gCss('--accent-light', '#67e8f9');
    var secondary = _v2gCss('--secondary', '#3b82f6');
    var secondaryLight = _v2gCss('--secondary-light', '#93c5fd');
    var user = _v2gCss('--warning', '#f59e0b');
    return {
      accent: accent,
      accentLight: accentLight,
      secondary: secondary,
      secondaryLight: secondaryLight,
      user: user,
      // Soft type variation — all stay in site blue/cyan family
      type: {
        person: secondary,
        tool: accent,
        concept: accentLight,
        location: secondaryLight,
        project: '#38bdf8',
        entity: '#7dd3fc',
      },
      pred: {
        functional: accent,
        set: secondaryLight,
        state: '#38bdf8',
      },
    };
  }
  // Legacy maps rebuilt from theme (filters / legend)
  function _v2gTypeColors() {
    var t = _v2gTheme();
    return Object.assign({ user: t.user }, t.type);
  }
  function _v2gPredColors() {
    return _v2gTheme().pred;
  }
  // Keep symbols for older call sites that read the maps directly
  var _V2G_TYPE_COLORS = _v2gTypeColors();
  var _V2G_PRED_COLORS = _v2gPredColors();
  function _v2gRefreshPalette() {
    _V2G_TYPE_COLORS = _v2gTypeColors();
    _V2G_PRED_COLORS = _v2gPredColors();
  }
  function _v2gIsUser(p) {
    if (!p) return false;
    if (p.isHub || p.isUser) return true;
    var id = String(p.id || '').toLowerCase().trim();
    if (id === 'user' || id === 'you' || id === 'me') return true;
    // Display name can be "Mubder" while id stays user — already handled by isUser.
    // Leak self shells: ent_* / person named You/User (not collapsed onto hub).
    var name = String(p.name || p.fullLabel || p.label || '').toLowerCase().trim();
    if (name === 'you' || name === 'user' || name === 'me') return true;
    return false;
  }

  /** True if neighbor is the memory hub (You/Mubder) — used for Cut hub / shortcut banner. */
  function _v2gIsHubNeighbor(other) {
    if (!other) return false;
    if (_v2gIsUser(other)) return true;
    // Fallback: only one hub-colored center exists; match by id user even if flags missing
    var id = String(other.id || '').toLowerCase();
    if (id === 'user') return true;
    return false;
  }

  // Display label for a node. Id stays canonical (user, shipx); name is the
  // user-editable brand/label. Hub defaults to "You" until renamed (e.g. Mubder).
  function _v2gDisplayName(p) {
    if (!p) return '';
    var raw = String(p.name || p.fullLabel || p.label || p.id || '').trim();
    // Strip legacy "You (user)" fullLabel if still present as sole source
    if (/^you\s*\(user\)$/i.test(raw)) raw = 'You';
    if (_v2gIsUser(p)) {
      var low = raw.toLowerCase();
      if (!raw || low === 'user' || low === 'you' || low === 'me') return 'You';
      return raw; // e.g. Mubder / Kazma
    }
    return raw || String(p.id || '');
  }
  // F: tier color palette. The operator organizes nodes into a tiered tree
  // (0=main/hub, 1=major, 2=sub, 3=leaf). Grouped nodes take their tier
  // color; ungrouped nodes (tier -1/undefined) fall back to the entity-type
  // color. The hub keeps its amber user color regardless.
  var _V2G_TIER_COLORS = {
    0: '#f59e0b',  // main  — amber/orange (the hub; matches existing hub style)
    1: '#3b82f6',  // major — royal blue
    2: '#3b82f6',  // sub   — blue
    3: '#94a3b8',  // leaf  — slate
  };
  function _v2gTierColor(tier) {
    return _V2G_TIER_COLORS[tier];
  }

  function _v2gNodeColor(p) {
    var t = _v2gTheme();
    if (_v2gIsUser(p)) return t.user;
    // Major nodes get a distinct violet color — stands out from tier palette
    // and hub amber, so the operator's marked projects/hubs are instantly visible.
    if (p && p.isMajor) return '#a855f7';
    // Tier color wins for grouped nodes (tier 0-3). -1/undefined → type color.
    if (p && p.tier !== undefined && p.tier >= 0 && _V2G_TIER_COLORS[p.tier]) {
      return _V2G_TIER_COLORS[p.tier];
    }
    return (t.type[p.type] || t.accent);
  }
  function _v2gHexAlpha(hex, a) {
    // #rgb / #rrggbb → rgba()
    if (!hex || hex[0] !== '#') return hex;
    var h = hex.slice(1);
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return hex;
    var r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  function _v2gSX(wx) { return wx * _v2gView.scale + _v2gView.ox; }
  function _v2gSY(wy) { return wy * _v2gView.scale + _v2gView.oy; }
  function _v2gWX(sx) { return (sx - _v2gView.ox) / _v2gView.scale; }
  function _v2gWY(sy) { return (sy - _v2gView.oy) / _v2gView.scale; }
  function _v2gFont(px) {
    var fam = 'sans-serif'; var p = document.getElementById('v2g-canvas-wrap');
    if (p) { var v = getComputedStyle(p).fontFamily; if (v && String(v).indexOf('var(') === -1) fam = v; }
    return px + 'px ' + fam;
  }

  function _v2gCanvasSize() {
    var wrap = document.getElementById('v2g-canvas-wrap');
    var canvas = document.getElementById('v2g-canvas');
    if (!wrap || !canvas) return null;
    var rect = wrap.getBoundingClientRect();
    var w = Math.max(320, Math.floor(rect.width));
    var h = Math.max(240, Math.floor(rect.height));
    var dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    var ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, w: w, h: h };
  }

  function _v2gIsPinned(pt) {
    return !!(pt && (pt.pinned || (_v2gPosCache[pt.id] && _v2gPosCache[pt.id].pinned)));
  }

  function _v2gRememberPos(pt) {
    if (!pt || pt.id == null) return;
    _v2gPosCache[pt.id] = {
      x: pt.x,
      y: pt.y,
      pinned: !!pt.pinned,
    };
  }

  // Force sim step: repulsion + spring + gravity + collision
  function _v2gStep(W, H) {
    var n = _v2gPts.length; if (!n) return;
    var cx = W / 2, cy = H / 2;
    // Repulsion (Coulomb) — O(n²) but capped at _v2gCap nodes
    for (var i = 0; i < n; i++) {
      for (var j = i + 1; j < n; j++) {
        var a = _v2gPts[i], b = _v2gPts[j];
        var dx = b.x - a.x, dy = b.y - a.y;
        var d2 = dx * dx + dy * dy + 0.01;
        var d = Math.sqrt(d2);
        var force = 900 * _v2gAlpha / d2;
        var fx = (dx / d) * force, fy = (dy / d) * force;
        // Pinned / dragged nodes are fixed anchors (user layout sticks)
        if (!(_v2gDrag && _v2gDrag.idx === i) && !_v2gIsPinned(a)) { a.vx -= fx; a.vy -= fy; }
        if (!(_v2gDrag && _v2gDrag.idx === j) && !_v2gIsPinned(b)) { b.vx += fx; b.vy += fy; }
      }
    }
    // Spring (Hooke) along edges, weighted by confidence
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e]; var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var dx = B.x - A.x, dy = B.y - A.y;
      var d = Math.sqrt(dx * dx + dy * dy + 0.01);
      var targetLen = 70;
      var k = 0.04 * (0.5 + (ed.confidence || 0.5));
      var f = (d - targetLen) * k * _v2gAlpha;
      var fx = (dx / d) * f, fy = (dy / d) * f;
      if (!(_v2gDrag && _v2gDrag.idx === ed.a) && !_v2gIsPinned(A)) { A.vx += fx; A.vy += fy; }
      if (!(_v2gDrag && _v2gDrag.idx === ed.b) && !_v2gIsPinned(B)) { B.vx -= fx; B.vy -= fy; }
    }
    // P2d: group-spring — holds grouped children in a tier-relative orbit
    // around their parent. Stiffer than belief-edges (k=0.06 vs 0.04) so a
    // cross-cluster belief edge doesn't pull a child out of its cluster.
    // Mutates vx/vy only (matches the existing force contract); reuses the
    // same pinned/dragged guards. Ungrouped nodes (groupParent === -1) skip.
    for (var gi = 0; gi < n; gi++) {
      var gp = _v2gPts[gi];
      if (!gp || gp.groupParent === undefined || gp.groupParent < 0) continue;
      var par = _v2gPts[gp.groupParent];
      if (!par) continue;
      var gdx = par.x - gp.x, gdy = par.y - gp.y;
      var gd = Math.sqrt(gdx * gdx + gdy * gdy + 0.01);
      var gTarget = 50 + (gp.tier >= 0 ? gp.tier : 1) * 30;
      var gk = 0.06 * _v2gAlpha;
      var gf = (gd - gTarget) * gk;
      var gfx = (gdx / gd) * gf, gfy = (gdy / gd) * gf;
      // Pull the child toward the orbit distance; the parent stays anchored
      // when pinned (the guard skips it), so dragged parents don't jump.
      if (!(_v2gDrag && _v2gDrag.idx === gi) && !_v2gIsPinned(gp)) {
        gp.vx += gfx; gp.vy += gfy;
      }
      if (!(_v2gDrag && _v2gDrag.idx === gp.groupParent) && !_v2gIsPinned(par)) {
        par.vx -= gfx; par.vy -= gfy;
      }
    }
    // Gravity + collision-aware integration
    var margin = 30;
    for (var p = 0; p < n; p++) {
      var pt = _v2gPts[p];
      if (_v2gDrag && _v2gDrag.idx === p) { pt.vx = 0; pt.vy = 0; continue; }
      if (_v2gIsPinned(pt)) {
        // Stay exactly where the user left it (refresh cache continuously)
        pt.vx = 0; pt.vy = 0;
        _v2gRememberPos(pt);
        continue;
      }
      pt.vx += (cx - pt.x) * 0.004 * _v2gAlpha;
      pt.vy += (cy - pt.y) * 0.004 * _v2gAlpha;
      if (pt.x < margin) pt.vx += (margin - pt.x) * 0.02 * _v2gAlpha;
      if (pt.x > W - margin) pt.vx -= (pt.x - (W - margin)) * 0.02 * _v2gAlpha;
      if (pt.y < margin) pt.vy += (margin - pt.y) * 0.02 * _v2gAlpha;
      if (pt.y > H - margin) pt.vy -= (pt.y - (H - margin)) * 0.02 * _v2gAlpha;
      pt.vx *= 0.82; pt.vy *= 0.82;
      var sp = Math.sqrt(pt.vx * pt.vx + pt.vy * pt.vy);
      if (sp > 14) { pt.vx = pt.vx / sp * 14; pt.vy = pt.vy / sp * 14; }
      pt.x += pt.vx; pt.y += pt.vy;
    }
    _v2gAlpha *= 0.985;
    if (_v2gAlpha < 0.004) _v2gAlpha = 0;
  }

  function _v2gHeated() { _v2gAlpha = Math.max(_v2gAlpha, 0.2); }

  function _v2gDrawCanvas(nodes, links) {
    var size = _v2gCanvasSize(); if (!size) return;
    var ctx = size.ctx, W = size.w, H = size.h;
    var empty = document.getElementById('v2g-empty');
    var canvas = document.getElementById('v2g-canvas');
    var wrap = document.getElementById('v2g-canvas-wrap');
    if (!nodes || !nodes.length) {
      ctx.clearRect(0, 0, W, H);
      if (empty) { empty.style.display = 'flex'; empty.style.flexDirection = 'column'; }
      _v2gPts = []; _v2gEdges = []; _v2gIds = {};
      _v2gStructSig = ''; _v2gLabelSig = '';
      if (_v2gAnim) { cancelAnimationFrame(_v2gAnim); _v2gAnim = null; }
      return;
    }
    if (empty) empty.style.display = 'none';
    var nsIn = nodes.slice(0, _v2gCap);
    // Dedupe by id before indexing. Server should already skip virtual
    // nodes whose id collides with a real entity (e.g. shipx as both
    // entity and belief object). Prefer non-virtual / higher beliefCount.
    var byId = {};
    nsIn.forEach(function(nd) {
      var id = nd && nd.id != null ? String(nd.id) : '';
      if (!id) return;
      var prev = byId[id];
      if (!prev) { byId[id] = nd; return; }
      var prevVirt = !!prev.isVirtual;
      var curVirt = !!nd.isVirtual;
      if (prevVirt && !curVirt) { byId[id] = nd; return; }
      if (!prevVirt && curVirt) return;
      if ((nd.beliefCount || 0) > (prev.beliefCount || 0)) byId[id] = nd;
    });
    var ns = Object.keys(byId).map(function(k) { return byId[k]; });
    function _nodeDisplay(nd) {
      var rawName = String(nd.name || nd.id || '').trim();
      var isUser = !!nd.isHub || String(nd.id || '').toLowerCase() === 'user';
      var display = rawName;
      // Blob compaction: a node whose id/text is >80 chars is a prose note
      // (e.g. a `noted` essay), not a real entity. Compact its display to the
      // first meaningful line + "…" so it renders as a readable chip, not a
      // giant unreadable node. The full text is available via fullLabel on
      // inspect.
      var isBlob = String(nd.id || '').length > 80 || rawName.length > 80;
      if (isBlob && !isUser) {
        var firstLine = rawName.split(/[\n\r]/)[0];
        display = firstLine.length > 40 ? firstLine.slice(0, 40) + '…' : (firstLine + '…');
      }
      if (isUser) {
        var low = rawName.toLowerCase();
        if (!rawName || low === 'user' || low === 'you' || low === 'me') display = 'You';
        // else keep branded name (Mubder, Kazma, …)
      }
      return { rawName: rawName, display: display, isUser: isUser, isBlob: isBlob };
    }
    var idKey = ns.map(function(nd) { return String(nd.id); }).join('\0');
    var labelKey = ns.map(function(nd) {
      var d = _nodeDisplay(nd);
      return String(nd.id) + '=' + d.display + (nd.isVirtual ? 'v' : '');
    }).join('|');
    var structSig = ns.length + ':' + ((links && links.length) || 0) + ':' + idKey.slice(0, 600);
    var labelSig = labelKey.slice(0, 1200);
    // Rename-only refresh: same topology, new display names — update labels
    // in place so the graph reflects list renames without resetting layout.
    if (structSig === _v2gStructSig && _v2gPts.length && labelSig !== _v2gLabelSig) {
      var nameById = {};
      ns.forEach(function(nd) { nameById[nd.id] = nd; });
      _v2gPts.forEach(function(p) {
        var nd = nameById[p.id];
        if (!nd) return;
        var d = _nodeDisplay(nd);
        p.name = d.rawName;
        p.label = d.display.slice(0, 22);
        p.fullLabel = d.display;
        p.isVirtual = !!nd.isVirtual;
        p.isHighStakes = !!nd.isHighStakes;
        p.type = nd.type || p.type;
        // Keep tier in sync if the payload updated it (e.g. after a group op).
        if (typeof nd.tier === 'number') p.tier = nd.tier;
      });
      _v2gLabelSig = labelSig;
      if (_v2gSelectedId) {
        for (var si = 0; si < _v2gPts.length; si++) {
          if (_v2gPts[si].id === _v2gSelectedId) { _v2gInspect(_v2gPts[si]); break; }
        }
      }
      _v2gRepaint();
    } else if (structSig !== _v2gStructSig) {
      var keepSel = _v2gSelectedId;
      var keepView = { scale: _v2gView.scale, ox: _v2gView.ox, oy: _v2gView.oy };
      var hadLayout = _v2gPts.length > 0;
      // Snapshot live positions before rebuild so a data refresh does not
      // fling user-arranged nodes back to the spiral layout.
      _v2gPts.forEach(function(p) { _v2gRememberPos(p); });
      _v2gStructSig = structSig;
      _v2gLabelSig = labelSig;
      _v2gIds = {};
      _v2gPts = ns.map(function(nd, i) {
        _v2gIds[nd.id] = i;
        var ang = i * 2.39996, r = 20 + (i % 6) * 24;
        var bc = nd.beliefCount || 1;
        var d = _nodeDisplay(nd);
        var rad = d.isUser ? 8 : r;
        var cached = _v2gPosCache[nd.id];
        var x = W / 2 + Math.cos(ang) * rad;
        var y = H / 2 + Math.sin(ang) * rad;
        var pinned = false;
        if (cached && typeof cached.x === 'number' && typeof cached.y === 'number') {
          x = cached.x; y = cached.y;
          pinned = !!cached.pinned;
        }
        return {
          x: x, y: y,
          vx: 0, vy: 0, id: nd.id,
          name: d.rawName,
          label: d.display.slice(0, 22),
          fullLabel: d.display,
          type: nd.type || 'entity',
          isUser: d.isUser,
          isHub: !!nd.isHub || d.isUser,
          isHighStakes: !!nd.isHighStakes,
          isMajor: !!nd.isMajor || !!nd.isHub,
          isBlob: !!d.isBlob,
          // Blob chips are compact: small radius so they don't dominate the
          // canvas and the group-spring can move them against other forces.
          r: d.isBlob ? _v2gNodeBaseR - 3
            : (d.isUser ? _v2gNodeBaseR + 4
              : (nd.isMajor ? _v2gNodeBaseR + 2
                : (nd.isEpisode ? _v2gNodeBaseR - 1 : _v2gNodeBaseR)))
              + Math.min(8, Math.sqrt(bc) * 1.5),
          isVirtual: !!nd.isVirtual,
          isEpisode: !!nd.isEpisode,
          tier: (typeof nd.tier === 'number' ? nd.tier : -1),
          pinned: pinned,
        };
      });
      // P2b: stamp groupParent (parent point index) + tier onto each point
      // from the view-only groupings. _v2gIds (id→index) is live here. The
      // group-spring in _v2gStep reads pt.groupParent; -1 = ungrouped.
      if (_v2gGroups && _v2gGroups.length) {
        var memberTier = {};
        var memberParent = {};
        _v2gGroups.forEach(function(g) {
          if (g.member) {
            memberTier[g.member] = g.member_tier;
            if (g.group_root) memberParent[g.member] = g.group_root;
          }
        });
        _v2gPts.forEach(function(p) {
          p.groupParent = -1;
          if (memberParent[p.id] !== undefined) {
            var pidx = _v2gIds[memberParent[p.id]];
            if (pidx !== undefined) p.groupParent = pidx;
          }
          if (p.tier === undefined || p.tier < 0) {
            var t = memberTier[p.id];
            if (t !== undefined) p.tier = t;
          }
        });
        // P2c: seed grouped (non-pinned) nodes in a ring around their parent,
        // ignoring the stale pre-grouping cached position. Pinned nodes (user
        // drag) keep their cache. The group-spring will hold this distance.
        _v2gPts.forEach(function(p, i) {
          if (p.groupParent >= 0 && !_v2gIsPinned(p)) {
            var parent_1 = _v2gPts[p.groupParent];
            if (parent_1) {
              var tlen = 50 + (p.tier >= 0 ? p.tier : 1) * 30;
              var seedAng = i * 2.39996;
              p.x = parent_1.x + Math.cos(seedAng) * tlen;
              p.y = parent_1.y + Math.sin(seedAng) * tlen;
            }
          }
        });
      }
      _v2gEdges = [];
      (links || []).forEach(function(l) {
        var ai = _v2gIds[l.source];
        var bi = _v2gIds[l.target];
        if (ai !== undefined && bi !== undefined) {
          _v2gEdges.push({
            a: ai,
            b: bi,
            // Belief id (from graph API `id`) — required for edit/unlink
            beliefId: l.id || l.belief_id || null,
            label: String(l.label || l.predicate || '').slice(0, 18),
            fullLabel: String(l.label || l.predicate || ''),
            objectText: String(l.object_text || ''),
            sourceId: l.source,
            targetId: l.target,
            type: l.type || 'set',
            confidence: l.confidence || 0.5,
            superseded: !!l.superseded,
          });
        }
      });
      // Drop selected edge if topology rebuilt without it
      if (_v2gOps.selectedEdgeIdx >= _v2gEdges.length) _v2gOps.selectedEdgeIdx = -1;
      // Keep pan/zoom if we already had a layout; only reset on first paint.
      if (hadLayout) {
        _v2gView = keepView;
        // Mild reheat so free (unpinned) nodes settle around pinned anchors
        _v2gAlpha = Math.max(_v2gAlpha, 0.12);
      } else {
        _v2gView = { scale: 1, ox: 0, oy: 0 };
        _v2gAlpha = 1;
      }
      _v2gSelectedId = keepSel && _v2gIds[keepSel] !== undefined ? keepSel : null;
    }
    _v2gBindPointer(canvas, wrap);
    if (!_v2gAnim) _v2gTick();
  }

  function _v2gTick() {
    var size = _v2gCanvasSize(); if (!size) { _v2gAnim = null; return; }
    var ctx = size.ctx, W = size.w, H = size.h;
    if (_v2gAlpha > 0) _v2gStep(W, H);
    _v2gPaint(ctx, W, H);
    // Keep a low-FPS idle loop for You/user breath + high-stakes pulse
    var idle = false;
    for (var i = 0; i < _v2gPts.length; i++) {
      if (_v2gIsUser(_v2gPts[i]) || _v2gPts[i].isHighStakes) { idle = true; break; }
    }
    if (_v2gAlpha > 0 || _v2gDrag || idle) {
      _v2gAnim = requestAnimationFrame(_v2gTick);
    } else {
      _v2gAnim = null;
    }
  }

  function _v2gRepaint() { if (!_v2gAnim) _v2gAnim = requestAnimationFrame(_v2gTick); }

  function _v2gPaint(ctx, W, H) {
    _v2gRefreshPalette();
    var theme = _v2gTheme();
    ctx.clearRect(0, 0, W, H);
    // Soft ambient glow (site accent)
    var amb = ctx.createRadialGradient(W * 0.5, H * 0.42, 8, W * 0.5, H * 0.5, Math.max(W, H) * 0.55);
    amb.addColorStop(0, _v2gHexAlpha(theme.accent, 0.07));
    amb.addColorStop(0.45, _v2gHexAlpha(theme.secondary, 0.03));
    amb.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = amb;
    ctx.fillRect(0, 0, W, H);

    // Edges — accent family, gradient along the link when connected to You
    // Wider hit targets via thicker stroke when zoomed out (pick still uses _v2gHitEdge)
    ctx.font = _v2gFont(10);
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e]; var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var ax = _v2gSX(A.x), ay = _v2gSY(A.y), bx = _v2gSX(B.x), by = _v2gSY(B.y);
      var hot = _v2gSelectedId && (A.id === _v2gSelectedId || B.id === _v2gSelectedId);
      var edgeSelected = e === _v2gOps.selectedEdgeIdx;
      var beliefHot = _v2gHighlightSubj && _v2gHighlightObj &&
        ((A.id === _v2gHighlightSubj && B.id === _v2gHighlightObj) ||
         (A.id === _v2gHighlightObj && B.id === _v2gHighlightSubj));
      var pcolor = _V2G_PRED_COLORS[ed.type] || theme.accent;
      var touchesUser = _v2gIsUser(A) || _v2gIsUser(B);
      ctx.lineCap = 'round';
      var pathHot = !!ed.pathHot || (_v2gPathIds[A.id] && _v2gPathIds[B.id]);
      // Source/target slot rings on edges between ops endpoints
      var opsEdge = (_v2gOps.sourceId && _v2gOps.targetId &&
        ((A.id === _v2gOps.sourceId && B.id === _v2gOps.targetId) ||
         (A.id === _v2gOps.targetId && B.id === _v2gOps.sourceId)));
      if (edgeSelected || beliefHot || pathHot) {
        ctx.strokeStyle = edgeSelected ? '#fbbf24' : theme.accentLight;
        ctx.lineWidth = edgeSelected ? 3.6 : (pathHot ? 3.0 : 3.4);
        ctx.shadowColor = _v2gHexAlpha(edgeSelected ? '#fbbf24' : theme.accent, 0.55); ctx.shadowBlur = 10;
      } else if (opsEdge) {
        ctx.strokeStyle = theme.user; ctx.lineWidth = 2.6;
        ctx.shadowColor = _v2gHexAlpha(theme.user, 0.45); ctx.shadowBlur = 8;
      } else if (hot) {
        ctx.strokeStyle = theme.accent; ctx.lineWidth = 2.3;
        ctx.shadowColor = _v2gHexAlpha(theme.accent, 0.4); ctx.shadowBlur = 8;
      } else if (ed.superseded) {
        ctx.strokeStyle = 'rgba(148,163,184,0.28)'; ctx.lineWidth = 1;
        ctx.shadowBlur = 0;
      } else {
        var grad = ctx.createLinearGradient(ax, ay, bx, by);
        if (touchesUser) {
          grad.addColorStop(0, _v2gHexAlpha(theme.user, 0.75));
          grad.addColorStop(1, _v2gHexAlpha(theme.accent, 0.55));
        } else {
          grad.addColorStop(0, _v2gHexAlpha(pcolor, 0.55));
          grad.addColorStop(1, _v2gHexAlpha(theme.accentLight, 0.4));
        }
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1 + (ed.confidence || 0.5) * 1.6;
        ctx.shadowBlur = 0;
      }
      if (ed.superseded) ctx.setLineDash([4, 3]); else ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
      // Arrowhead
      var ang = Math.atan2(by - ay, bx - ax);
      var ah = 6;
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.lineTo(bx - ah * Math.cos(ang - 0.4), by - ah * Math.sin(ang - 0.4));
      ctx.lineTo(bx - ah * Math.cos(ang + 0.4), by - ah * Math.sin(ang + 0.4));
      ctx.closePath();
      ctx.fillStyle = hot || beliefHot ? theme.accentLight : (ed.superseded ? 'rgba(148,163,184,0.4)' : _v2gHexAlpha(pcolor, 0.85));
      ctx.fill();
      // Label
      if (_v2gView.scale > 0.7 && ed.label) {
        var mx = (ax + bx) / 2, my = (ay + by) / 2;
        var tw = ctx.measureText(ed.label).width;
        ctx.fillStyle = 'rgba(10,15,20,0.82)';
        ctx.beginPath();
        // rounded pill
        var px = mx - tw / 2 - 5, py = my - 9, pw = tw + 10, ph = 15, pr = 4;
        ctx.moveTo(px + pr, py); ctx.arcTo(px + pw, py, px + pw, py + ph, pr);
        ctx.arcTo(px + pw, py + ph, px, py + ph, pr); ctx.arcTo(px, py + ph, px, py, pr);
        ctx.arcTo(px, py, px + pw, py, pr); ctx.closePath(); ctx.fill();
        ctx.strokeStyle = _v2gHexAlpha(theme.accent, 0.2); ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = hot || beliefHot ? theme.accentLight : 'rgba(200,220,235,0.95)';
        ctx.textAlign = 'center'; ctx.fillText(ed.label, mx, my + 3);
      }
    }
    // Nodes
    var showLabels = _v2gView.scale > 0.55;
    ctx.font = _v2gFont(11);
    var tNow = Date.now();
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var x = _v2gSX(p.x), y = _v2gSY(p.y);
      var r = p.r;
      var isHover = (i === _v2gHover), isSel = (p.id === _v2gSelectedId);
      var isUser = _v2gIsUser(p);
      var onPath = !!_v2gPathIds[p.id];
      if (isSel || isHover || onPath) r += 3;
      if (isUser) r += 1;
      var color = p.isEpisode ? _v2gHexAlpha(theme.secondary, 0.55) : _v2gNodeColor(p);
      // Soft outer glow
      var glowR = r + (isUser || onPath ? 10 : 7);
      var glow = ctx.createRadialGradient(x, y, r * 0.2, x, y, glowR);
      glow.addColorStop(0, _v2gHexAlpha(color, isUser || isSel || onPath ? 0.5 : 0.22));
      glow.addColorStop(1, _v2gHexAlpha(color, 0));
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(x, y, glowR, 0, 2 * Math.PI); ctx.fill();
      if (onPath) {
        ctx.beginPath(); ctx.arc(x, y, r + 6, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accentLight; ctx.lineWidth = 2; ctx.stroke();
      }
      // High-stakes red halo (above glow)
      if (p.isHighStakes) {
        var pulse = 0.5 + 0.5 * Math.sin(tNow / 400);
        ctx.beginPath(); ctx.arc(x, y, r + 5 + pulse * 2, 0, 2 * Math.PI);
        ctx.strokeStyle = 'rgba(239,68,68,' + (0.4 + pulse * 0.3) + ')'; ctx.lineWidth = 2; ctx.stroke();
      }
      // User: gentle amber breath
      if (isUser) {
        var up = 0.5 + 0.5 * Math.sin(tNow / 650);
        ctx.beginPath(); ctx.arc(x, y, r + 5 + up * 2.5, 0, 2 * Math.PI);
        ctx.strokeStyle = _v2gHexAlpha(theme.user, 0.35 + up * 0.25); ctx.lineWidth = 2; ctx.stroke();
      }
      // Selected ring (accent)
      if (isSel) {
        ctx.beginPath(); ctx.arc(x, y, r + 5, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accentLight; ctx.lineWidth = 2.2; ctx.stroke();
      }
      // Ops source / target rings (cyan = src, blue = tgt)
      if (_v2gOps.sourceId && p.id === _v2gOps.sourceId) {
        ctx.beginPath(); ctx.arc(x, y, r + 7, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accent; ctx.lineWidth = 2.4; ctx.stroke();
      }
      if (_v2gOps.targetId && p.id === _v2gOps.targetId) {
        ctx.beginPath(); ctx.arc(x, y, r + 9, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.secondary; ctx.lineWidth = 2; ctx.setLineDash([3, 2]); ctx.stroke();
        ctx.setLineDash([]);
      }
      // Blob compaction: prose-note nodes (>80 chars) render as compact
      // rounded-rect chips, not circles — smaller, distinct shape, so they
      // don't dominate the canvas and the group-spring can reposition them.
      if (p.isBlob && !isUser) {
        var chipW = r * 2.4, chipH = r * 1.4;
        var chipAlpha = (p.isolated && !isSel && !isHover) ? 0.4
                      : (p.isVirtual && !isSel && !isHover) ? 0.72 : 1;
        ctx.globalAlpha = chipAlpha;
        // chip body
        ctx.fillStyle = _v2gHexAlpha(color, 0.55);
        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') {
          ctx.roundRect(x - chipW / 2, y - chipH / 2, chipW, chipH, 3);
        } else {
          ctx.rect(x - chipW / 2, y - chipH / 2, chipW, chipH);
        }
        ctx.fill();
        // chip border (dashed = note, not entity)
        ctx.setLineDash([2, 2]);
        ctx.strokeStyle = _v2gHexAlpha(color, 0.8);
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        // label inside the chip (truncated display name + …), clamped to chip width
        if (showLabels) {
          ctx.font = _v2gFont(9);
          var chipLab = p.label;
          while (ctx.measureText(chipLab).width > chipW - 6 && chipLab.length > 3) {
            chipLab = chipLab.slice(0, -2) + '…';
          }
          ctx.fillStyle = '#e6edf3';
          ctx.textAlign = 'center';
          ctx.fillText(chipLab, x, y + 3);
        }
        continue;  // skip the normal circle body below
      }
      // Body — radial highlight for depth
      var body = ctx.createRadialGradient(x - r * 0.3, y - r * 0.35, 0, x, y, r);
      body.addColorStop(0, _v2gHexAlpha('#ffffff', isUser ? 0.45 : 0.28));
      body.addColorStop(0.35, color);
      body.addColorStop(1, _v2gHexAlpha(color, 0.85));
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = body;
      // F2: isolated (link-less under filter) + virtual nodes render faded.
      ctx.globalAlpha = (p.isVirtual && !isSel && !isHover) ? 0.72
                      : (p.isolated && !isSel && !isHover) ? 0.4 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (p.isVirtual) {
        ctx.setLineDash([3, 2]);
        ctx.strokeStyle = _v2gHexAlpha(theme.accentLight, 0.55);
      } else if (p.isolated) {
        // Isolation badge: amber dashed ring so a stranded node is visible,
        // not mistaken for a healthy connected node.
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = 'rgba(245,158,11,0.7)';
      } else {
        ctx.setLineDash([]);
        ctx.strokeStyle = isUser ? _v2gHexAlpha('#fff7ed', 0.55) : 'rgba(255,255,255,0.28)';
      }
      ctx.lineWidth = isUser ? 1.6 : 1;
      ctx.stroke();
      ctx.setLineDash([]);
      // User center mark
      if (isUser && r > 8) {
        ctx.beginPath(); ctx.arc(x, y, Math.max(2.2, r * 0.22), 0, 2 * Math.PI);
        ctx.fillStyle = 'rgba(255,255,255,0.9)'; ctx.fill();
      }
      if (showLabels) {
        var lab = p.label;
        // Clamp the label to fit within a plate proportional to the node
        // radius (so long names don't overflow the card/box). Truncate with
        // … if the measured text exceeds the max width.
        ctx.font = (isUser ? '600 ' : '') + _v2gFont(isUser ? 12 : 11);
        var maxLabelW = Math.max(60, r * 3.2);
        while (ctx.measureText(lab).width > maxLabelW && lab.length > 4) {
          lab = lab.slice(0, -2) + '…';
        }
        // Canvas font: weight + size/family (re-set after measure loop)
        ctx.font = (isUser ? '600 ' : '') + _v2gFont(isUser ? 12 : 11);
        // subtle label plate for readability
        var lw = ctx.measureText(lab).width;
        ctx.fillStyle = 'rgba(10,15,20,0.55)';
        ctx.fillRect(x - lw / 2 - 4, y + r + 3, lw + 8, 14);
        ctx.fillStyle = isUser ? theme.user : '#e6edf3';
        ctx.textAlign = 'center';
        ctx.fillText(lab, x, y + r + 13);
      }
    }
  }

  function _v2gHit(sx, sy) {
    for (var i = _v2gPts.length - 1; i >= 0; i--) {
      var p = _v2gPts[i];
      var dx = sx - _v2gSX(p.x), dy = sy - _v2gSY(p.y);
      if (dx * dx + dy * dy < (p.r + 4) * (p.r + 4)) return i;
    }
    return -1;
  }

  /** Distance from point to segment; used for edge pick (edit/unlink). */
  function _v2gDistToSeg(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1, dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    if (len2 < 1e-6) {
      var d0x = px - x1, d0y = py - y1;
      return Math.sqrt(d0x * d0x + d0y * d0y);
    }
    var t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    var qx = x1 + t * dx, qy = y1 + t * dy;
    var ex = px - qx, ey = py - qy;
    return Math.sqrt(ex * ex + ey * ey);
  }

  function _v2gHitEdge(sx, sy) {
    // Generous hit area so edges are easy to pick for edit/unlink
    var best = -1, bestD = 14;
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e];
      var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var d = _v2gDistToSeg(sx, sy, _v2gSX(A.x), _v2gSY(A.y), _v2gSX(B.x), _v2gSY(B.y));
      if (d < bestD) { bestD = d; best = e; }
    }
    return best;
  }

  // ── Graph ops helpers (link / merge / unlink / edit) ─────────────

  function _v2gToast(msg, type) {
    if (window.showToast) window.showToast(msg, type || 'info');
  }

  async function _v2gConfirm(opts) {
    try {
      if (typeof window.kazmaConfirm === 'function') {
        return !!(await window.kazmaConfirm(opts || {}));
      }
      // window.confirm may be async (overridden by stores.js) — always await
      var native = window._nativeConfirm || window.confirm;
      var res = native.call(window, (opts && (opts.message || opts.title)) || 'Confirm?');
      return !!(await Promise.resolve(res));
    } catch (e) {
      return false;
    }
  }

  async function _v2gPrompt(opts) {
    try {
      if (typeof window.kazmaPrompt === 'function') {
        return await window.kazmaPrompt(opts || {});
      }
      var native = window._nativePrompt || window.prompt;
      var res = native.call(
        window,
        (opts && opts.message) || '',
        (opts && opts.defaultValue) || ''
      );
      return await Promise.resolve(res);
    } catch (e) {
      return null;
    }
  }

  async function _v2gApiJson(url, opts) {
    opts = opts || {};
    var resp = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        Accept: 'application/json',
      },
      method: opts.method || 'GET',
      body: opts.body != null ? opts.body : undefined,
    });
    var data = {};
    var text = '';
    try {
      text = await resp.text();
      data = text ? JSON.parse(text) : {};
    } catch (e) {
      data = { ok: false, error: text ? text.slice(0, 160) : ('HTTP ' + resp.status) };
    }
    if (!resp.ok && data.ok == null) data.ok = false;
    if (!resp.ok && !data.error) data.error = 'HTTP ' + resp.status;
    data._status = resp.status;
    return data;
  }

  function _v2gShortId(id) {
    var s = String(id || '');
    return s.length > 18 ? s.slice(0, 16) + '…' : s;
  }

  function _v2gSyncOpsBar() {
    var srcEl = document.getElementById('v2g-ops-source');
    var tgtEl = document.getElementById('v2g-ops-target');
    var hint = document.getElementById('v2g-ops-hint');
    var linkBtn = document.getElementById('v2g-ops-link');
    var mergeBtn = document.getElementById('v2g-ops-merge');
    if (srcEl) {
      srcEl.textContent = 'src: ' + (_v2gOps.sourceId ? _v2gShortId(_v2gOps.sourceId) : '—');
      srcEl.title = _v2gOps.sourceId || 'Source entity';
      srcEl.style.borderColor = _v2gOps.sourceId ? 'var(--accent,#22d3ee)' : 'var(--border-subtle)';
    }
    if (tgtEl) {
      tgtEl.textContent = 'tgt: ' + (_v2gOps.targetId ? _v2gShortId(_v2gOps.targetId) : '—');
      tgtEl.title = _v2gOps.targetId || 'Target entity';
      tgtEl.style.borderColor = _v2gOps.targetId ? 'var(--secondary,#3b82f6)' : 'var(--border-subtle)';
    }
    if (hint) {
      if (_v2gOps.mode === 'link') {
        hint.textContent = _v2gOps.sourceId
          ? 'Link mode: click the target node…'
          : 'Link mode: click the source node…';
        hint.style.color = 'var(--accent,#22d3ee)';
      } else if (_v2gOps.mode === 'merge') {
        hint.textContent = _v2gOps.sourceId
          ? 'Merge mode: click the target (survivor)…'
          : 'Merge mode: click the source (will be retired)…';
        hint.style.color = 'var(--warning,#f59e0b)';
      } else if (_v2gOps.mode === 'repoint') {
        hint.textContent = 'Move mode: click the new endpoint node… (Clear to cancel)';
        hint.style.color = 'var(--accent,#22d3ee)';
      } else if (_v2gOps.mode === 'group') {
        hint.textContent = 'Group mode: click the PARENT node… (Clear to cancel)';
        hint.style.color = 'var(--accent,#22d3ee)';
      } else if (_v2gOps.sourceId && _v2gOps.targetId) {
        hint.textContent = 'Ready — press Link or Merge, or click an edge to edit/unlink.';
        hint.style.color = 'var(--text-secondary)';
      } else {
        hint.textContent = 'Click node → inspect. Click edge → edit/unlink. Link: set src+tgt or use pick mode.';
        hint.style.color = 'var(--text-muted)';
      }
    }
    if (linkBtn) {
      linkBtn.classList.toggle('btn-primary', _v2gOps.mode === 'link' || !!(!_v2gOps.mode && _v2gOps.sourceId && _v2gOps.targetId));
      linkBtn.textContent = _v2gOps.mode === 'link' ? 'Linking…' : 'Link';
    }
    if (mergeBtn) {
      mergeBtn.classList.toggle('btn-primary', _v2gOps.mode === 'merge');
      mergeBtn.textContent = _v2gOps.mode === 'merge' ? 'Merging…' : 'Merge';
    }
  }

  function _v2gBroadcastSlots() {
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-ops-slots', {
        detail: {
          sourceId: _v2gOps.sourceId,
          targetId: _v2gOps.targetId,
          predicate: (document.getElementById('v2g-ops-predicate') || {}).value || 'related_to',
        },
      }));
    } catch (e) { /* ignore */ }
    _v2gSyncOpsBar();
    _v2gRepaint();
  }

  function _v2gSetSlot(which, id, opts) {
    opts = opts || {};
    if (!id) return;
    if (which === 'source') _v2gOps.sourceId = id;
    else if (which === 'target') _v2gOps.targetId = id;
    if (!opts.silent) _v2gBroadcastSlots();
  }

  function _v2gClearSlots(opts) {
    opts = opts || {};
    _v2gOps.sourceId = null;
    _v2gOps.targetId = null;
    _v2gOps.mode = null;
    _v2gOps.repoint = null;  // F1: cancel any pending move
    _v2gOps.group = null;    // F: cancel any pending group-under
    if (!opts.keepEdge) _v2gOps.selectedEdgeIdx = -1;
    _v2gBroadcastSlots();
  }

  function _v2gEnterMode(mode) {
    _v2gOps.mode = mode;
    // Soft-start: if no source yet, wait for first click; if source set, wait for target
    _v2gSyncOpsBar();
    _v2gToast(
      mode === 'link'
        ? 'Link mode: click source, then target'
        : 'Merge mode: click source (retire), then target (keep)',
      'info'
    );
  }

  function _v2gOpsPredicate() {
    var el = document.getElementById('v2g-ops-predicate');
    var p = el ? String(el.value || '').trim() : '';
    return p || 'related_to';
  }

  async function _v2gReloadGraph() {
    _v2gStructSig = '';
    _v2gLabelSig = '';
    await _v2gLoad();
  }

  async function _v2gDoLink(src, tgt, pred) {
    src = String(src || '').trim();
    tgt = String(tgt || '').trim();
    pred = String(pred || _v2gOpsPredicate()).trim() || 'related_to';
    if (!src || !tgt) {
      _v2gToast('Set source and target first', 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast('Source and target must differ', 'error');
      return false;
    }
    try {
      var data = await _v2gApiJson('/api/memory/v2/entities/link', {
        method: 'POST',
        body: JSON.stringify({ subject: src, predicate: pred, object: tgt }),
      });
      if (!data.ok) {
        _v2gToast(data.error || 'Link failed', 'error');
        console.warn('[v2g] link failed', data);
        return false;
      }
      _v2gToast(
        (data.already ? 'Already linked · ' : 'Linked ') +
          _v2gShortId(data.subject || src) + ' —' + pred + '→ ' + _v2gShortId(data.object || tgt),
        'success'
      );
      _v2gOps.mode = null;
      _v2gSyncOpsBar();
      await _v2gReloadGraph();
      _v2gSelectEntity(data.object || tgt, { notify: false });
      // F4: refresh predicate chips so a newly-created predicate appears.
      _v2gLoadPredChips();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'link', source: data.subject || src, target: data.object || tgt, beliefId: data.belief_id },
        }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Link failed: ' + (err && err.message ? err.message : err), 'error');
      console.warn('[v2g] link exception', err);
      return false;
    }
  }

  async function _v2gDoMerge(src, tgt) {
    src = String(src || '').trim();
    tgt = String(tgt || '').trim();
    if (!src || !tgt) {
      _v2gToast('Set source and target first', 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast('Source and target must differ', 'error');
      return false;
    }
    var ok = await _v2gConfirm({
      title: 'Merge entities',
      message: 'Merge ' + src + ' into ' + tgt + '?\nBeliefs rewire to the target; source is retired.',
    });
    if (!ok) return false;
    try {
      var resp = await fetch('/api/memory/v2/entities/merge', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ source_id: src, target_id: tgt }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Merge failed', 'error');
        return false;
      }
      _v2gToast('Merged ' + _v2gShortId(src) + ' → ' + _v2gShortId(tgt), 'success');
      _v2gOps.sourceId = null;
      _v2gOps.targetId = tgt;
      _v2gOps.mode = null;
      _v2gBroadcastSlots();
      await _v2gReloadGraph();
      _v2gSelectEntity(tgt, { notify: false });
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'merge', source: src, target: tgt } }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Merge failed', 'error');
      return false;
    }
  }

  /** Build SPO seed from a canvas edge for unlink API. */
  function _v2gEdgeSeed(ed) {
    if (!ed) return {};
    var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
    var pred = ed.fullLabel || ed.label || '';
    return {
      subject: (A && A.id) || ed.sourceId || '',
      predicate: pred,
      object: ed.objectText || (B && B.id) || ed.targetId || '',
      objectText: ed.objectText || '',
      sourceId: (A && A.id) || ed.sourceId || '',
      targetId: (B && B.id) || ed.targetId || '',
      label: pred,
      fullLabel: pred,
      beliefId: ed.beliefId || null,
      edgeIdx: typeof ed._idx === 'number' ? ed._idx : -1,
    };
  }

  /** All edges touching a node id (with neighbor + hub flags). */
  function _v2gEdgesForNode(nodeId) {
    var out = [];
    if (!nodeId) return out;
    for (var i = 0; i < _v2gEdges.length; i++) {
      var ed = _v2gEdges[i];
      var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      if (A.id !== nodeId && B.id !== nodeId) continue;
      var other = A.id === nodeId ? B : A;
      var outbound = A.id === nodeId;
      out.push({
        idx: i,
        ed: ed,
        other: other,
        outbound: outbound,
        toHub: _v2gIsHubNeighbor(other),
        seed: _v2gEdgeSeed(ed),
      });
    }
    return out;
  }

  /**
   * Unlink (soft-invalidate) a belief edge.
   * @param {string|null} beliefId
   * @param {{subject?:string,predicate?:string,object?:string,objectText?:string}} [seed]
   * @param {{skipConfirm?:boolean,skipReload?:boolean,silent?:boolean}} [opts]
   */
  async function _v2gUnlinkBelief(beliefId, seed, opts) {
    seed = seed || {};
    opts = opts || {};
    var subject = String(seed.subject || seed.sourceId || '').trim();
    var predicate = String(seed.predicate || seed.label || seed.fullLabel || '').trim();
    var object = String(seed.object || seed.objectText || seed.targetId || '').trim();
    if (!beliefId) beliefId = seed.beliefId || null;
    if (!beliefId && !(subject && predicate && object)) {
      if (!opts.silent) _v2gToast('Cannot cut — missing belief id and edge triple', 'error');
      return false;
    }
    if (!opts.skipConfirm) {
      var ok = await _v2gConfirm({
        title: 'Cut connection',
        message:
          'Remove this edge from active memory?\n' +
          (subject && predicate
            ? subject + ' —' + predicate + '→ ' + object
            : 'Soft-invalidate (recoverable via Hygiene).'),
        confirmText: 'Cut',
        danger: true,
      });
      if (!ok) return false;
    }
    try {
      // Prefer unified unlink (id + SPO fallback) so missing belief ids still work
      var data = await _v2gApiJson('/api/memory/v2/entities/unlink', {
        method: 'POST',
        body: JSON.stringify({
          belief_id: beliefId || null,
          subject: subject || null,
          predicate: predicate || null,
          object: object || null,
          object_text: seed.objectText || object || null,
        }),
      });
      if (!data.ok) {
        // Legacy fallback: direct invalidate by id
        if (beliefId) {
          data = await _v2gApiJson(
            '/api/memory/v2/beliefs/' + encodeURIComponent(beliefId) + '/invalidate',
            { method: 'POST', body: '{}' }
          );
        }
      }
      if (!data.ok) {
        if (!opts.silent) {
          _v2gToast(data.error || 'Cut failed', 'error');
          console.warn('[v2g] unlink failed', data, { beliefId: beliefId, seed: seed });
        }
        return false;
      }
      if (!opts.silent && !opts.skipReload) {
        _v2gToast(data.already ? 'Already cut' : 'Connection cut', 'success');
      }
      if (!opts.skipReload) {
        _v2gOps.selectedEdgeIdx = -1;
        var insp = document.getElementById('v2g-inspect');
        if (insp) {
          insp.innerHTML = '<span style="color:var(--text-muted);">Connection cut. Click a node or edge.</span>';
        }
        await _v2gReloadGraph();
        try {
          if (typeof loadV2Beliefs === 'function') {
            loadV2Beliefs((document.getElementById('v2-belief-search') || {}).value || '');
          }
        } catch (e) { /* optional */ }
        try {
          window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
            detail: { op: 'unlink', beliefId: data.belief_id || beliefId },
          }));
        } catch (e2) { /* ignore */ }
      }
      return true;
    } catch (err) {
      if (!opts.silent) {
        _v2gToast('Cut failed: ' + (err && err.message ? err.message : err), 'error');
        console.warn('[v2g] unlink exception', err);
      }
      return false;
    }
  }

  /**
   * Cut several edges after a single confirm (hub-shortcut cleanups).
   * @param {Array<{beliefId?:string,seed?:object,ed?:object}>} items
   */
  async function _v2gCutEdges(items, opts) {
    opts = opts || {};
    items = (items || []).filter(Boolean);
    if (!items.length) {
      _v2gToast('No edges to cut', 'info');
      return 0;
    }
    var msg =
      opts.message ||
      ('Cut ' + items.length + ' connection' + (items.length > 1 ? 's' : '') +
        ' from active memory?');
    var ok = await _v2gConfirm({
      title: opts.title || 'Cut connections',
      message: msg,
      confirmText: opts.confirmText || ('Cut ' + items.length),
      danger: true,
    });
    if (!ok) return 0;
    var n = 0;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var seed = it.seed || (it.ed ? _v2gEdgeSeed(it.ed) : it);
      var bid = it.beliefId || seed.beliefId || (it.ed && it.ed.beliefId) || null;
      var done = await _v2gUnlinkBelief(bid, seed, {
        skipConfirm: true,
        skipReload: true,
        silent: true,
      });
      if (done) n++;
    }
    if (n > 0) {
      _v2gToast('Cut ' + n + ' connection' + (n > 1 ? 's' : ''), 'success');
      _v2gOps.selectedEdgeIdx = -1;
      await _v2gReloadGraph();
      try {
        if (typeof loadV2Beliefs === 'function') {
          loadV2Beliefs((document.getElementById('v2-belief-search') || {}).value || '');
        }
      } catch (e) { /* optional */ }
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'unlink-batch', count: n },
        }));
      } catch (e2) { /* ignore */ }
    } else {
      _v2gToast('No edges were cut', 'error');
    }
    return n;
  }

  /** Cut every direct edge between this node and the memory hub (You/Mubder). */
  async function _v2gCutHubLinks(nodeId) {
    var edges = _v2gEdgesForNode(nodeId).filter(function(x) { return x.toHub; });
    if (!edges.length) {
      _v2gToast('No direct hub link on this node', 'info');
      return 0;
    }
    var otherLinks = _v2gEdgesForNode(nodeId).filter(function(x) { return !x.toHub; });
    var hint = otherLinks.length
      ? '\n\nKeeps links to: ' +
        otherLinks
          .map(function(x) { return _v2gDisplayName(x.other); })
          .slice(0, 6)
          .join(', ') +
        (otherLinks.length > 6 ? '…' : '') +
        '\nUseful when the chain should be leaf → parent → hub (not leaf → hub).'
      : '';
    return _v2gCutEdges(
      edges.map(function(x) {
        return { beliefId: x.ed.beliefId, seed: x.seed, ed: x.ed };
      }),
      {
        title: 'Cut hub shortcut',
        message:
          'Remove ' +
          edges.length +
          ' direct link' +
          (edges.length > 1 ? 's' : '') +
          ' to the hub (You/Mubder)?' +
          hint,
        confirmText: 'Cut hub link' + (edges.length > 1 ? 's' : ''),
      }
    );
  }

  async function _v2gEditBeliefById(beliefId, seed) {
    seed = seed || {};
    if (!beliefId) {
      _v2gToast('No belief id — cannot edit', 'error');
      return false;
    }
    // Prefer live detail so we edit the current triple
    var b = {
      id: beliefId,
      subject: seed.subject || '',
      predicate: seed.predicate || seed.label || '',
      object: seed.object || seed.objectText || '',
    };
    try {
      var r0 = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId), {
        credentials: 'same-origin',
      });
      var d0 = await r0.json().catch(function() { return {}; });
      if (d0.ok && d0.belief) {
        b.subject = d0.belief.subject || b.subject;
        b.predicate = d0.belief.predicate || b.predicate;
        b.object = d0.belief.object || b.object;
      }
    } catch (e) { /* use seed */ }

    var object = await _v2gPrompt({
      title: 'Edit belief — object',
      message: 'Fact / object text. Cancel aborts.',
      defaultValue: b.object || '',
      confirmText: 'Next',
      placeholder: 'e.g. Paris',
    });
    if (object == null) return false;
    var predicate = await _v2gPrompt({
      title: 'Edit belief — predicate',
      message: 'Relation name (snake_case ok).',
      defaultValue: b.predicate || '',
      confirmText: 'Next',
      placeholder: 'lives_in',
    });
    if (predicate == null) return false;
    var subject = await _v2gPrompt({
      title: 'Edit belief — subject',
      message: 'Subject entity id.',
      defaultValue: b.subject || '',
      confirmText: 'Save',
      placeholder: 'user',
    });
    if (subject == null) return false;
    subject = String(subject).trim();
    predicate = String(predicate).trim();
    object = String(object).trim();
    if (!subject || !predicate || !object) {
      _v2gToast('Subject, predicate, and object are required', 'error');
      return false;
    }
    if (subject === b.subject && predicate === b.predicate && object === b.object) return false;
    try {
      var resp = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId), {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ subject: subject, predicate: predicate, object: object }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Edit failed', 'error');
        return false;
      }
      _v2gToast('Belief updated', 'success');
      _v2gOps.selectedEdgeIdx = -1;
      await _v2gReloadGraph();
      try {
        if (typeof loadV2Beliefs === 'function') loadV2Beliefs((document.getElementById('v2-belief-search') || {}).value || '');
      } catch (e2) { /* optional */ }
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'edit', beliefId: beliefId, subject: subject, object: object },
        }));
      } catch (e3) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Edit failed', 'error');
      return false;
    }
  }

  // F1: repoint (move) a belief's subject or object to a different node.
  // Flow: click "Move" on an edge → choose which side (subject/object) →
  // enter pick mode → click the new endpoint node → POST /repoint. Replaces
  // the destructive cut+relink two-step that left nodes adrift.
  function _v2gRepointBelief(beliefId, seed) {
    seed = seed || {};
    if (!beliefId) { _v2gToast('No belief id — cannot move', 'error'); return false; }
    var subj = seed.subject || '';
    var obj = seed.object || '';
    var fromId = _v2gSelectedId;
    // Ask which endpoint to move. Default to the non-selected endpoint so the
    // most common case (move the OTHER end away from the inspected node) is
    // one click.
    _v2gConfirm({
      title: 'Move which end of the edge?',
      message: 'Subject = ' + _v2gShortId(subj) + ' · Object = ' + _v2gShortId(obj)
        + '. Pick the end to move to another node.',
      confirmText: 'Move subject',
      cancelText: 'Move object',
    }).then(function (moveSubject) {
      _v2gOps.mode = 'repoint';
      _v2gOps.repoint = { beliefId: beliefId, side: moveSubject ? 'subject' : 'object' };
      _v2gSyncOpsBar();
      _v2gToast('Click the new endpoint node…', 'info');
    });
    return true;
  }

  async function _v2gDoRepoint(beliefId, side, newEndpoint) {
    try {
      var payload = {};
      payload[side] = newEndpoint;
      var data = await _v2gApiJson('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId) + '/repoint', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (!data.ok) {
        _v2gToast(data.error || 'Move failed', 'error');
        return false;
      }
      var warn = data.warn_orphaned && data.warn_orphaned.length
        ? ' · stranded: ' + data.warn_orphaned.join(', ')
        : '';
      _v2gToast('Moved edge to ' + _v2gShortId(newEndpoint) + (data.undo_token ? ' · Undo' : '') + warn, 'success');
      _v2gOps.mode = null;
      _v2gOps.repoint = null;
      _v2gSyncOpsBar();
      await _v2gReloadGraph();
      _v2gSelectEntity(newEndpoint, { notify: false });
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'repoint', beliefId: beliefId, side: side, endpoint: newEndpoint },
        }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Move failed', 'error');
      return false;
    }
  }

  // F: "Group under" — a VIEW-ONLY association (no belief change). The operator
  // picks a member node, then clicks the parent; the canvas clusters + tiers
  // them. Distinct from Link (which mutates memory). See MEMORY_GRAPH_GROUPING_PLAN.md.

  // Toggle the is_major flag on an entity — makes it render bigger + violet.
  async function _v2gToggleMajor(p) {
    if (!p || !p.id || _v2gIsUser(p)) return;
    var newMajor = !p.isMajor;
    try {
      var data = await _v2gApiJson('/api/memory/v2/entities/' + encodeURIComponent(p.id) + '/major', {
        method: 'POST',
        body: JSON.stringify({ major: newMajor }),
      });
      if (!data.ok) { _v2gToast(data.error || 'Toggle failed', 'error'); return; }
      p.isMajor = newMajor;
      _v2gToast((newMajor ? 'Marked ' : 'Unmarked ') + _v2gShortId(p.id) + (newMajor ? ' as major' : ''), 'success');
      _v2gRepaint();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'major', id: p.id, major: newMajor } }));
      } catch (e) { /* ignore */ }
    } catch (e) {
      _v2gToast('Toggle failed', 'error');
    }
  }

  function _v2gGroupUnder(memberId) {
    memberId = String(memberId || '').trim();
    if (!memberId) { _v2gToast('Pick a node first', 'error'); return false; }
    if (_v2gIsUser({ id: memberId })) {
      _v2gToast('The hub is the top-level root — not groupable', 'info');
      return false;
    }
    _v2gOps.mode = 'group';
    _v2gOps.group = { member: memberId };
    _v2gSyncOpsBar();
    _v2gToast('Click the PARENT node to group "' + _v2gShortId(memberId) + '" under…', 'info');
    return true;
  }

  async function _v2gDoGroup(memberId, rootId) {
    try {
      var data = await _v2gApiJson('/api/memory/v2/graph/groups', {
        method: 'POST',
        body: JSON.stringify({ group_root: rootId, member: memberId }),
      });
      if (!data.ok) {
        _v2gToast(data.error || 'Group failed', 'error');
        return false;
      }
      _v2gToast(
        'Grouped ' + _v2gShortId(memberId) + ' under ' + _v2gShortId(rootId)
        + ' (tier ' + data.member_tier + ') · view-only, memory untouched',
        'success'
      );
      _v2gOps.mode = null;
      _v2gOps.group = null;
      _v2gSyncOpsBar();
      await _v2gReloadGraph();
      _v2gHeated();  // P2d: reheat so the new group-spring snaps the member into orbit
      _v2gSelectEntity(memberId, { notify: false });
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', {
          detail: { op: 'group', member: memberId, root: rootId, tier: data.member_tier },
        }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Group failed', 'error');
      return false;
    }
  }

  async function _v2gDeleteEntity(id) {
    if (!id || id === 'user') {
      _v2gToast('Cannot delete protected hub', 'error');
      return false;
    }
    var ok = await _v2gConfirm({
      title: 'Delete entity',
      message: 'Delete entity shell “' + id + '”? (Protected / non-empty may fail.)',
    });
    if (!ok) return false;
    try {
      var resp = await fetch('/api/memory/v2/entities/' + encodeURIComponent(id), {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        _v2gToast(data.error || 'Delete failed', 'error');
        return false;
      }
      _v2gToast('Deleted ' + _v2gShortId(id), 'success');
      if (_v2gOps.sourceId === id) _v2gOps.sourceId = null;
      if (_v2gOps.targetId === id) _v2gOps.targetId = null;
      _v2gBroadcastSlots();
      await _v2gReloadGraph();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'delete', id: id } }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast('Delete failed', 'error');
      return false;
    }
  }

  function _v2gHandleNodePick(p) {
    if (!p || p.isEpisode) return false;
    if (!_v2gOps.mode) return false;
    // F1: repoint mode consumes a single node click as the new endpoint.
    if (_v2gOps.mode === 'repoint') {
      var rp = _v2gOps.repoint || {};
      if (!rp.beliefId || !rp.side) { _v2gOps.mode = null; _v2gSyncOpsBar(); return true; }
      if (p.id === _v2gSelectedId) { _v2gToast('Pick a different node', 'info'); return true; }
      var beliefId = rp.beliefId, side = rp.side, endpoint = p.id;
      _v2gOps.mode = null;
      _v2gOps.repoint = null;
      _v2gSyncOpsBar();
      _v2gDoRepoint(beliefId, side, endpoint);
      return true;
    }
    // F: group mode — single click on the parent completes the grouping.
    if (_v2gOps.mode === 'group') {
      var g = _v2gOps.group || {};
      var member = g.member;
      _v2gOps.mode = null;
      _v2gOps.group = null;
      _v2gSyncOpsBar();
      if (!member || p.id === member) { _v2gToast('Pick a different parent node', 'info'); return true; }
      _v2gDoGroup(member, p.id);
      return true;
    }
    if (!_v2gOps.sourceId) {
      _v2gSetSlot('source', p.id);
      _v2gSyncOpsBar();
      return true;
    }
    if (p.id === _v2gOps.sourceId) {
      _v2gToast('Pick a different node as target', 'info');
      return true;
    }
    _v2gSetSlot('target', p.id);
    var mode = _v2gOps.mode;
    _v2gOps.mode = null;
    _v2gSyncOpsBar();
    if (mode === 'link') {
      _v2gDoLink(_v2gOps.sourceId, _v2gOps.targetId, _v2gOpsPredicate());
    } else if (mode === 'merge') {
      _v2gDoMerge(_v2gOps.sourceId, _v2gOps.targetId);
    }
    return true;
  }

  function _v2gInspectEdge(edgeIdx) {
    var el = document.getElementById('v2g-inspect');
    if (!el || edgeIdx < 0 || !_v2gEdges[edgeIdx]) return;
    var ed = _v2gEdges[edgeIdx];
    var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
    if (!A || !B) return;
    _v2gOps.selectedEdgeIdx = edgeIdx;
    _v2gSelectedId = null;
    _v2gHighlightSubj = A.id;
    _v2gHighlightObj = B.id;
    var pcolor = _V2G_PRED_COLORS[ed.type] || _v2gTheme().accent;
    var pred = ed.fullLabel || ed.label || '';
    var html = '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:6px;">';
    html += '<div style="font-weight:700;font-size:0.8rem;color:#fbbf24;word-break:break-word;flex:1;">Edge · belief</div>';
    html += '</div>';
    html += '<div style="font-size:0.74rem;line-height:1.45;margin-bottom:8px;color:var(--text-primary);">';
    html += '<b>' + _v2gEsc(_v2gDisplayName(A)) + '</b> ';
    html += '<span style="color:' + pcolor + ';">' + _v2gEsc(String(pred).replace(/_/g, ' ')) + '</span> ';
    html += '<b>' + _v2gEsc(_v2gDisplayName(B)) + '</b>';
    html += '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.65rem;margin-bottom:8px;font-family:var(--font-mono);">';
    if (ed.beliefId) html += 'id: ' + _v2gEsc(String(ed.beliefId).slice(0, 20));
    else html += 'id: (missing — unlink may fail)';
    html += ' · ' + _v2gEsc(ed.type || '?') + ' · conf ' + Math.round((ed.confidence || 0) * 100) + '%';
    if (ed.superseded) html += ' · superseded';
    html += '</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">';
    html += '<button type="button" class="btn btn-sm btn-danger v2g-edge-act" data-act="unlink" style="font-size:0.65rem;padding:2px 8px;" title="Cut this edge">Cut</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="edit" style="font-size:0.65rem;padding:2px 8px;">Edit</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="src" style="font-size:0.65rem;padding:2px 8px;">Src←A</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="tgt" style="font-size:0.65rem;padding:2px 8px;">Tgt→B</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="list" style="font-size:0.65rem;padding:2px 8px;">In list</button>';
    html += '</div>';
    // Stash edge triple on the panel for delegated clicks (survives re-renders better)
    el.setAttribute('data-edge-belief-id', ed.beliefId || '');
    el.setAttribute('data-edge-subject', A.id || '');
    el.setAttribute('data-edge-target', B.id || '');
    el.setAttribute('data-edge-predicate', pred || '');
    el.setAttribute('data-edge-object', ed.objectText || B.id || '');
    el.innerHTML = html;
    el.querySelectorAll('.v2g-edge-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute('data-act');
        var seed = {
          subject: A.id,
          predicate: pred,
          object: ed.objectText || B.id,
          objectText: ed.objectText,
          sourceId: A.id,
          targetId: B.id,
          label: pred,
          fullLabel: pred,
        };
        if (act === 'edit') {
          _v2gEditBeliefById(ed.beliefId, seed);
        } else if (act === 'unlink') {
          _v2gUnlinkBelief(ed.beliefId, seed);
        } else if (act === 'src') {
          _v2gSetSlot('source', A.id);
        } else if (act === 'tgt') {
          _v2gSetSlot('target', B.id);
        } else if (act === 'list') {
          _v2gNotifyList({
            type: 'belief',
            id: ed.beliefId,
            subject: A.id,
            object: ed.objectText || B.id,
            scrollOps: true,
          });
        }
      });
    });
    _v2gRepaint();
  }

  function _v2gBindPointer(canvas, wrap) {
    if (canvas._v2gBound) return; canvas._v2gBound = true;
    function evToCanvas(ev) {
      var rect = canvas.getBoundingClientRect();
      return { sx: ev.clientX - rect.left, sy: ev.clientY - rect.top };
    }
    canvas.addEventListener('pointerdown', function(ev) {
      var c = evToCanvas(ev); var idx = _v2gHit(c.sx, c.sy);
      if (idx >= 0) {
        var p = _v2gPts[idx];
        // Link/merge pick mode: first/second node click assigns slots
        if (_v2gOps.mode && !p.isEpisode) {
          _v2gSelectedId = p.id;
          _v2gOps.selectedEdgeIdx = -1;
          _v2gHighlightSubj = null; _v2gHighlightObj = null;
          _v2gInspect(p);
          _v2gHandleNodePick(p);
          canvas.setPointerCapture(ev.pointerId);
          canvas.style.cursor = 'pointer';
          _v2gHeated(); _v2gRepaint();
          return;
        }
        // Shift+click: soft-pick into source/target without a formal mode
        if (ev.shiftKey && !p.isEpisode) {
          if (!_v2gOps.sourceId || (_v2gOps.sourceId && _v2gOps.targetId)) {
            _v2gOps.targetId = null;
            _v2gSetSlot('source', p.id);
          } else {
            _v2gSetSlot('target', p.id);
          }
        }
        _v2gDrag = {
          idx: idx,
          wx: _v2gWX(c.sx) - p.x,
          wy: _v2gWY(c.sy) - p.y,
          sx0: c.sx,
          sy0: c.sy,
          moved: false,
        };
        _v2gSelectedId = p.id; _v2gInspect(p); canvas.setPointerCapture(ev.pointerId);
        canvas.style.cursor = 'grabbing';
        // Clear belief-click highlight when selecting a node directly
        _v2gHighlightSubj = null; _v2gHighlightObj = null;
        _v2gOps.selectedEdgeIdx = -1;
        // Do NOT jump the page to the entities list on single click —
        // that blocks free explore/drag. List sync is double-click only.
      } else {
        // Prefer edge hit when not on a node (edit/unlink without leaving graph)
        var eidx = _v2gHitEdge(c.sx, c.sy);
        if (eidx >= 0) {
          _v2gInspectEdge(eidx);
          canvas.setPointerCapture(ev.pointerId);
          canvas.style.cursor = 'pointer';
          _v2gHeated(); _v2gRepaint();
          return;
        }
        _v2gDrag = { pan: true, sx: c.sx, sy: c.sy, ox: _v2gView.ox, oy: _v2gView.oy };
        canvas.style.cursor = 'grabbing';
        // Clear selection + belief highlight on empty-space click
        _v2gSelectedId = null; _v2gHighlightSubj = null; _v2gHighlightObj = null;
        _v2gOps.selectedEdgeIdx = -1;
      }
      // Heat lightly so free nodes can settle; pinned nodes stay fixed.
      _v2gHeated(); _v2gRepaint();
    });
    canvas.addEventListener('pointermove', function(ev) {
      var c = evToCanvas(ev);
      if (_v2gDrag) {
        if (_v2gDrag.pan) {
          _v2gView.ox = _v2gDrag.ox + (c.sx - _v2gDrag.sx);
          _v2gView.oy = _v2gDrag.oy + (c.sy - _v2gDrag.sy);
        } else {
          var p = _v2gPts[_v2gDrag.idx];
          if (p) {
            if (Math.abs(c.sx - _v2gDrag.sx0) + Math.abs(c.sy - _v2gDrag.sy0) > 3) {
              _v2gDrag.moved = true;
            }
            p.x = _v2gWX(c.sx) - _v2gDrag.wx; p.y = _v2gWY(c.sy) - _v2gDrag.wy;
            p.vx = 0; p.vy = 0;
          }
        }
        _v2gRepaint();
      } else {
        var idx = _v2gHit(c.sx, c.sy);
        var eHover = idx < 0 ? _v2gHitEdge(c.sx, c.sy) : -1;
        if (idx !== _v2gHover) { _v2gHover = idx; _v2gRepaint(); }
        canvas.style.cursor = (idx >= 0 || eHover >= 0) ? 'pointer' : 'grab';
        var tip = document.getElementById('v2g-tooltip');
        if (idx >= 0 && tip) {
          var p = _v2gPts[idx];
          var tc = _v2gNodeColor(p);
          var tLabel = _v2gTitle(_v2gDisplayName(p));
          var modeHint = '';
          if (_v2gOps.mode === 'link') modeHint = '<br><span style="color:var(--accent);">link pick</span>';
          else if (_v2gOps.mode === 'merge') modeHint = '<br><span style="color:var(--warning);">merge pick</span>';
          tip.innerHTML = '<b style="color:' + tc + ';word-break:break-word;">' + _v2gEsc(tLabel) + '</b><br><span style="color:var(--text-muted);">' +
            (_v2gIsUser(p) ? 'you · center of memory' : ('type: ' + p.type)) +
            (p.isHighStakes ? ' · high-stakes' : '') +
            (p.isVirtual ? ' · fact' : '') +
            (p.id && _v2gDisplayName(p) !== p.id ? ' · id: ' + _v2gEsc(String(p.id).slice(0, 24)) : '') +
            '</span>' + modeHint;
          tip.style.display = 'block';
          tip.style.borderColor = _v2gHexAlpha(tc, 0.35);
          var rect = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (eHover >= 0 && tip) {
          var edh = _v2gEdges[eHover];
          var Ah = _v2gPts[edh.a], Bh = _v2gPts[edh.b];
          tip.innerHTML = '<b style="color:#fbbf24;">' + _v2gEsc((edh.fullLabel || edh.label || 'edge').replace(/_/g, ' ')) + '</b><br>' +
            '<span style="color:var(--text-muted);">' +
            _v2gEsc(Ah ? _v2gDisplayName(Ah) : '?') + ' → ' + _v2gEsc(Bh ? _v2gDisplayName(Bh) : '?') +
            '</span><br><span style="color:var(--text-muted);font-size:0.68rem;">click to edit / unlink</span>';
          tip.style.display = 'block';
          tip.style.borderColor = 'rgba(251,191,36,0.4)';
          var rect2 = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect2.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (tip) { tip.style.display = 'none'; }
      }
    });
    canvas.addEventListener('pointerup', function(ev) {
      // After a real drag, pin the node so physics / refresh cannot yank it back.
      if (_v2gDrag && !_v2gDrag.pan && _v2gDrag.moved && _v2gPts[_v2gDrag.idx]) {
        var placed = _v2gPts[_v2gDrag.idx];
        placed.pinned = true;
        placed.vx = 0; placed.vy = 0;
        _v2gRememberPos(placed);
      }
      _v2gDrag = null; canvas.style.cursor = 'grab'; _v2gRepaint();
    });
    canvas.addEventListener('pointercancel', function() { _v2gDrag = null; });
    canvas.addEventListener('wheel', function(ev) {
      ev.preventDefault(); var c = evToCanvas(ev);
      var factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      var ns = Math.max(_v2gMinScale, Math.min(_v2gMaxScale, _v2gView.scale * factor));
      if (ns === _v2gView.scale) return;
      var wx = _v2gWX(c.sx), wy = _v2gWY(c.sy);
      _v2gView.scale = ns; _v2gView.ox = c.sx - wx * ns; _v2gView.oy = c.sy - wy * ns;
      _v2gRepaint();
    }, { passive: false });
    canvas.addEventListener('dblclick', function(ev) {
      var c = evToCanvas(ev); var idx = _v2gHit(c.sx, c.sy);
      if (idx < 0) return;
      var p = _v2gPts[idx];
      _v2gSelectedId = p.id;
      _v2gInspect(p);
      // Double-click: jump to the matching row in the entities/beliefs list.
      // Single-click deliberately does not (keeps free explore + drag).
      if (!p.isEpisode) {
        _v2gNotifyList({
          type: 'entity',
          id: p.id,
          name: _v2gDisplayName(p),
          scrollOps: true,
        });
        try {
          var ops = document.getElementById('mem-tab-entities');
          if (ops) ops.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (e) { /* ignore */ }
      }
      _v2gRepaint();
    });
  }

  function _v2gEsc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  // Short heading for a node: the first " - " segment, or the first ~56
  // chars. Long belief texts ("Repository: X - Description: ...") get a
  // readable title instead of dumping the whole blob into the header.
  function _v2gTitle(s) {
    var t = String(s || '').trim();
    if (!t) return '';
    if (t.length <= 56) return t;
    var seg = t.split(' - ')[0].trim();
    if (seg && seg.length <= 56) return seg;
    var cut = t.slice(0, 56).replace(/\s+\S*$/, '');
    return (cut || t.slice(0, 56)) + '…';
  }

  // Full contents of a belief text, split into readable lines on " - ".
  function _v2gContents(s) {
    var t = String(s || '').trim();
    if (!t) return '';
    var segs = t.split(' - ');
    if (segs.length > 1) {
      return segs.map(function(seg) {
        var html = _v2gEsc(seg.trim());
        return '<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.04);line-height:1.4;">' + html + '</div>';
      }).join('');
    }
    return '<div style="line-height:1.45;">' + _v2gEsc(t) + '</div>';
  }

  // Truncate a long neighbor label in belief rows so the selected node's
  // full text is not repeated next to itself (the full text lives in the
  // Contents block above).
  function _v2gShortLabel(s, n) {
    var t = String(s || '').trim();
    if (!t) return '';
    var lim = n || 64;
    return t.length > lim ? t.slice(0, lim) + '…' : t;
  }

  function _v2gInspect(p) {
    var el = document.getElementById('v2g-inspect');
    if (!el || !p) return;
    try {
    _v2gRefreshPalette();
    _v2gOps.selectedEdgeIdx = -1;
    var color = _v2gNodeColor(p);
    var fullName = _v2gDisplayName(p);
    var title = _v2gTitle(fullName);
    var html = '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:4px;">';
    html += '<div style="color:' + color + ';font-weight:700;font-size:0.82rem;word-break:break-word;flex:1;min-width:0;">' + _v2gEsc(title) + '</div>';
    html += '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.68rem;margin-bottom:6px;">';
    html += _v2gIsUser(p) ? 'you · memory hub' : ('type: ' + p.type);
    if (p.id) html += ' · id: <code style="font-size:0.65rem;">' + _v2gEsc(String(p.id)) + '</code>';
    if (p.isHighStakes) html += ' · <span style="color:#ef4444;display:inline-flex;align-items:center;gap:3px;">' + KazmaIcons.span('alert') + ' high-stakes</span>';
    if (p.isVirtual) html += ' · fact node';
    html += '</div>';
    // Collect connections once for cut-hub + list UI
    var nodeEdges = _v2gEdgesForNode(p.id);
    var hubEdges = nodeEdges.filter(function(x) { return x.toHub; });
    var nonHubEdges = nodeEdges.filter(function(x) { return !x.toHub; });
    // Hub shortcut: leaf linked to hub AND to another node (should be leaf→parent→hub)
    var hubShortcut = !_v2gIsUser(p) && hubEdges.length > 0 && nonHubEdges.length > 0;

    // Node ops — always show Cut when there is anything to cut
    if (!p.isEpisode) {
      html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;">';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="src" style="font-size:0.65rem;padding:2px 8px;" title="Set as link/merge source">Src</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="tgt" style="font-size:0.65rem;padding:2px 8px;" title="Set as link/merge target">Tgt</button>';
      html += '<button type="button" class="btn btn-sm btn-primary v2g-node-act" data-act="link-from" style="font-size:0.65rem;padding:2px 8px;" title="Start link from this node — click target next">Link→</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="merge-from" style="font-size:0.65rem;padding:2px 8px;" title="Start merge from this node (will be retired)">Merge→</button>';
      // F: "Group under" — view-only association (no memory change). The hub
      // can't be grouped (it's the root), so hide it for the hub.
      if (!_v2gIsUser(p)) {
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="group-under" style="font-size:0.65rem;padding:2px 8px;" title="Group this node under a parent (view-only — does not change memory)">Group under→</button>';
      }
      // Always offer Cut hub when any hub edge exists (even without "shortcut" pattern)
      if (hubEdges.length && !_v2gIsUser(p)) {
        html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="cut-hub" style="font-size:0.65rem;padding:2px 8px;" title="Remove direct link(s) to You/Mubder — keep parent chain">Cut hub</button>';
      }
      if (nodeEdges.length >= 1 && !_v2gIsUser(p)) {
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="cut-all" style="font-size:0.65rem;padding:2px 8px;" title="Cut every edge on this node">Cut all (' + nodeEdges.length + ')</button>';
      }
      // Major toggle — mark important nodes (projects, hubs) so they render
      // bigger + violet. Hidden for the hub (always major) and virtual facts.
      if (!_v2gIsUser(p) && !p.isVirtual) {
        var isMaj = !!p.isMajor;
        html += '<button type="button" class="btn btn-sm v2g-node-act" data-act="major" style="font-size:0.65rem;padding:2px 8px;' + (isMaj ? 'border-color:#a855f7;color:#c084fc;' : '') + '" title="Mark as major (bigger + violet)">' + (isMaj ? KazmaIcons.span('star') + ' Major' : 'Major') + '</button>';
      }
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="rename" style="font-size:0.65rem;padding:2px 8px;" title="Change display name (id stays the same)">Rename</button>';
      html += '<button type="button" class="btn btn-sm btn-secondary v2g-node-act" data-act="list" style="font-size:0.65rem;padding:2px 8px;" title="Highlight in entities list">In list</button>';
      if (!_v2gIsUser(p) && !p.isVirtual) {
        html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="delete" style="font-size:0.65rem;padding:2px 8px;" title="Delete empty entity shell">Del</button>';
      }
      html += '</div>';
    }

    // Banner: true shortcut (hub + other), OR softer note when only hub-linked
    if (hubShortcut) {
      var parentNames = nonHubEdges
        .map(function(x) { return _v2gDisplayName(x.other); })
        .filter(Boolean);
      var uniqParents = [];
      parentNames.forEach(function(n) {
        if (uniqParents.indexOf(n) < 0) uniqParents.push(n);
      });
      html += '<div style="margin-bottom:8px;padding:8px 10px;border-radius:8px;border:1px solid rgba(245,158,11,0.45);background:rgba(245,158,11,0.12);font-size:0.7rem;line-height:1.4;color:#fcd34d;">';
      html += '<strong style="color:#fbbf24;">Hub shortcut</strong> — direct link to hub while also linked to ';
      html += '<b>' + _v2gEsc(uniqParents.slice(0, 4).map(function(n) { return _v2gShortLabel(n, 30); }).join(', ')) + (uniqParents.length > 4 ? '…' : '') + '</b>.';
      html += '<div style="margin-top:4px;color:var(--text-muted);font-size:0.65rem;">Preferred: this → parent → hub (You/Mubder). Use <b>Cut hub</b> to drop the shortcut edge.</div>';
      html += '<button type="button" class="btn btn-sm btn-danger v2g-node-act" data-act="cut-hub" style="margin-top:6px;font-size:0.68rem;padding:3px 10px;">Cut hub link' + (hubEdges.length > 1 ? 's' : '') + '</button>';
      html += '</div>';
    } else if (hubEdges.length && !_v2gIsUser(p) && nonHubEdges.length === 0) {
      html += '<div style="margin-bottom:8px;padding:6px 10px;border-radius:8px;border:1px solid var(--border-subtle);background:rgba(255,255,255,0.03);font-size:0.68rem;color:var(--text-muted);">Linked only to hub. Use <b style="color:#f87171;">Cut</b> on the connection row to detach.</div>';
    }

    // Contents — the full text of this belief/entity, shown exactly once.
    if (fullName !== title) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Contents</div>';
      html += '<div style="background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);border-radius:6px;padding:6px 8px;font-size:0.72rem;color:var(--text-secondary);word-break:break-word;max-height:200px;overflow-y:auto;margin-bottom:8px;">' + _v2gContents(fullName) + '</div>';
    }
    // Connections — one Cut per neighbor (easy topology cleanup)
    if (nodeEdges.length) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Connections (' + nodeEdges.length + ') · Cut to detach</div>';
      html += '<div style="display:flex;flex-direction:column;gap:3px;max-height:260px;overflow-y:auto;">';
      for (var r = 0; r < nodeEdges.length; r++) {
        var row = nodeEdges[r];
        var ed = row.ed;
        var pcolor = _V2G_PRED_COLORS[ed.type] || _v2gTheme().accent;
        var predLabel = _v2gEsc((ed.fullLabel || ed.label || '').replace(/_/g, ' '));
        var neighLabel = _v2gEsc(_v2gShortLabel(_v2gDisplayName(row.other), 30));
        var dir = row.outbound ? '→' : '←';
        var hubBadge = row.toHub
          ? ' <span style="font-size:0.58rem;padding:1px 5px;border-radius:3px;background:rgba(245,158,11,0.2);color:#fbbf24;">hub</span>'
          : '';
        var rowBg = row.toHub && hubShortcut ? 'rgba(245,158,11,0.1)' : 'transparent';
        var rowBorder = row.toHub && hubShortcut ? 'rgba(245,158,11,0.35)' : 'transparent';
        html += '<div class="v2g-belief-row" data-edge-idx="' + row.idx + '" style="color:var(--text-secondary);line-height:1.35;font-size:0.72rem;word-break:break-word;padding:5px 6px;border-radius:6px;cursor:pointer;border:1px solid ' + rowBorder + ';background:' + rowBg + ';" onmouseover="this.style.background=\'rgba(251,191,36,0.1)\'" onmouseout="this.style.background=\'' + rowBg + '\'">';
        html += '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px;">';
        html += '<div style="min-width:0;flex:1;">';
        html += '<span style="color:var(--text-muted);">' + dir + '</span> <b>' + neighLabel + '</b>' + hubBadge;
        html += '<div style="font-size:0.62rem;color:var(--text-muted);margin-top:1px;"><span style="color:' + pcolor + ';">' + predLabel + '</span>' +
          (ed.superseded ? ' · superseded' : '') + '</div>';
        html += '</div>';
        html += '<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;" onclick="event.stopPropagation()">';
        html += '<button type="button" class="btn btn-sm btn-danger v2g-rel-act" data-act="cut" data-edge-idx="' + row.idx + '" style="font-size:0.62rem;padding:2px 8px;" title="Cut this edge only">Cut</button>';
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-rel-act" data-act="edit" data-edge-idx="' + row.idx + '" style="font-size:0.6rem;padding:1px 6px;">Edit</button>';
        html += '<button type="button" class="btn btn-sm btn-secondary v2g-rel-act" data-act="move" data-edge-idx="' + row.idx + '" style="font-size:0.6rem;padding:1px 6px;" title="Move this edge to another node (repoint)">Move</button>';
        html += '</div></div></div>';
      }
      html += '</div>';
    } else {
      html += '<div style="color:var(--text-muted);font-size:0.7rem;">No direct beliefs — set as Src and Link→ another node to connect it.</div>';
    }
    el.innerHTML = html;

    el.querySelectorAll('.v2g-node-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute('data-act');
        if (act === 'src') {
          _v2gSetSlot('source', p.id);
          _v2gToast('Source = ' + _v2gShortId(p.id), 'info');
        } else if (act === 'tgt') {
          _v2gSetSlot('target', p.id);
          _v2gToast('Target = ' + _v2gShortId(p.id), 'info');
        } else if (act === 'link-from') {
          _v2gOps.sourceId = p.id;
          _v2gOps.targetId = null;
          _v2gOps.mode = 'link';
          _v2gBroadcastSlots();
          _v2gToast('Link from ' + _v2gShortId(p.id) + ' — click target on graph', 'info');
        } else if (act === 'merge-from') {
          _v2gOps.sourceId = p.id;
          _v2gOps.targetId = null;
          _v2gOps.mode = 'merge';
          _v2gBroadcastSlots();
          _v2gToast('Merge from ' + _v2gShortId(p.id) + ' — click survivor target', 'info');
        } else if (act === 'group-under') {
          // F: view-only grouping — pick the parent next.
          _v2gGroupUnder(p.id);
        } else if (act === 'cut-hub') {
          _v2gCutHubLinks(p.id).then(function() {
            // Re-inspect node after graph reload if still present
            var idx = _v2gFindNodeIndex(p.id);
            if (idx >= 0) _v2gInspect(_v2gPts[idx]);
          });
        } else if (act === 'cut-all') {
          var all = _v2gEdgesForNode(p.id);
          _v2gCutEdges(
            all.map(function(x) {
              return { beliefId: x.ed.beliefId, seed: x.seed, ed: x.ed };
            }),
            {
              title: 'Cut all connections',
              message:
                'Detach “' +
                _v2gDisplayName(p) +
                '” from all ' +
                all.length +
                ' neighbor(s)? Node shell stays.',
              confirmText: 'Cut all',
            }
          ).then(function() {
            var idx2 = _v2gFindNodeIndex(p.id);
            if (idx2 >= 0) _v2gInspect(_v2gPts[idx2]);
          });
        } else if (act === 'rename') {
          _v2gRenameNode(p);
        } else if (act === 'major') {
          _v2gToggleMajor(p);
        } else if (act === 'list') {
          _v2gNotifyList({ type: 'entity', id: p.id, name: _v2gDisplayName(p), scrollOps: true });
          var ops = document.getElementById('mem-tab-entities') || document.querySelector('[id^="mem-tab-"]');
          if (ops) {
            try { ops.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
          }
        } else if (act === 'delete') {
          _v2gDeleteEntity(p.id);
        }
      });
    });

    el.querySelectorAll('.v2g-rel-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var eidx = parseInt(btn.getAttribute('data-edge-idx'), 10);
        var ed = _v2gEdges[eidx];
        if (!ed) return;
        var act = btn.getAttribute('data-act');
        var seed = _v2gEdgeSeed(ed);
        if (act === 'edit') {
          _v2gEditBeliefById(ed.beliefId, seed);
        } else if (act === 'move') {
          _v2gRepointBelief(ed.beliefId, seed);
        } else if (act === 'cut' || act === 'unlink') {
          _v2gUnlinkBelief(ed.beliefId, seed).then(function(ok) {
            if (ok) {
              var idx3 = _v2gFindNodeIndex(p.id);
              if (idx3 >= 0) _v2gInspect(_v2gPts[idx3]);
            }
          });
        }
      });
    });

    el.querySelectorAll('.v2g-belief-row').forEach(function(row) {
      row.addEventListener('click', function(ev) {
        if (ev.target && ev.target.closest && ev.target.closest('button')) return;
        var eidx = parseInt(row.getAttribute('data-edge-idx'), 10);
        if (!isNaN(eidx)) _v2gInspectEdge(eidx);
      });
    });
    } catch (inspectErr) {
      console.error('[v2g] inspect failed', inspectErr);
      try {
        el.innerHTML = '<div style="color:#f87171;font-size:0.75rem;">Inspect error: ' +
          _v2gEsc(String(inspectErr && inspectErr.message ? inspectErr.message : inspectErr)) +
          '</div>';
      } catch (e2) { /* ignore */ }
    }
  }

  async function _v2gRenameNode(p) {
    if (!p || p.isEpisode) return;
    var current = _v2gDisplayName(p);
    var msg = 'Display name for this node. The id stays "' + String(p.id) + '" so all beliefs keep linking correctly.';
    var name;
    if (window.kazmaPrompt) {
      name = await window.kazmaPrompt({
        title: 'Rename node',
        message: msg,
        defaultValue: current === 'You' && _v2gIsUser(p) ? 'You' : current,
        confirmText: 'Rename',
        placeholder: _v2gIsUser(p) ? 'e.g. Mubder or Kazma' : 'e.g. ShipX',
      });
    } else {
      name = window.prompt(msg, current);
    }
    if (name == null) return;
    name = String(name).trim();
    if (!name) {
      if (window.showToast) window.showToast('Name cannot be empty', 'error');
      return;
    }
    if (name === current) return;
    try {
      var resp = await fetch('/api/memory/v2/entities/' + encodeURIComponent(p.id) + '/rename', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ name: name }),
      });
      var data = await resp.json().catch(function() { return {}; });
      if (!resp.ok || !data.ok) {
        if (window.showToast) window.showToast(data.error || 'Rename failed', 'error');
        return;
      }
      if (window.showToast) window.showToast('Renamed to “' + name + '”', 'success');
      // Update cached raw node so filters don't flash old label
      for (var i = 0; i < (_v2gRawNodes || []).length; i++) {
        if (_v2gRawNodes[i] && _v2gRawNodes[i].id === p.id) {
          _v2gRawNodes[i].name = name;
          _v2gRawNodes[i].isVirtual = false;
        }
      }
      // Soft label update (struct same, names changed)
      _v2gLabelSig = '';
      _v2gApplyFilters();
      _v2gSelectedId = p.id;
      for (var j = 0; j < _v2gPts.length; j++) {
        if (_v2gPts[j].id === p.id) {
          _v2gInspect(_v2gPts[j]);
          break;
        }
      }
      _v2gNotifyList({ type: 'entity', id: p.id, name: name });
      _v2gRepaint();
    } catch (err) {
      if (window.showToast) window.showToast('Rename failed', 'error');
    }
  }

  // ── Filter logic (CLIENT-SIDE against cached data) ──
  // Filters don't re-fetch from the server — they filter the cached
  // _v2gRawNodes/_v2gRawLinks. This avoids the "empty graph on filter"
  // bug where the server-side entity_type filter found no matches.

  function _v2gBuildUrl() {
    var params = new URLSearchParams();
    // Only the time slider goes to the server (bi-temporal query)
    var slider = document.getElementById('v2g-time-slider');
    if (slider && _v2gTimeRange.max > 0 && parseFloat(slider.value) < 100) {
      var frac = parseFloat(slider.value) / 100;
      var ts = _v2gTimeRange.min + frac * (_v2gTimeRange.max - _v2gTimeRange.min);
      params.set('at', String(Math.floor(ts)));
    }
    var qs = params.toString();
    return '/api/memory/v2/graph' + (qs ? ('?' + qs) : '');
  }

  var _v2gLastStats = {};

  function _v2gRenderTruncation(stats) {
    var el = document.getElementById('v2g-trunc-banner');
    if (!el) return;
    var truncI18n = (window.__DASH_MEM_I18N && window.__DASH_MEM_I18N.graphTrunc) || 'showing first {n} of {total} nodes';
    if (stats && stats.truncated && stats.total_nodes > (stats.nodes || 0)) {
      var msg = String(truncI18n)
        .replace('{n}', stats.nodes || 0)
        .replace('{total}', stats.total_nodes);
      el.textContent = msg + ' — filter to narrow the view.';
      el.style.display = 'block';
    } else {
      el.style.display = 'none';
    }
  }

  // Refresh the canvas's screen-reader label with live node/edge counts so
  // the graph state is announced (the canvas itself is non-text). a11y.
  function _v2gUpdateCanvasAria(stats) {
    var canvas = document.getElementById('v2g-canvas');
    if (!canvas) return;
    var nodes = (stats && stats.nodes) || 0;
    var links = (stats && stats.links) || 0;
    var focus = _v2gSelectedId || '';
    var base = 'V2 belief topology graph. Arrow keys pan, plus minus zoom, Home resets. Click edges to edit or unlink beliefs.';
    canvas.setAttribute('aria-label', base + ' Currently showing ' + nodes + ' nodes and ' + links + ' edges.' + (focus ? ' Focused on ' + focus + '.' : ''));
  }

  async function _v2gLoad() {
    try {
      // P2: fetch view-only groupings in parallel (non-blocking — if it fails,
      // the tree layout's group-spring just has no data, no harm).
      try {
        var gresp = await fetch('/api/memory/v2/graph/groups', { credentials: 'same-origin' });
        var gdata = await gresp.json();
        _v2gGroups = (gdata && gdata.ok && Array.isArray(gdata.groups)) ? gdata.groups : [];
      } catch (ge) { _v2gGroups = []; }
      var resp = await fetch(_v2gBuildUrl());
      var data = await resp.json();
      var stats = data.stats || {};
      _v2gLastStats = stats;
      _v2gRawNodes = data.nodes || [];
      _v2gRawLinks = data.links || [];
      // The payload may also carry groups inline (faster, one fetch); prefer
      // the dedicated endpoint's result when both exist.
      if (!_v2gGroups.length && Array.isArray(data.groups)) _v2gGroups = data.groups;
      // Normalize link fields (neo4j probe may use predicate instead of label)
      _v2gRawLinks.forEach(function(l) {
        if (!l.label && l.predicate) l.label = l.predicate;
        if (!l.type) l.type = 'set';
        if (!l.source && l.subject) l.source = l.subject;
        if (!l.target && l.object) l.target = l.object;
      });
      _v2gRawNodes.forEach(function(n) {
        if (!n.name && n.label) n.name = n.label;
        if (!n.type) n.type = 'concept';
      });
      // Episode overlay — faint virtual nodes (not edges)
      if (_v2gShowEpisodes && _v2gEpisodeNodes.length) {
        var existing = {};
        _v2gRawNodes.forEach(function(n) { existing[n.id] = true; });
        _v2gEpisodeNodes.forEach(function(ep) {
          if (!existing[ep.id]) _v2gRawNodes.push(ep);
        });
      }
      if (stats.earliest && stats.latest && stats.latest > stats.earliest) {
        _v2gTimeRange = { min: stats.earliest, max: Math.max(stats.latest, Date.now() / 1000) };
      }
      _v2gRenderTruncation(stats);
      _v2gUpdateCanvasAria(stats);
      _v2gRenderFilters();
      _v2gApplyFilters();
      // If there are groupings AND the sim is cold (alpha=0), reheat once so
      // the group-spring can pull children into orbit. Without this, the sim
      // is settled on page load and groupings have no visible effect.
      // Only fires when alpha is already 0 (avoids reheating every 30s poll).
      if (_v2gGroups && _v2gGroups.length && _v2gAlpha < 0.01) _v2gHeated();
    } catch (e) { /* silent */ }
  }

  function _v2gTypeCountsFromData() {
    var counts = {};
    (_v2gRawNodes || []).forEach(function(n) {
      var t = n.type || 'concept';
      counts[t] = (counts[t] || 0) + 1;
    });
    return counts;
  }

  function _v2gPredCountsFromData() {
    var counts = {};
    (_v2gRawLinks || []).forEach(function(l) {
      var t = l.type || 'set';
      counts[t] = (counts[t] || 0) + 1;
    });
    return counts;
  }

  function _v2gUpdateLegend(entCounts) {
    var leg = document.getElementById('v2g-legend');
    if (!leg) return;
    _v2gRefreshPalette();
    var theme = _v2gTheme();
    var hasUser = (_v2gRawNodes || []).some(function(n) {
      return String(n.id || '').toLowerCase() === 'user';
    });
    var parts = [];
    if (hasUser) {
      parts.push(
        '<span title="You"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' +
        theme.user + ';margin-right:4px;box-shadow:0 0 6px ' + _v2gHexAlpha(theme.user, 0.5) +
        ';"></span>You</span>'
      );
    }
    var order = ['person', 'tool', 'concept', 'location', 'project', 'entity'];
    var keys = order.filter(function(k) { return (entCounts[k] || 0) > 0; });
    Object.keys(entCounts || {}).forEach(function(k) {
      if (keys.indexOf(k) < 0 && entCounts[k] > 0) keys.push(k);
    });
    if (!keys.length) keys = ['concept'];
    keys.forEach(function(k) {
      // person count includes user — still show type chip in accent family
      var c = _V2G_TYPE_COLORS[k] || theme.accent;
      var n = entCounts[k] || 0;
      parts.push(
        '<span><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' +
        c + ';margin-right:4px;box-shadow:0 0 5px ' + _v2gHexAlpha(c, 0.35) +
        ';"></span>' + k + (n ? ' <span style="opacity:0.7">(' + n + ')</span>' : '') + '</span>'
      );
    });
    parts.push('<span style="opacity:0.7;margin-left:2px;">· site accent</span>');
    leg.innerHTML = parts.join('');
  }

  function _v2gApplyFilters() {
    // Apply client-side entity-type + predicate-type + search filters
    var activeEnt = Object.keys(_v2gFilters.entity);
    var activePred = Object.keys(_v2gFilters.predicate);
    var search = '';
    var searchEl = document.getElementById('v2g-search');
    if (searchEl) search = searchEl.value.trim().toLowerCase();

    var nodes = _v2gRawNodes, links = _v2gRawLinks;
    // LOD: hard-cap node count for paint performance (P2-4)
    var _LOD_MAX = 200;
    if (nodes.length > _LOD_MAX) {
      nodes = nodes.slice(0, _LOD_MAX);
      var keep = {};
      nodes.forEach(function(n) { keep[n.id] = true; });
      links = links.filter(function(e) {
        var s = e.source && (e.source.id || e.source);
        var t = e.target && (e.target.id || e.target);
        return keep[s] && keep[t];
      });
    }

    // Filter links by predicate type + search
    if (activePred.length || search) {
      links = links.filter(function(l) {
        if (activePred.length && activePred.indexOf(l.type) < 0) return false;
        if (search) {
          var hay = ((l.label || '') + ' ' + (l.source || '') + ' ' + (l.target || '') + ' ' + (l.object_text || '')).toLowerCase();
          if (hay.indexOf(search) < 0) return false;
        }
        return true;
      });
    }
    // Keep only nodes referenced by surviving links (+ search match on node names).
    // Isolation-safe rendering (F2): a node that matches the entity-type filter
    // but lost its links (e.g. right after a Cut) is kept and marked `isolated`
    // so the paint loop can dim it + badge it — instead of silently dropping it.
    // Without this, cutting the only edge to a node while a filter is active made
    // the node vanish from the canvas even though it still exists in the DB.
    var nodeIds = new Set();
    links.forEach(function(l) { nodeIds.add(l.source); nodeIds.add(l.target); });
    if (search) {
      nodes.forEach(function(n) {
        var nm = (n.name || n.label || n.id || '').toLowerCase();
        if (nm.indexOf(search) >= 0) nodeIds.add(n.id);
      });
    }
    var linkFilterActive = !!(activePred.length || search);
    var isolatedCount = 0;
    nodes = nodes.filter(function(n) {
      // Always start from a clean per-pass flag.
      n.isolated = false;
      if (activeEnt.length && activeEnt.indexOf(n.type) < 0) return false;
      if (linkFilterActive) {
        if (nodeIds.has(n.id)) return true;
        // Entity-type matched but no surviving link → keep, but dim.
        // (Search still drops non-matching nodes — those are intentional hides.)
        if (!search) { n.isolated = true; isolatedCount++; return true; }
        return false;
      }
      return true;
    });
    // If entity-type filter is on, re-filter links to only those between surviving nodes
    if (activeEnt.length) {
      var keepIds = new Set(nodes.map(function(n) { return n.id; }));
      links = links.filter(function(l) { return keepIds.has(l.source) && keepIds.has(l.target); });
    }

    var sl = document.getElementById('v2g-stats-line');
    // Expose the isolated count for the ops-bar indicator + tests.
    _v2gIsolatedCount = isolatedCount;
    if (sl) {
      var st = _v2gLastStats || {};
      var paint = st.paint_source || st.source || 'sqlite';
      var gprov = st.graph_provider || paint;
      var parts = [nodes.length + ' nodes · ' + links.length + ' beliefs'];
      if (isolatedCount > 0) parts.push(isolatedCount + ' isolated');
      parts.push('paint ' + paint);
      if (gprov === 'neo4j') {
        parts.push(st.graph_online ? 'neo4j dual-write online' : 'neo4j offline');
      }
      if (activeEnt.length || activePred.length || search) {
        parts.push('filtered from ' + _v2gRawNodes.length);
      }
      sl.textContent = parts.join(' · ');
    }
    _v2gUpdateLegend(_v2gTypeCountsFromData());
    // Let _v2gDrawCanvas compare signatures. Clearing always forced a full
    // spiral re-layout and wiped user-dragged positions on every 30s poll /
    // filter pass. Positions are restored from _v2gPosCache on rebuild.
    _v2gDrawCanvas(nodes, links);
  }

  // F4: predicate chips — reuse existing vocabulary instead of inventing
  // near-duplicate predicate names (the root cause of the reset-explosion
  // mess: next_reset / grok_next_reset / grok_next_reset_personal).
  var _v2gPredVocab = [];
  async function _v2gLoadPredChips() {
    try {
      var data = await _v2gApiJson('/api/memory/v2/vocab');
      if (!data || !data.ok || !Array.isArray(data.predicates)) return;
      _v2gPredVocab = data.predicates;
    } catch (e) { return; }
    var box = document.getElementById('v2g-pred-chips-list');
    if (!box) return;
    var cur = _v2gOpsPredicate().toLowerCase();
    // Canonical predicates pinned at top so they're always one click away.
    var canonical = ['related_to', 'has_project', 'next_reset', 'supports_channels', 'works_at', 'email_is'];
    var preds = _v2gPredVocab.map(function (p) { return p.name; });
    canonical.forEach(function (c) { if (preds.indexOf(c) < 0) preds.unshift(c); });
    // De-dup + cap at 40 for the bar (search/typeahead can extend later).
    var seen = {}; var ordered = [];
    preds.forEach(function (p) { if (p && !seen[p]) { seen[p] = 1; ordered.push(p); } });
    ordered = ordered.slice(0, 40);
    box.innerHTML = ordered.map(function (p) {
      var active = (p === cur) ? 'chip-sel' : '';
      var esc = String(p).replace(/"/g, '&quot;');
      return '<button type="button" class="v2g-chip ' + active + '" data-pred="' + esc + '" '
        + 'style="font-size:0.62rem;font-family:var(--font-mono);padding:2px 8px;border-radius:999px;'
        + 'border:1px solid ' + (active ? 'var(--accent)' : 'var(--border-subtle)') + ';'
        + 'background:' + (active ? 'rgba(34,211,238,0.14)' : 'rgba(255,255,255,0.03)') + ';'
        + 'color:' + (active ? 'var(--text-primary)' : 'var(--text-secondary)') + ';cursor:pointer;">'
        + esc + '</button>';
    }).join('');
    box.querySelectorAll('[data-pred]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var predEl = document.getElementById('v2g-ops-predicate');
        if (predEl) predEl.value = btn.getAttribute('data-pred') || '';
        _v2gBroadcastSlots();
        _v2gLoadPredChips(); // refresh active styling
      });
    });
  }

  // F4: similarity hint — when the typed predicate is a near-miss of an
  // existing one (e.g. "grok_next_reset" when "next_reset" exists), nudge
  // toward reuse. Non-blocking; shown in the ops hint line.
  function _v2gMaybeHintPredicate(typed) {
    var hintEl = document.getElementById('v2g-ops-hint');
    if (!hintEl) return;
    typed = String(typed || '').trim().toLowerCase();
    if (!typed || !_v2gPredVocab.length) return;
    // Exact match → no hint.
    for (var i = 0; i < _v2gPredVocab.length; i++) {
      if (String(_v2gPredVocab[i].name).toLowerCase() === typed) {
        hintEl.textContent = 'Reusing existing predicate.';
        return;
      }
    }
    // Near-miss: typed contains an existing predicate as a token-substring
    // (catches grok_next_reset_personal ⊇ next_reset). Tokenize on _ .
    var typedTokens = typed.split('_');
    for (var j = 0; j < _v2gPredVocab.length; j++) {
      var name = String(_v2gPredVocab[j].name).toLowerCase();
      if (name.length < 4) continue;
      if (typed !== name && typedTokens.indexOf(name) >= 0) {
        hintEl.innerHTML = 'Similar to <b style="color:var(--accent);cursor:pointer;" data-adopt="' + name + '">' + name + '</b>? Click to reuse.';
        var adopt = hintEl.querySelector('[data-adopt]');
        if (adopt) adopt.addEventListener('click', function () {
          var predEl = document.getElementById('v2g-ops-predicate');
          if (predEl) predEl.value = name;
          _v2gBroadcastSlots();
          _v2gLoadPredChips();
        });
        return;
      }
    }
  }

  function _v2gRenderFilters() {
    var entBox = document.getElementById('v2g-filters-entity');
    var predBox = document.getElementById('v2g-filters-predicate');
    var entCounts = _v2gTypeCountsFromData();
    var predCounts = _v2gPredCountsFromData();
    // Prefer types present in data; always offer the core set so empty graphs stay usable
    var coreEnt = ['person', 'tool', 'concept', 'location', 'project'];
    var entTypes = coreEnt.slice();
    Object.keys(entCounts).forEach(function(k) {
      if (entTypes.indexOf(k) < 0) entTypes.push(k);
    });
    // Drop core types that never appear once we have data (except keep all until first load)
    if (_v2gRawNodes.length) {
      entTypes = entTypes.filter(function(k) {
        return (entCounts[k] || 0) > 0 || !!_v2gFilters.entity[k];
      });
      if (!entTypes.length) entTypes = coreEnt.slice();
    }
    var corePred = ['functional', 'set', 'state'];
    var predTypes = corePred.slice();
    Object.keys(predCounts).forEach(function(k) {
      if (predTypes.indexOf(k) < 0) predTypes.push(k);
    });
    if (_v2gRawLinks.length) {
      predTypes = predTypes.filter(function(k) {
        return (predCounts[k] || 0) > 0 || !!_v2gFilters.predicate[k];
      });
      if (!predTypes.length) predTypes = corePred.slice();
    }
    function makeToggle(label, group, key, color, count) {
      var id = 'v2g-ft-' + group + '-' + key;
      var active = !!_v2gFilters[group][key];
      var cnt = (count != null && count > 0) ? ' <span style="opacity:0.7;font-family:var(--font-mono);">' + count + '</span>' : '';
      return '<label title="' + label + (count != null ? ': ' + count : '') + '" style="display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:0.65rem;padding:2px 7px;border-radius:999px;border:1px solid ' + (active ? color : 'var(--border-subtle)') + ';background:' + (active ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)') + ';' + (active ? 'color:var(--text-primary);' : 'color:var(--text-muted);') + '">' +
             '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (active ? color : 'transparent') + ';border:1px solid ' + color + ';flex-shrink:0;"></span>' +
             '<input type="checkbox" id="' + id + '" ' + (active ? 'checked' : '') + ' style="display:none;">' + label + cnt + '</label>';
    }
    if (entBox) entBox.innerHTML = entTypes.map(function(t) {
      return makeToggle(t, 'entity', t, _V2G_TYPE_COLORS[t] || '#94a3b8', entCounts[t] || 0);
    }).join('');
    if (predBox) predBox.innerHTML = predTypes.map(function(t) {
      return makeToggle(t, 'predicate', t, _V2G_PRED_COLORS[t] || '#94a3b8', predCounts[t] || 0);
    }).join('');
    // Wire change handlers — multi-select (toggle each independently)
    document.querySelectorAll('[id^="v2g-ft-entity-"]').forEach(function(cb) {
      cb.addEventListener('change', function() {
        var key = cb.id.replace('v2g-ft-entity-', '');
        if (cb.checked) _v2gFilters.entity[key] = true;
        else delete _v2gFilters.entity[key];
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    });
    document.querySelectorAll('[id^="v2g-ft-predicate-"]').forEach(function(cb) {
      cb.addEventListener('change', function() {
        var key = cb.id.replace('v2g-ft-predicate-', '');
        if (cb.checked) _v2gFilters.predicate[key] = true;
        else delete _v2gFilters.predicate[key];
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    });
    // Active filter chips + reset button
    var chips = document.getElementById('v2g-active-filters');
    if (chips) {
      var all = Object.keys(_v2gFilters.entity).map(function(k) { return { group: 'entity', key: k, label: 'entity:' + k }; })
        .concat(Object.keys(_v2gFilters.predicate).map(function(k) { return { group: 'predicate', key: k, label: 'pred:' + k }; }));
      var html = all.map(function(c, idx) {
        return '<span data-fg="' + c.group + '" data-fk="' + c.key + '" style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:rgba(59,130,246,0.15);color:#93c5fd;cursor:pointer;display:inline-flex;align-items:center;gap:3px;">' + c.label + ' ' + KazmaIcons.span('x') + '</span>';
      }).join('');
      if (all.length) html += '<span id="v2g-reset-filters" style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:rgba(239,68,68,0.12);color:#f87171;cursor:pointer;margin-left:4px;">Reset all</span>';
      chips.innerHTML = html;
      chips.querySelectorAll('span[data-fg]').forEach(function(span) {
        span.addEventListener('click', function() {
          var g = span.getAttribute('data-fg');
          var k = span.getAttribute('data-fk');
          if (g && k && _v2gFilters[g]) delete _v2gFilters[g][k];
          _v2gRenderFilters(); _v2gApplyFilters();
        });
      });
      var reset = document.getElementById('v2g-reset-filters');
      if (reset) reset.addEventListener('click', function() {
        _v2gFilters = { entity: {}, predicate: {} };
        var s = document.getElementById('v2g-search'); if (s) s.value = '';
        _v2gRenderFilters(); _v2gApplyFilters();
      });
    }
  }

  var _v2gPlayTimer = null;
  var _v2gPreferReducedMotion = false;
  try {
    _v2gPreferReducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (e) { _v2gPreferReducedMotion = false; }

  function _v2gStopPlay() {
    if (_v2gPlayTimer) { clearInterval(_v2gPlayTimer); _v2gPlayTimer = null; }
    var playBtn = document.getElementById('v2g-time-play');
    if (playBtn) playBtn.textContent = playBtn.getAttribute('data-play-label') || 'Play';
  }

  var _v2gPathIds = {};
  var _v2gEpisodeNodes = [];
  var _v2gShowEpisodes = false;

  function _v2gApplyPathFromQuery() {
    var seeds = (_v2gLastQuerySeeds || []).map(function(s) { return String(s).toLowerCase(); });
    if (!seeds.length) {
      var q = ((document.getElementById('v2g-search') || {}).value || (document.getElementById('v2-probe-input') || {}).value || '').trim();
      if (q) seeds = q.toLowerCase().split(/\s+/).filter(function(w) { return w.length > 2; });
    }
    _v2gPathIds = {};
    var matched = 0;
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var hay = ((p.id || '') + ' ' + (p.fullLabel || '') + ' ' + (p.label || '')).toLowerCase();
      for (var s = 0; s < seeds.length; s++) {
        if (seeds[s] && hay.indexOf(seeds[s]) >= 0) {
          _v2gPathIds[p.id] = true;
          matched++;
          break;
        }
      }
    }
    // Also mark edges between path nodes
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e], A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (A && B && _v2gPathIds[A.id] && _v2gPathIds[B.id]) ed.pathHot = true;
      else ed.pathHot = false;
    }
    if (window.showToast) {
      window.showToast(matched ? ('Path: highlighted ' + matched + ' nodes') : 'No matching nodes for query path', matched ? 'success' : 'info');
    }
    // Zoom to first path node
    for (var j = 0; j < _v2gPts.length; j++) {
      if (_v2gPathIds[_v2gPts[j].id]) {
        _v2gSelectedId = _v2gPts[j].id;
        var size = _v2gCanvasSize();
        if (size) {
          _v2gView.scale = 1.8;
          _v2gView.ox = size.w / 2 - _v2gPts[j].x * 1.8;
          _v2gView.oy = size.h / 2 - _v2gPts[j].y * 1.8;
        }
        break;
      }
    }
    _v2gHeated();
    _v2gRepaint();
  }

  async function _v2gLoadEpisodes() {
    try {
      var resp = await fetch('/api/memory/v2/episodes?limit=30');
      var data = await resp.json();
      _v2gEpisodeNodes = (data.episodes || []).map(function(ep) {
        return {
          id: 'ep:' + ep.id,
          name: (ep.preview || ep.id).slice(0, 40),
          type: 'entity',
          beliefCount: 1,
          isHighStakes: false,
          isVirtual: true,
          isEpisode: true,
          tier: ep.tier || '',
        };
      });
    } catch (e) {
      _v2gEpisodeNodes = [];
    }
  }

  function _v2gExportPng() {
    var canvas = document.getElementById('v2g-canvas');
    if (!canvas) return;
    try {
      var a = document.createElement('a');
      a.download = 'kazma-v2-topology.png';
      a.href = canvas.toDataURL('image/png');
      a.click();
      if (window.showToast) window.showToast('PNG downloaded', 'success');
    } catch (e) {
      if (window.showToast) window.showToast('PNG export failed', 'error');
    }
  }

  function _v2gExportSvg() {
    var W = 800, H = 500;
    var size = _v2gCanvasSize();
    if (size) { W = size.w; H = size.h; }
    var parts = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">',
      '<rect width="100%" height="100%" fill="#0a0f14"/>'];
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e], A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      parts.push('<line x1="' + _v2gSX(A.x).toFixed(1) + '" y1="' + _v2gSY(A.y).toFixed(1) +
        '" x2="' + _v2gSX(B.x).toFixed(1) + '" y2="' + _v2gSY(B.y).toFixed(1) +
        '" stroke="#3b82f6" stroke-opacity="0.45" stroke-width="1.5"/>');
    }
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var col = _v2gNodeColor(p);
      parts.push('<circle cx="' + _v2gSX(p.x).toFixed(1) + '" cy="' + _v2gSY(p.y).toFixed(1) +
        '" r="' + p.r + '" fill="' + col + '" fill-opacity="0.9"/>');
      parts.push('<text x="' + _v2gSX(p.x).toFixed(1) + '" y="' + (_v2gSY(p.y) + p.r + 12).toFixed(1) +
        '" fill="#e6edf3" font-size="11" text-anchor="middle" font-family="sans-serif">' +
        _v2gEsc(p.label || p.id).replace(/&/g, '&amp;') + '</text>');
    }
    parts.push('</svg>');
    var blob = new Blob([parts.join('')], { type: 'image/svg+xml' });
    var a = document.createElement('a');
    a.download = 'kazma-v2-topology.svg';
    a.href = URL.createObjectURL(blob);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(a.href); }, 2000);
    if (window.showToast) window.showToast('SVG downloaded', 'success');
  }

  function _v2gWireControls() {
    var refresh = document.getElementById('v2g-refresh');
    if (refresh) refresh.addEventListener('click', _v2gLoad);
    var search = document.getElementById('v2g-search');
    if (search) { var sto; search.addEventListener('input', function() { clearTimeout(sto); sto = setTimeout(_v2gApplyFilters, 250); }); }
    document.getElementById('v2g-path-query')?.addEventListener('click', _v2gApplyPathFromQuery);
    document.getElementById('v2g-export-png')?.addEventListener('click', _v2gExportPng);
    document.getElementById('v2g-export-svg')?.addEventListener('click', _v2gExportSvg);

    // Prevent canvas pointer capture from eating inspect-panel clicks
    var inspectEl = document.getElementById('v2g-inspect');
    if (inspectEl && !inspectEl._v2gPointerGuard) {
      inspectEl._v2gPointerGuard = true;
      ['pointerdown', 'mousedown', 'click', 'touchstart'].forEach(function(evName) {
        inspectEl.addEventListener(evName, function(ev) {
          ev.stopPropagation();
        });
      });
    }

    // Graph ops bar
    var linkBtn = document.getElementById('v2g-ops-link');
    if (linkBtn && !linkBtn._v2gWired) {
      linkBtn._v2gWired = true;
      linkBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        if (_v2gOps.sourceId && _v2gOps.targetId) {
          _v2gDoLink(_v2gOps.sourceId, _v2gOps.targetId, _v2gOpsPredicate());
        } else if (_v2gOps.mode === 'link') {
          _v2gOps.mode = null;
          _v2gSyncOpsBar();
          _v2gToast('Link mode cancelled', 'info');
        } else {
          _v2gEnterMode('link');
        }
      });
    }
    var mergeBtn = document.getElementById('v2g-ops-merge');
    if (mergeBtn && !mergeBtn._v2gWired) {
      mergeBtn._v2gWired = true;
      mergeBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        if (_v2gOps.sourceId && _v2gOps.targetId) {
          _v2gDoMerge(_v2gOps.sourceId, _v2gOps.targetId);
        } else if (_v2gOps.mode === 'merge') {
          _v2gOps.mode = null;
          _v2gSyncOpsBar();
          _v2gToast('Merge mode cancelled', 'info');
        } else {
          _v2gEnterMode('merge');
        }
      });
    }
    var swapBtn = document.getElementById('v2g-ops-swap');
    if (swapBtn) {
      swapBtn.addEventListener('click', function() {
        var s = _v2gOps.sourceId;
        _v2gOps.sourceId = _v2gOps.targetId;
        _v2gOps.targetId = s;
        _v2gBroadcastSlots();
      });
    }
    var clearBtn = document.getElementById('v2g-ops-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', function() {
        _v2gClearSlots();
        _v2gToast('Slots cleared', 'info');
      });
    }
    var predEl = document.getElementById('v2g-ops-predicate');
    if (predEl) {
      predEl.addEventListener('change', function() { _v2gBroadcastSlots(); });
      // F4: live similarity hint as the operator types a predicate.
      predEl.addEventListener('input', function() { _v2gMaybeHintPredicate(predEl.value); });
    }
    // F4: load predicate chips from /vocab on init.
    _v2gLoadPredChips();
    // List → graph slot sync (Entities form Src/Tgt)
    window.addEventListener('kazma:memory-ops-slots', function(ev) {
      var d = (ev && ev.detail) || {};
      // Avoid feedback loop: only apply if values differ from graph state
      // and the event is marked from list (fromList: true)
      if (!d.fromList) return;
      if (d.sourceId !== undefined) _v2gOps.sourceId = d.sourceId || null;
      if (d.targetId !== undefined) _v2gOps.targetId = d.targetId || null;
      if (d.predicate && predEl) predEl.value = d.predicate;
      _v2gSyncOpsBar();
      _v2gRepaint();
    });
    _v2gSyncOpsBar();
    var epToggle = document.getElementById('v2g-episode-overlay');
    if (epToggle) {
      epToggle.addEventListener('change', async function() {
        _v2gShowEpisodes = !!epToggle.checked;
        if (_v2gShowEpisodes && !_v2gEpisodeNodes.length) await _v2gLoadEpisodes();
        _v2gLoad();
      });
    }
    // Keyboard a11y: pan / zoom / reset
    var canvas = document.getElementById('v2g-canvas');
    if (canvas && !canvas._v2gKeys) {
      canvas._v2gKeys = true;
      canvas.addEventListener('keydown', function(ev) {
        var step = 28;
        var handled = true;
        if (ev.key === 'ArrowLeft') _v2gView.ox += step;
        else if (ev.key === 'ArrowRight') _v2gView.ox -= step;
        else if (ev.key === 'ArrowUp') _v2gView.oy += step;
        else if (ev.key === 'ArrowDown') _v2gView.oy -= step;
        else if (ev.key === '+' || ev.key === '=') {
          _v2gView.scale = Math.min(_v2gMaxScale, _v2gView.scale * 1.15);
        } else if (ev.key === '-' || ev.key === '_') {
          _v2gView.scale = Math.max(_v2gMinScale, _v2gView.scale / 1.15);
        } else if (ev.key === 'Home') {
          _v2gView = { scale: 1, ox: 0, oy: 0 };
        } else if (ev.key === 'Escape') {
          _v2gSelectedId = null; _v2gPathIds = {};
          _v2gOps.mode = null; _v2gOps.selectedEdgeIdx = -1;
          _v2gHighlightSubj = null; _v2gHighlightObj = null;
          _v2gSyncOpsBar();
        } else {
          handled = false;
        }
        if (handled) { ev.preventDefault(); _v2gRepaint(); }
      });
    }
    // Time slider + play/pause scrub
    var slider = document.getElementById('v2g-time-slider');
    var label = document.getElementById('v2g-time-label');
    var liveBtn = document.getElementById('v2g-time-live');
    var playBtn = document.getElementById('v2g-time-play');
    if (playBtn) playBtn.setAttribute('data-play-label', playBtn.textContent || 'Play');
    function _updateTimeLabel() {
      if (!slider || !label) return;
      var v = parseFloat(slider.value);
      if (v >= 99.5) label.textContent = 'Live (now)';
      else if (_v2gTimeRange.max > 0) {
        var ts = _v2gTimeRange.min + (v / 100) * (_v2gTimeRange.max - _v2gTimeRange.min);
        label.textContent = new Date(ts * 1000).toLocaleDateString();
      } else label.textContent = '—';
    }
    if (slider) {
      slider.addEventListener('input', _updateTimeLabel);
      slider.addEventListener('change', function() { _v2gStopPlay(); _v2gLoad(); });
    }
    if (liveBtn) liveBtn.addEventListener('click', function() {
      _v2gStopPlay();
      if (slider) slider.value = 100;
      if (label) label.textContent = 'Live (now)';
      _v2gLoad();
    });
    if (playBtn && slider) {
      playBtn.addEventListener('click', function() {
        if (_v2gPreferReducedMotion) {
          // One-step instead of animation when user prefers reduced motion
          var v = parseFloat(slider.value);
          slider.value = String(Math.min(100, v + 10));
          _updateTimeLabel();
          _v2gLoad();
          return;
        }
        if (_v2gPlayTimer) {
          _v2gStopPlay();
          return;
        }
        playBtn.textContent = playBtn.getAttribute('data-pause-label') || 'Pause';
        if (parseFloat(slider.value) >= 99) slider.value = '0';
        _v2gPlayTimer = setInterval(function() {
          var cur = parseFloat(slider.value);
          if (cur >= 100) {
            _v2gStopPlay();
            return;
          }
          slider.value = String(Math.min(100, cur + 2));
          _updateTimeLabel();
          _v2gLoad();
        }, 400);
      });
    }
    // Resize debouncer
    var rto;
    window.addEventListener('resize', function() { clearTimeout(rto); rto = setTimeout(function() { if (_v2gPts.length) _v2gRepaint(); }, 200); });
  }

  // Expose for Memory admin list ↔ graph bridge
  window._v2gLoad = _v2gLoad;
  window._v2gForceReload = _v2gReloadGraph;
  window._v2gRenameNode = _v2gRenameNode;
  // F2: live isolated-node count (for the ops-bar indicator + tests). Getter
  // because the value is recomputed each filter pass.
  window._v2gGetIsolatedCount = function() { return _v2gIsolatedCount; };
  // P2: positions by id (for tests + operator inspection of the tree layout).
  window._v2gGetNodePos = function(id) {
    for (var i = 0; i < _v2gPts.length; i++) {
      if (_v2gPts[i].id === id) return { x: _v2gPts[i].x, y: _v2gPts[i].y, tier: _v2gPts[i].tier, groupParent: _v2gPts[i].groupParent };
    }
    return null;
  };
  window._v2gSelectEntity = _v2gSelectEntity;
  window._v2gSelectBelief = function(subj, obj, beliefId, opts) {
    opts = opts || {};
    var ok = _v2gSelectByBelief(subj, obj, beliefId);
    if (ok && opts.notify !== false) {
      _v2gNotifyList({
        type: 'belief',
        id: beliefId || null,
        subject: subj,
        object: obj,
      });
    }
    return ok;
  };
  window._v2gSetOpsSlots = function(src, tgt, pred) {
    if (src !== undefined) _v2gOps.sourceId = src || null;
    if (tgt !== undefined) _v2gOps.targetId = tgt || null;
    var predEl = document.getElementById('v2g-ops-predicate');
    if (pred && predEl) predEl.value = pred;
    _v2gSyncOpsBar();
    _v2gRepaint();
  };
  window._v2gGetOpsSlots = function() {
    return {
      sourceId: _v2gOps.sourceId,
      targetId: _v2gOps.targetId,
      predicate: _v2gOpsPredicate(),
      mode: _v2gOps.mode,
    };
  };
  window._v2gDoLink = _v2gDoLink;
  window._v2gDoMerge = _v2gDoMerge;
  window._v2gUnlinkBelief = _v2gUnlinkBelief;
  window._v2gCutHubLinks = _v2gCutHubLinks;
  window._v2gCutEdges = _v2gCutEdges;
  window._v2gEditBeliefById = _v2gEditBeliefById;
  // F1: move (repoint) a belief endpoint to another node by clicking.
  window._v2gRepointBelief = _v2gRepointBelief;
  // F: view-only grouping (cluster + tier without mutating memory).
  window._v2gGroupUnder = _v2gGroupUnder;
  window._v2gToggleMajor = _v2gToggleMajor;

  try {
    _v2gRenderFilters();
    _v2gWireControls();
    _v2gLoad();
    setInterval(function() { if (!document.hidden) _v2gLoad(); }, 30000);
  } catch (e) { /* V2 graph optional */ }
})();
