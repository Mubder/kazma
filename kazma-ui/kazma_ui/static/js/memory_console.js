/**
 * Memory console — health, V2 KPIs, belief list, topology graph, probe, maintenance.
 * Moved from dashboard.html so /memory is the single memory hub.
 * Expects window.__DASH_MEM_I18N for labels (optional).
 */
(function () {
  "use strict";
  var I18N = window.__DASH_MEM_I18N || window.I18N || {};
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
        if (!cards.length) return '';
        return (
          '<div>' +
            '<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-tertiary);margin-bottom:6px;">' + g.title + '</div>' +
            '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;">' +
              cards.map(_memCardHtml).join('') +
            '</div>' +
          '</div>'
        );
      }).join('');
      var rest = components.filter(function(c) { return !used[c.id]; });
      if (rest.length) {
        html += (
          '<div>' +
            '<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-tertiary);margin-bottom:6px;">Other</div>' +
            '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;">' +
              rest.map(_memCardHtml).join('') +
            '</div>' +
          '</div>'
        );
      }
      grid.innerHTML = html;
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
          return '<div style="display:flex;align-items:center;gap:8px;font-size:0.75rem;">' +
                 '<span style="width:60px;color:var(--text-secondary);">' + name + '</span>' +
                 '<div style="flex:1;height:8px;background:rgba(255,255,255,0.05);border-radius:4px;overflow:hidden;">' +
                   '<div style="width:' + pct + '%;height:100%;background:' + color + ';border-radius:4px;"></div>' +
                 '</div>' +
                 '<span style="width:32px;text-align:right;font-family:var(--font-mono);color:var(--text-primary);">' + (count||0) + '</span>' +
                 '</div>';
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
      body.innerHTML =
        '<div><b>' + _esc(b.subject) + '</b> ' + _esc((b.predicate||'').replace(/_/g,' ')) +
        ' <b>' + _esc(b.object) + '</b></div>' +
        '<div style="color:var(--text-muted);margin-top:4px;">id: ' + _esc(b.id) +
        ' · conf ' + Math.round((b.confidence||0)*100) + '%' +
        ' · imp ' + (b.structural_importance||'?') +
        ' · access ' + (b.access_count||0) + '</div>' +
        (chain ? '<div style="margin-top:6px;color:var(--text-muted);">supersedes chain: ' + chain + '</div>' : '');
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
      ? await window.kazmaConfirm({ title: 'Invalidate belief?', message: 'Soft-delete this fact from active memory.' })
      : confirm('Invalidate belief?');
    if (!ok) return;
    await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(_openBeliefId) + '/invalidate', { method: 'POST' });
    const d = document.getElementById('v2-belief-drawer');
    if (d) d.style.display = 'none';
    loadV2Beliefs();
    pollV2Health();
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
  setInterval(function() { loadV2Queue(); loadV2Merges(); loadV2Procedural(); loadV2Quality(); }, 15000);

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
          row.style.borderLeft = '3px solid #6366f1';
        });
      });
    } catch (e) { /* silent */ }
  }

  // Select a belief's OBJECT node in the graph canvas + zoom to it.
  // The object is the interesting entity (e.g. "teal", "Paris"), not
  // the subject (usually "user"). Highlights both endpoints + the edge.
  var _v2gHighlightSubj = null, _v2gHighlightObj = null;
  function _v2gSelectByBelief(subj, obj) {
    if (!_v2gPts.length) return;
    // Find the OBJECT node first (the specific entity, not "user")
    var objIdx = -1, subjIdx = -1;
    for (var i = 0; i < _v2gPts.length; i++) {
      var pid = _v2gPts[i].id;
      var plabel = (_v2gPts[i].fullLabel || _v2gPts[i].label || '').toLowerCase();
      // Match by id or by label (the graph node id is a slug; the belief
      // object may be a display name — try both, case-insensitive)
      if (objIdx < 0 && (pid === obj || pid === _v2gSlugify(obj) || plabel === obj.toLowerCase())) objIdx = i;
      if (subjIdx < 0 && (pid === subj || pid === _v2gSlugify(subj) || plabel === subj.toLowerCase())) subjIdx = i;
    }
    // Prefer the object node (the specific entity); fall back to subject
    var targetIdx = objIdx >= 0 ? objIdx : subjIdx;
    if (targetIdx < 0) return;
    var p = _v2gPts[targetIdx];
    _v2gSelectedId = p.id;
    // Store highlight endpoints so _v2gPaint can emphasize the edge
    _v2gHighlightSubj = subjIdx >= 0 ? _v2gPts[subjIdx].id : null;
    _v2gHighlightObj = objIdx >= 0 ? _v2gPts[objIdx].id : null;
    _v2gInspect(p);
    // Zoom to the target node
    var size = _v2gCanvasSize();
    if (size) {
      _v2gView.scale = 2.5;
      _v2gView.ox = size.w / 2 - p.x * 2.5;
      _v2gView.oy = size.h / 2 - p.y * 2.5;
      _v2gHeated();
      _v2gRepaint();
    }
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
    setInterval(pollV2Health, 5000);
  } catch (e) { /* V2 panel optional */ }

  // ══ V2 BELIEF TOPOLOGY GRAPH ══════════════════════════════════
  // Self-contained force-directed canvas for the V2 belief graph.
  // Separate _v2g* namespace — does NOT touch the L2 _kg* state.
  // Features: entity nodes (colored by type, sized by belief count),
  // belief edges (colored by predicate_type, dashed if superseded),
  // high-stakes red halo, bi-temporal time slider, filter toggles.

  var _v2gPts = [], _v2gEdges = [], _v2gIds = {}, _v2gSig = '';
  var _v2gView = { scale: 1, ox: 0, oy: 0 };
  var _v2gAlpha = 0, _v2gAnim = null, _v2gDrag = null, _v2gHover = -1, _v2gSelectedId = null;
  var _v2gCap = 80, _v2gNodeBaseR = 7;
  var _v2gMinScale = 0.3, _v2gMaxScale = 4;
  var _v2gTimeRange = { min: 0, max: 0 };
  var _v2gFilters = { entity: {}, predicate: {} };
  // Cache the last full dataset so client-side filters don't need a re-fetch
  var _v2gRawNodes = [], _v2gRawLinks = [];

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
    var id = String(p.id || '').toLowerCase();
    return id === 'user' || id === 'you' || !!p.isUser;
  }
  function _v2gNodeColor(p) {
    var t = _v2gTheme();
    if (_v2gIsUser(p)) return t.user;
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
        if (!(_v2gDrag && _v2gDrag.idx === i)) { a.vx -= fx; a.vy -= fy; }
        if (!(_v2gDrag && _v2gDrag.idx === j)) { b.vx += fx; b.vy += fy; }
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
      if (!(_v2gDrag && _v2gDrag.idx === ed.a)) { A.vx += fx; A.vy += fy; }
      if (!(_v2gDrag && _v2gDrag.idx === ed.b)) { B.vx -= fx; B.vy -= fy; }
    }
    // Gravity + collision-aware integration
    var margin = 30;
    for (var p = 0; p < n; p++) {
      var pt = _v2gPts[p];
      if (_v2gDrag && _v2gDrag.idx === p) { pt.vx = 0; pt.vy = 0; continue; }
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
      if (_v2gAnim) { cancelAnimationFrame(_v2gAnim); _v2gAnim = null; }
      return;
    }
    if (empty) empty.style.display = 'none';
    var sig = nodes.length + ':' + (links ? links.length : 0) + ':' + (nodes[0] ? nodes[0].id : '');
    if (sig !== _v2gSig) {
      _v2gSig = sig;
      var ns = nodes.slice(0, _v2gCap);
      _v2gIds = {};
      _v2gPts = ns.map(function(nd, i) {
        _v2gIds[nd.id] = i;
        var ang = i * 2.39996, r = 20 + (i % 6) * 24;
        var bc = nd.beliefCount || 1;
        var fullName = String(nd.name || nd.id);
        var isUser = String(nd.id || '').toLowerCase() === 'user';
        // Place the user slightly toward center for visual hierarchy
        var rad = isUser ? 8 : r;
        return {
          x: W / 2 + Math.cos(ang) * rad, y: H / 2 + Math.sin(ang) * rad,
          vx: 0, vy: 0, id: nd.id,
          label: isUser ? 'You' : fullName.slice(0, 22),
          fullLabel: isUser ? 'You (user)' : fullName,
          type: nd.type || 'entity',
          isUser: isUser,
          isHighStakes: !!nd.isHighStakes,
          r: (isUser ? _v2gNodeBaseR + 4 : (nd.isEpisode ? _v2gNodeBaseR - 1 : _v2gNodeBaseR)) + Math.min(8, Math.sqrt(bc) * 1.5),
          isVirtual: !!nd.isVirtual,
          isEpisode: !!nd.isEpisode,
        };
      });
      _v2gEdges = [];
      (links || []).forEach(function(l) {
        var ai = _v2gIds[l.source];
        var bi = _v2gIds[l.target];
        if (ai !== undefined && bi !== undefined) {
          _v2gEdges.push({ a: ai, b: bi, label: String(l.label || '').slice(0, 18), objectText: String(l.object_text || ''), type: l.type || 'set', confidence: l.confidence || 0.5, superseded: !!l.superseded });
        }
      });
      _v2gView = { scale: 1, ox: 0, oy: 0 }; _v2gSelectedId = null; _v2gAlpha = 1;
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
    ctx.font = _v2gFont(10);
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e]; var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var ax = _v2gSX(A.x), ay = _v2gSY(A.y), bx = _v2gSX(B.x), by = _v2gSY(B.y);
      var hot = _v2gSelectedId && (A.id === _v2gSelectedId || B.id === _v2gSelectedId);
      var beliefHot = _v2gHighlightSubj && _v2gHighlightObj &&
        ((A.id === _v2gHighlightSubj && B.id === _v2gHighlightObj) ||
         (A.id === _v2gHighlightObj && B.id === _v2gHighlightSubj));
      var pcolor = _V2G_PRED_COLORS[ed.type] || theme.accent;
      var touchesUser = _v2gIsUser(A) || _v2gIsUser(B);
      ctx.lineCap = 'round';
      var pathHot = !!ed.pathHot || (_v2gPathIds[A.id] && _v2gPathIds[B.id]);
      if (beliefHot || pathHot) {
        ctx.strokeStyle = theme.accentLight; ctx.lineWidth = pathHot ? 3.0 : 3.4;
        ctx.shadowColor = _v2gHexAlpha(theme.accent, 0.55); ctx.shadowBlur = 10;
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
      // Body — radial highlight for depth
      var body = ctx.createRadialGradient(x - r * 0.3, y - r * 0.35, 0, x, y, r);
      body.addColorStop(0, _v2gHexAlpha('#ffffff', isUser ? 0.45 : 0.28));
      body.addColorStop(0.35, color);
      body.addColorStop(1, _v2gHexAlpha(color, 0.85));
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = body;
      ctx.globalAlpha = p.isVirtual && !isSel && !isHover ? 0.72 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (p.isVirtual) {
        ctx.setLineDash([3, 2]);
        ctx.strokeStyle = _v2gHexAlpha(theme.accentLight, 0.55);
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
        // Canvas font: weight + size/family
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
        _v2gDrag = { idx: idx, wx: _v2gWX(c.sx) - p.x, wy: _v2gWY(c.sy) - p.y };
        _v2gSelectedId = p.id; _v2gInspect(p); canvas.setPointerCapture(ev.pointerId);
        canvas.style.cursor = 'grabbing';
        // Clear belief-click highlight when selecting a node directly
        _v2gHighlightSubj = null; _v2gHighlightObj = null;
      } else {
        _v2gDrag = { pan: true, sx: c.sx, sy: c.sy, ox: _v2gView.ox, oy: _v2gView.oy };
        canvas.style.cursor = 'grabbing';
        // Clear selection + belief highlight on empty-space click
        _v2gSelectedId = null; _v2gHighlightSubj = null; _v2gHighlightObj = null;
      }
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
          p.x = _v2gWX(c.sx) - _v2gDrag.wx; p.y = _v2gWY(c.sy) - _v2gDrag.wy;
        }
        _v2gRepaint();
      } else {
        var idx = _v2gHit(c.sx, c.sy);
        if (idx !== _v2gHover) { _v2gHover = idx; _v2gRepaint(); }
        canvas.style.cursor = idx >= 0 ? 'pointer' : 'grab';
        var tip = document.getElementById('v2g-tooltip');
        if (idx >= 0 && tip) {
          var p = _v2gPts[idx];
          var tc = _v2gNodeColor(p);
          var tLabel = _v2gIsUser(p) ? 'You' : _v2gTitle(p.fullLabel || p.label);
          tip.innerHTML = '<b style="color:' + tc + ';word-break:break-word;">' + _v2gEsc(tLabel) + '</b><br><span style="color:var(--text-muted);">' +
            (_v2gIsUser(p) ? 'you · center of memory' : ('type: ' + p.type)) +
            (p.isHighStakes ? ' · ⚠ high-stakes' : '') +
            (p.isVirtual ? ' · fact' : '') + '</span>';
          tip.style.display = 'block';
          tip.style.borderColor = _v2gHexAlpha(tc, 0.35);
          var rect = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (tip) { tip.style.display = 'none'; }
      }
    });
    canvas.addEventListener('pointerup', function(ev) {
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
      if (idx < 0) return; var p = _v2gPts[idx]; _v2gSelectedId = p.id; _v2gInspect(p);
      var rect = canvas.getBoundingClientRect();
      _v2gView.scale = 2.5; _v2gView.ox = rect.width / 2 - p.x * 2.5; _v2gView.oy = rect.height / 2 - p.y * 2.5;
      _v2gHeated(); _v2gRepaint();
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
    _v2gRefreshPalette();
    var color = _v2gNodeColor(p);
    var fullName = p.fullLabel || p.label || p.id;
    var title = _v2gIsUser(p) ? 'You' : _v2gTitle(fullName);
    var html = '<div style="color:' + color + ';font-weight:700;font-size:0.82rem;margin-bottom:4px;word-break:break-word;">' + _v2gEsc(title) + '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.68rem;margin-bottom:8px;">';
    html += _v2gIsUser(p) ? 'you · memory hub' : ('type: ' + p.type);
    if (p.isHighStakes) html += ' · <span style="color:#ef4444;">⚠ high-stakes</span>';
    if (p.isVirtual) html += ' · fact node';
    html += '</div>';
    // Contents — the full text of this belief/entity, shown exactly once.
    if (fullName !== title) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Contents</div>';
      html += '<div style="background:rgba(255,255,255,0.03);border:1px solid var(--border-subtle);border-radius:6px;padding:6px 8px;font-size:0.72rem;color:var(--text-secondary);word-break:break-word;max-height:200px;overflow-y:auto;margin-bottom:8px;">' + _v2gContents(fullName) + '</div>';
    }
    // List readable belief sentences touching this node
    var rels = [];
    for (var i = 0; i < _v2gEdges.length; i++) {
      var ed = _v2gEdges[i];
      var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var pcolor = _V2G_PRED_COLORS[ed.type] || _v2gTheme().accent;
      var predLabel = _v2gEsc(ed.label.replace(/_/g, ' '));
      // Neighbor labels only — never repeat THIS node's own text in the row.
      var targetName = _v2gEsc(_v2gIsUser(B) ? 'You' : _v2gShortLabel(B.fullLabel || B.label || B.id || ed.objectText));
      var sourceName = _v2gEsc(_v2gIsUser(A) ? 'You' : _v2gShortLabel(A.fullLabel || A.label || A.id || ed.objectText));
      if (A.id === p.id) {
        rels.push('<span style="color:' + pcolor + ';font-size:0.6rem;padding:1px 4px;border-radius:3px;background:' + _v2gHexAlpha(pcolor, 0.15) + ';">' + ed.type + '</span> ' + predLabel + ' <b style="word-break:break-word;">' + targetName + '</b>' + (ed.superseded ? ' <span style="color:var(--text-muted);font-size:0.58rem;">(superseded)</span>' : ''));
      } else if (B.id === p.id) {
        rels.push('<span style="color:var(--text-muted);font-size:0.68rem;">←</span> ' + sourceName + ' ' + predLabel + ' <b>(this)</b>');
      }
    }
    if (rels.length) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">Beliefs (' + rels.length + ')</div>';
      html += '<div style="display:flex;flex-direction:column;gap:3px;max-height:280px;overflow-y:auto;">' + rels.map(function(r) { return '<div style="color:var(--text-secondary);line-height:1.35;font-size:0.72rem;word-break:break-word;padding:2px 0;">' + r + '</div>'; }).join('') + '</div>';
    } else {
      html += '<div style="color:var(--text-muted);font-size:0.7rem;">No direct beliefs — this entity may be referenced indirectly.</div>';
    }
    el.innerHTML = html;
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

  async function _v2gLoad() {
    try {
      var resp = await fetch(_v2gBuildUrl());
      var data = await resp.json();
      var stats = data.stats || {};
      _v2gLastStats = stats;
      _v2gRawNodes = data.nodes || [];
      _v2gRawLinks = data.links || [];
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
      _v2gRenderFilters();
      _v2gApplyFilters();
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
    // Keep only nodes referenced by surviving links (+ search match on node names)
    var nodeIds = new Set();
    links.forEach(function(l) { nodeIds.add(l.source); nodeIds.add(l.target); });
    if (search) {
      nodes.forEach(function(n) {
        var nm = (n.name || n.label || n.id || '').toLowerCase();
        if (nm.indexOf(search) >= 0) nodeIds.add(n.id);
      });
    }
    // Filter nodes by entity type + membership
    nodes = nodes.filter(function(n) {
      if (activeEnt.length && activeEnt.indexOf(n.type) < 0) return false;
      // Keep nodes that are in a surviving link, OR all nodes if no link filter
      if (activePred.length || search) return nodeIds.has(n.id);
      return true;
    });
    // If entity-type filter is on, re-filter links to only those between surviving nodes
    if (activeEnt.length) {
      var keepIds = new Set(nodes.map(function(n) { return n.id; }));
      links = links.filter(function(l) { return keepIds.has(l.source) && keepIds.has(l.target); });
    }

    var sl = document.getElementById('v2g-stats-line');
    if (sl) {
      var st = _v2gLastStats || {};
      var paint = st.paint_source || st.source || 'sqlite';
      var gprov = st.graph_provider || paint;
      var parts = [nodes.length + ' nodes · ' + links.length + ' beliefs'];
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
    // Invalidate the layout signature so drawCanvas re-layouts on every
    // filter change/reset, not just when node count differs.
    _v2gSig = '';
    _v2gDrawCanvas(nodes, links);
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
      var cnt = (count != null && count > 0) ? ' <span style="opacity:0.65;font-family:var(--font-mono);">(' + count + ')</span>' : '';
      return '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:0.75rem;' + (active ? 'color:var(--text-primary);' : 'color:var(--text-muted);') + '">' +
             '<span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:' + (active ? color : 'transparent') + ';border:1px solid ' + color + ';"></span>' +
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
        return '<span data-fg="' + c.group + '" data-fk="' + c.key + '" style="font-size:0.62rem;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,0.15);color:#a5b4fc;cursor:pointer;">' + c.label + ' ✕</span>';
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
        '" stroke="#22d3ee" stroke-opacity="0.45" stroke-width="1.5"/>');
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

  try {
    _v2gRenderFilters();
    _v2gWireControls();
    _v2gLoad();
    setInterval(_v2gLoad, 30000);
  } catch (e) { /* V2 graph optional */ }
})();
