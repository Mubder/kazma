// Memory console — health polling, KPIs, component board, V2 panel (queue/
// merges/procedural/quality/beliefs/drawer), probe/federated/golden search,
// maintenance deck. Shared helpers live here. Split from memory_console.js.
import { state, I18N, dispatchOpsDone } from './state.js';

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
  var state._memCompOpen = {};
  var state._memCompToggleAllWired = false;

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
    if (key) state._memCompOpen[key] = !!open;
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
    if (allBtn && !state._memCompToggleAllWired) {
      state._memCompToggleAllWired = true;
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
    var open = state._memCompOpen[key] === true;
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

  export async function pollMemoryStatus() {
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

  export async function loadBackups() {
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
  setInterval(pollMemoryStatus, 5000);

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

  export async function pollV2Health() {
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

  var state._v2gLastQuerySeeds = []; // entity/id strings from last probe/federated hit
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
    state._v2gLastQuerySeeds = seeds;
  }
  export async function runV2Probe() {
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
      q.split(/\s+/).forEach(function(w) { if (w.length > 2) state._v2gLastQuerySeeds.push(w); });
      const lines = [];
      function _srcChips(h) {
        var arr = (h.sources && h.sources.length) ? h.sources : (h.source ? [h.source] : []);
        if (!arr.length) return '';
        return arr.map(function(s) {
          var color = '#94a3b8';
          var key = String(s || '').toLowerCase();
          if (key.indexOf('ppr') >= 0 || key.indexOf('belief_ppr') >= 0) color = '#a78bfa';
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

  var state._openBeliefId = null;
  export async function openBeliefDrawer(beliefId) {
    state._openBeliefId = beliefId;
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
    if (!state._openBeliefId) return;
    const ok = window.kazmaConfirm
      ? await window.kazmaConfirm({ title: 'Unlink belief?', message: 'Soft-invalidate this edge from active memory.' })
      : confirm('Unlink (invalidate) belief?');
    if (!ok) return;
    await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(state._openBeliefId) + '/invalidate', { method: 'POST' });
    const d = document.getElementById('v2-belief-drawer');
    if (d) d.style.display = 'none';
    loadV2Beliefs();
    pollV2Health();
    if (typeof window._v2gForceReload === 'function') window._v2gForceReload();
    try {
      window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'unlink', beliefId: state._openBeliefId } }));
    } catch (e) { /* ignore */ }
  });

  export async function loadV2Queue() {
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

  export async function loadV2Merges() {
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
          '<button type="button" data-mid="' + _esc(m.id) + '" data-act="approve" class="v2-merge-act btn btn-sm" style="font-size:0.65rem;padding:1px 6px;">✓</button>' +
          '<button type="button" data-mid="' + _esc(m.id) + '" data-act="reject" class="v2-merge-act btn btn-sm" style="font-size:0.65rem;padding:1px 6px;">✗</button>' +
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

  export async function runFederatedSearch() {
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
      qWords.split(/\s+/).forEach(function(w) { if (w.length > 2) state._v2gLastQuerySeeds.push(w); });
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
  export async function loadV2Procedural() {
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
  export async function loadV2Quality() {
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
  setInterval(function() { loadV2Queue(); loadV2Merges(); loadV2Procedural(); loadV2Quality(); }, 15000);

  export async function loadV2Beliefs(q) {
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
          row.style.borderLeft = '3px solid #6366f1';
        });
      });
    } catch (e) { /* silent */ }
  }

  // Select a belief's OBJECT node in the graph canvas + zoom to it.
  // The object is the interesting entity (e.g. "teal", "Paris"), not
  // the subject (usually "user"). Highlights both endpoints + the edge.
