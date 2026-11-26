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
  if (typeof window.__kazmaMemConsoleCleanup === 'function') {
    try { window.__kazmaMemConsoleCleanup(); } catch (e0) { /* ignore */ }
  }
  var _memTimers = [];
  var _memWin = [];
  function _memEvery(ms, fn) {
    var id = setInterval(fn, ms);
    _memTimers.push(id);
    return id;
  }
  function _memOnWin(ev, fn) {
    window.addEventListener(ev, fn);
    _memWin.push([ev, fn]);
  }
  window.__kazmaMemConsoleCleanup = function () {
    _memTimers.forEach(clearInterval);
    _memTimers = [];
    _memWin.forEach(function (pair) {
      try { window.removeEventListener(pair[0], pair[1]); } catch (e1) { /* ignore */ }
    });
    _memWin = [];
  };
  // Leaving the page runs whatever cleanup is current (the graph below adds
  // to it). Added to the leave hooks from the start, so a failure further
  // down cannot leave the timers running; it used to overwrite the hooks.
  window.kazmaAddLeaveHook(function () {
    if (typeof window.__kazmaMemConsoleCleanup === 'function') window.__kazmaMemConsoleCleanup();
  });
  // Read when used, not when this script loads: a sidebar visit runs the
  // page's script files before its inline ones, so __DASH_MEM_I18N did not
  // exist yet and the console showed "0 undefined" until a reload.
  var I18N = (typeof Proxy === 'function')
    ? new Proxy({}, { get: function (_t, k) { return (window.__DASH_MEM_I18N || window.I18N || {})[k]; } })
    : (window.__DASH_MEM_I18N || window.I18N || {});
  // Text this console builds: the catalog's text for *key* in the page's
  // language, else *fallback* (English), {name} placeholders from *vars*.
  function _mt(key, fallback, vars) {
    if (typeof window.kazmaT === 'function') return window.kazmaT(key, fallback, vars);
    var text = fallback;
    if (vars) for (var k in vars) text = text.split('{' + k + '}').join(String(vars[k]));
    return text;
  }
  // When a memory was made, in the page's language.
  function _memWhen(epochSeconds) {
    return window.KazmaFormat ? window.KazmaFormat.dateTime(epochSeconds) : new Date(epochSeconds * 1000).toLocaleString();
  }
  function _memDay(epochSeconds) {
    return window.KazmaFormat ? window.KazmaFormat.date(epochSeconds) : new Date(epochSeconds * 1000).toLocaleDateString();
  }
  // A server error code, worded in the page's language (Memory audit M32).
  var _ERR = {
    not_found: ['memory.err.not_found', 'Not found: it may have been changed or removed meanwhile'],
    protected: ['memory.err.protected', 'This entity is protected'],
    not_open: ['memory.err.not_open', 'This was already decided'],
    replaced_since: ['memory.err.replaced_since', 'This fact has changed again since; undo the newer change first'],
    forgotten: ['memory.err.forgotten', 'The earlier value was forgotten, so it cannot come back'],
    not_written: ['memory.err.not_written', 'The value could not be saved'],
    not_yours: ['memory.err.not_yours', "This is another member's; only they or the space's owner can forget it"],
    not_theirs: ['memory.err.not_theirs', "This is another member's fact; only they or the space's owner decides on it"],
    identity_unavailable: ['memory.err.identity_unavailable', 'Who may decide this could not be read right now. Nothing changed; try again'],
    about_too_long: ['memory.err.about_too_long', 'About me would pass its limit: shorten it in Settings first'],
    no_account: ['memory.err.no_account', 'Sign in to add to your own About me'],
  };
  function _errText(key, fallback, d) {
    var code = d && (d.code || d.error);
    if (code && _ERR[code]) return _mt(_ERR[code][0], _ERR[code][1]);
    return (d && (d.error || d.detail)) || _mt(key, fallback);
  }
  // A value from the data shown as a label (an entity type, a predicate
  // type): the catalog's name for it, else the value itself, marked as
  // content (translate="no") -- it is the data's own word.
  function _mtData(prefix, value) {
    var v = String(value == null ? '' : value);
    var text = typeof window.tOr === 'function' ? window.tOr(prefix + v.toLowerCase(), '') : '';
    return text ? { text: text, attr: '' } : { text: v, attr: ' translate="no"' };
  }
  // _mt as HTML: the catalog text escaped, each var already HTML.
  function _mtHtml(key, fallback, htmlVars) {
    var text = _esc(typeof window.tOr === 'function' ? window.tOr(key, fallback) : fallback);
    if (htmlVars) for (var k in htmlVars) text = text.split('{' + k + '}').join(String(htmlVars[k]));
    return text;
  }
  // Visible build stamp — if missing in browser console, JS is stale/cached
  window.__KAZMA_MEMORY_CONSOLE_BUILD = 'comp-collapse-2026-08-04';
  // Template icons ([data-icon]) are filled on DOM ready only, and a sidebar
  // visit swaps this page in afterwards: fill the page's own (the stat
  // tiles' wells were empty squares). Filling twice is a no-op.
  try {
    if (window.KazmaIcons && typeof window.KazmaIcons.hydrate === 'function') {
      window.KazmaIcons.hydrate(document.querySelector('.memory-page') || document);
    }
  } catch (eIcons) { /* icons are decoration */ }
  // Memory & Governance Polling
  const memoryBadge = document.getElementById('memory-status-badge');
  const memoryDesc = document.getElementById('memory-status-desc');
  const installBtn = document.getElementById('install-ml-btn');

  function formatBytes(bytes, decimals = 2) {
    if (bytes === null || bytes === undefined || bytes === 0) return '0 ' + _mt('memory.console.bytes', 'Bytes');
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = [_mt('memory.console.bytes', 'Bytes'), 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  // A component's status as one of the shared tones (badge-<tone>,
  // tone-<tone>): the stylesheet holds both themes' colours.
  function _memTone(status) {
    if (status === 'ok') return 'success';
    if (status === 'warn') return 'warning';
    if (status === 'off') return 'neutral';
    return 'danger';
  }

  // Swap an element's tone-<x> class (a stat tile) for *tone*.
  function _memSetTone(el, tone) {
    if (!el) return;
    ['accent', 'success', 'warning', 'danger', 'info', 'neutral'].forEach(function(t) {
      el.classList.toggle('tone-' + t, t === tone);
    });
  }
  // Swap a badge's badge-<x> class for *tone*.
  function _memSetBadge(el, tone) {
    if (!el) return;
    ['accent', 'success', 'warning', 'danger', 'info', 'neutral', 'secondary'].forEach(function(t) {
      el.classList.toggle('badge-' + t, t === tone);
    });
    el.classList.add('badge');
  }

  function _memCardHtml(c) {
    const tone = _memTone(c.status || (c.ok ? 'ok' : 'error'));
    const statusLabel = (c.status || (c.ok ? 'ok' : 'error')).toUpperCase();
    const detail = (c.detail || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const locName = (I18N.compNames && c.id && I18N.compNames[c.id]) || c.name || c.id || 'component';
    const name = String(locName).replace(/</g, '&lt;');
    return (
      // min-width:0 + overflow-wrap so long details (e.g. Postgres DSNs in
      // the ConfigStore card) wrap INSIDE the card instead of widening the
      // grid track and drifting past the border.
      '<div class="card-nested" style="min-width:0;overflow-wrap:break-word;padding:10px 12px;">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px;min-width:0;">' +
          '<span style="min-width:0;overflow-wrap:break-word;font-size:0.78rem;font-weight:600;color:var(--text-primary);">' + name + '</span>' +
          '<span class="badge badge-' + tone + ' mem-pill" style="flex-shrink:0;font-size:0.65rem;letter-spacing:0.04em;">' +
            '<span class="mem-dot"></span>' + statusLabel +
          '</span>' +
        '</div>' +
        '<div translate="no" style="font-size:0.7rem;line-height:1.4;color:var(--text-secondary);overflow-wrap:break-word;">' + detail + '</div>' +
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
      return (
        '<span' + (f.detail ? ' translate="no"' : '') + ' title="' + _attr(f.detail || ch.label) + '" class="badge badge-' + _memTone(st) + ' mem-pill" style="font-size:0.68rem;">' +
          '<span class="mem-dot" style="width:6px;height:6px;"></span><span translate="yes">' + ch.label + '</span>' +
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
    set('kpi-vector-size', data.v2 ? _mt('memory.console.superseded_n', '{n} superseded', { n: supersededBeliefs }) : '–');
    
    // Episodes card: show episodic count, subtitle shows working
    set('kpi-fts-count', data.v2 ? String(episodicCount) : '–');
    set('kpi-fts-size', data.v2 ? _mt('memory.console.working_n', '{n} working', { n: workingCount }) : '–');
    
    // Entities card: show total count
    set('kpi-graph-nodes', data.v2 ? String(entitiesTotal) : '0');
    set('kpi-graph-edges', data.v2 ? _mt('memory.console.total_entities', 'total entities') : '–');
    
    var comps = Array.isArray(data.components) ? data.components : [];
    var okN = comps.filter(function(c) { return c.status === 'ok'; }).length;
    set('kpi-health-summary', comps.length ? (okN + '/' + comps.length + ' ' + okLbl) : (data.summary || '–'));
    // The health tile takes the worst component's tone.
    var healthTile = document.getElementById('kpi-health-tile');
    if (healthTile) {
      var worst = !comps.length ? 'neutral'
        : comps.some(function(c) { return c.status === 'error' || (!c.status && !c.ok); }) ? 'danger'
        : comps.some(function(c) { return c.status === 'warn'; }) ? 'warning' : 'success';
      _memSetTone(healthTile, worst);
    }
    set('graph-size-metric', data.v2 ? window.kazmaCount('memory.console.beliefs_n', activeBeliefs) : '–');
    set('graph-count-metric', window.kazmaCount('memory.console.entities_n', data.v2 ? entitiesTotal : 0));
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
    var parts = [_mt('memory.console.group_ok', '{ok}/{n} OK', { ok: okN, n: cards.length })];
    if (warnN) parts.push(window.kazmaCount('memory.console.group_warn', warnN));
    if (errN) parts.push(window.kazmaCount('memory.console.group_err', errN));
    if (offN) parts.push(_mt('memory.console.group_off', '{n} off', { n: offN }));
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
    if (hasErr) return 'var(--danger)';
    if (hasWarn) return 'var(--warning)';
    if (hasOff) return 'var(--text-muted)';
    return 'var(--success)';
  }

  function _memCompSyncToggleAllLabel() {
    var btn = document.getElementById('memory-components-toggle-all');
    var grid = document.getElementById('memory-components-grid');
    if (!btn || !grid) return;
    var panels = grid.querySelectorAll('.mem-comp-group');
    if (!panels.length) {
      btn.textContent = _mt('memory.console.expand_all', 'Expand all');
      return;
    }
    var openN = 0;
    panels.forEach(function(p) {
      if (p.getAttribute('data-open') === '1') openN++;
    });
    // If any open → offer collapse all; if all closed → expand all
    btn.textContent = openN > 0
      ? _mt('memory.console.collapse_all', 'Collapse all')
      : _mt('memory.console.expand_all', 'Expand all');
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
    var headBtn = groupEl.querySelector('.mem-comp-group-head');
    if (headBtn) headBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
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
      '<div class="card-nested mem-comp-group" data-group-key="' + key.replace(/"/g, '') + '" data-open="' + (open ? '1' : '0') + '" ' +
        'style="padding:0;overflow:hidden;">' +
        '<button type="button" class="mem-comp-group-head" aria-expanded="' + (open ? 'true' : 'false') + '" ' +
          'style="width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border:none;background:transparent;cursor:pointer;text-align:start;color:inherit;font:inherit;">' +
          '<span style="display:inline-flex;align-items:center;gap:8px;min-width:0;">' +
            '<span class="mem-comp-chevron" style="font-size:0.75rem;color:var(--text-muted);width:0.9rem;flex-shrink:0;">' + (open ? '▾' : '▸') + '</span>' +
            '<span style="font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-secondary);">' + String(title).replace(/</g, '&lt;') + '</span>' +
          '</span>' +
          '<span style="display:inline-flex;align-items:center;gap:6px;flex-shrink:0;font-size:0.68rem;font-family:var(--font-mono);color:var(--text-muted);">' +
            '<span class="mem-dot" style="--mem-c:' + dot + ';"></span>' +
            statusLine +
          '</span>' +
        '</button>' +
        '<div class="mem-comp-group-body" style="display:' + (open ? 'grid' : 'none') + ';grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;padding:0 12px 12px;">' +
          cards.map(_memCardHtml).join('') +
        '</div>' +
      '</div>'
    );
  }

  function _memEsc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // A page of this site only: "/settings?tab=memory", never "//host" or an
  // address elsewhere.
  function _memSiteLink(link) {
    const s = String(link || '');
    return s.charAt(0) === '/' && s.charAt(1) !== '/' && s.charAt(1) !== '\\' ? s : '';
  }

  // One problem: what is wrong, what to do, and the page where it is done.
  // The server's sentences are content (translate="no"); the labels are the
  // page's own words.
  function _memProblemHtml(p) {
    const problem = (p && typeof p === 'object') ? p : { detail: p };
    const link = _memSiteLink(problem.link);
    let html = '<li style="margin-bottom:6px;"><span translate="no">' + _memEsc(problem.detail) + '</span>';
    if (problem.fix) {
      html += '<div class="mem-problem-fix" style="margin-top:2px;"><strong>' +
        _memEsc(_mt('memory.console.problem_fix', 'What to do:')) + '</strong> <span translate="no">' +
        _memEsc(problem.fix) + '</span>';
      if (link) {
        html += ' <a class="btn btn-sm btn-secondary" href="' + _memEsc(link) + '">' +
          _memEsc(_mt('memory.console.problem_open', 'Open')) + '</a>';
      }
      html += '</div>';
    }
    return html + '</li>';
  }

  function renderMemoryComponents(data) {
    const grid = document.getElementById('memory-components-grid');
    const summaryEl = document.getElementById('memory-health-summary');
    const issuesEl = document.getElementById('memory-issues-list');
    if (!grid) return;

    const components = Array.isArray(data.components) ? data.components : [];
    if (summaryEl) {
      summaryEl.textContent = components.length
        ? window.kazmaCount('memory.console.components_healthy', components.length, { ok: components.filter(c => c.status === 'ok').length })
        : '';
    }

    renderMemoryPipeline(data);
    renderMemoryKpis(data);

    if (!components.length) {
      grid.innerHTML = '<div class="card-nested mem-muted" style="padding:10px 12px;font-size:0.75rem;">' + (I18N.noComponentData || 'No component data yet.') + '</div>';
    } else {
      var groups = [
        { title: I18N.groupFeatures || 'Features', ids: ['memory_enabled', 'per_turn_retrieval', 'auto_store', 'consolidation', 'kb_merge'] },
        { title: I18N.groupLayers || 'V2 cognitive stack', ids: ['embedder', 'vector_memory', 'layer_l1', 'layer_l2', 'memory_findability', 'layer_l3', 'layer_l4', 'graph_neo4j'] },
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
        html += _memGroupHtml(_mt('memory.console.group_other', 'Other'), rest);
      }
      grid.innerHTML = html;
      _memCompWireGroups(grid);
    }

    // Each problem says what is wrong, what to do and where (``problems``);
    // an older server sends the sentences alone (``issues``).
    const problems = Array.isArray(data.problems) && data.problems.length
      ? data.problems
      : (Array.isArray(data.issues) ? data.issues : []).map(function(i) { return { detail: i }; });
    if (issuesEl) {
      if (problems.length) {
        issuesEl.style.display = 'block';
        issuesEl.innerHTML =
          '<div style="font-weight:700;margin-bottom:4px;">' + (I18N.issuesHeading || 'Needs attention') + '</div>' +
          '<ul style="margin:0;padding-inline-start:1.1rem;">' +
          problems.map(_memProblemHtml).join('') +
          '</ul>';
      } else {
        issuesEl.style.display = 'none';
        issuesEl.innerHTML = '';
      }
    }
  }

  // The status line at the top of the page says what a person needs (on, a
  // problem, unknown); the engine's own wording goes to Health and
  // maintenance (#memory-status-detail).
  const memoryDetail = document.getElementById('memory-status-detail');
  let _memStatusRead = false;

  async function pollMemoryStatus() {
    try {
      // null while the server restarts (a proxy's HTML error page): keep what
      // is shown and let the next tick try again -- but never leave the first
      // "Checking..." standing as if all were well.
      const data = await window.kazmaGetJson('/api/system/status');
      if (!data) {
        if (!_memStatusRead) {
          memoryBadge.textContent = _mt('memory.page.status_unknown', 'Unknown');
          _memSetBadge(memoryBadge, 'warning');
          memoryDesc.removeAttribute('translate');
          memoryDesc.textContent = _mt('memory.page.status_unknown_desc', 'Kazma could not read memory\'s status. It tries again in a few seconds.');
        }
        return;
      }
      _memStatusRead = true;
      const status = data.status || 'ACTIVE';

      if (status === 'ACTIVE') {
        memoryBadge.textContent = I18N.memoryActive;
        _memSetBadge(memoryBadge, 'success');
        memoryDesc.removeAttribute('translate');
        memoryDesc.textContent = I18N.memoryDescActive;
        if (memoryDetail) {
          const comps = Array.isArray(data.components) ? data.components : [];
          const backend = data.backend || {};
          const engineOk = comps.some(c => c.id === 'vector_memory' && c.status === 'ok');
          const okN = comps.filter(c => c.status === 'ok').length;
          memoryDetail.textContent = data.headline
            ? _mt('memory.console.headline',
              'Persistence: {config} (config/sessions/swarm); checkpoints: {checkpoints}. {engine}.', {
                config: backend.config || 'sqlite',
                checkpoints: backend.checkpoints || 'sqlite',
                engine: engineOk
                  ? _mt('memory.console.engine_ok', 'V2 cognitive engine operational')
                  : _mt('memory.console.engine_degraded', 'V2 memory degraded or offline'),
              }) + (comps.length ? ' · ' + window.kazmaCount('memory.console.components_healthy', comps.length, { ok: okN }) : '')
            : (data.summary || '–');
        }
        installBtn.style.display = 'none';
        installBtn.disabled = false;
        installBtn.textContent = I18N.installMl;
      } else if (status === 'DEMO') {
        memoryBadge.textContent = _mt('memory.console.demo_badge', 'DEMO');
        _memSetBadge(memoryBadge, 'accent');
        memoryDesc.removeAttribute('translate');
        memoryDesc.textContent = _mt('memory.console.demo_desc', 'Demo mode — RAG memory is disabled. The full version includes ChromaDB vector search and sentence-transformers.');
        installBtn.style.display = 'none';
      } else if (status === 'INSTALLING') {
        memoryBadge.textContent = I18N.memoryInstalling;
        _memSetBadge(memoryBadge, 'warning');
        memoryDesc.removeAttribute('translate');
        memoryDesc.textContent = I18N.memoryDescInstalling;
        installBtn.style.display = 'inline-block';
        installBtn.disabled = true;
        installBtn.textContent = I18N.installing;
      } else {
        memoryBadge.textContent = I18N.memoryDegraded;
        _memSetBadge(memoryBadge, 'danger');
        memoryDesc.removeAttribute('translate');
        memoryDesc.textContent = I18N.memoryDescDegraded;
        // The first live issue is the server's diagnostic, shown as written
        // in Health and maintenance.
        const firstIssue = data.issues && data.issues[0];
        if (memoryDetail) memoryDetail.textContent = firstIssue || '–';
        installBtn.style.display = 'inline-block';
        installBtn.disabled = false;
        installBtn.textContent = I18N.installMl;
      }

      renderMemoryComponents(data);
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
        await window.kazmaSave('/api/system/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ extra: 'rag' })
        });
      } catch (e) {
        window.kazmaAlert({ title: _mt('memory.console.install_failed_title', 'Install failed'), message: I18N.installFailed, variant: 'btn-danger' });
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
      // A refused read lands in the catch's "failed", never in "no backups"
      // (tests/test_empty_states_after_load.py).
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const backups = data.backups || [];

      if (backups.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--text-muted);">${_esc(I18N.noBackups)}</td></tr>`;
        return;
      }

      // Only the memory database's backups are listed (the route leaves
      // out the operations database's copies, which Restore refuses): when
      // it was made, its size, its file (2026-11-18 review: every row said
      // "None (0 docs)" from fields the route never sent).
      tbody.innerHTML = '';
      backups.forEach(backup => {
        const created = window.KazmaFormat ? window.KazmaFormat.dateTime(backup.timestamp) : new Date(backup.timestamp * 1000).toLocaleString();
        const tr = document.createElement('tr');

        const tdCreated = document.createElement('td');
        tdCreated.style.padding = '10px 12px';
        tdCreated.textContent = created;

        const tdSize = document.createElement('td');
        tdSize.style.padding = '10px 12px';
        tdSize.textContent = formatBytes(backup.size || 0);

        const tdName = document.createElement('td');
        tdName.style.padding = '10px 12px';
        tdName.style.color = 'var(--text-muted)';
        tdName.setAttribute('translate', 'no');
        tdName.textContent = backup.name;

        const tdAction = document.createElement('td');
        tdAction.style.padding = '10px 12px';
        tdAction.style.textAlign = 'end';
        const btn = document.createElement('button');
        btn.dataset.name = backup.name;
        btn.dataset.when = created;
        btn.className = 'btn btn-xs btn-secondary restore-btn';
        btn.textContent = I18N.restore;
        tdAction.appendChild(btn);

        tr.appendChild(tdCreated);
        tr.appendChild(tdSize);
        tr.appendChild(tdName);
        tr.appendChild(tdAction);
        tbody.appendChild(tr);
      });

      // Bind restore button action
      document.querySelectorAll('.restore-btn').forEach(btn => {
        btn.addEventListener('click', async function() {
          const name = this.getAttribute('data-name');
          const when = this.getAttribute('data-when') || name;
          if (await window.kazmaConfirm({ title: _mt('memory.console.restore_title', 'Restore backup'), message: _mt('memory.page.restore_confirm', 'Bring memory back to the copy made {when}?', { when: when }), confirmText: _mt('memory.console.restore', 'Restore'), danger: true })) {
            this.disabled = true;
            this.textContent = I18N.restoring;
            try {
              const r = await fetch('/api/system/memory/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backup_name: name })
              });
              const res = await r.json().catch(() => ({}));
              if (r.ok && res.status === 'success') {
                window.kazmaAlert({ title: _mt('memory.console.restore_complete', 'Restore complete'), message: I18N.restoreSuccess });
                pollMemoryStatus();
                loadBackups();
              } else {
                const detail = res.detail === 'not_a_memory_backup'
                  ? _mt('memory.page.restore_not_memory', 'That file is not a backup of memory, so nothing was restored.')
                  : _mt('memory.console.restoration_error', 'Restoration error: {error}', { error: res.detail || ('HTTP ' + r.status) });
                window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: detail, variant: 'btn-danger' });
              }
            } catch (e) {
              window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: _mt('memory.console.restoration_failed', 'Restoration failed: {error}', { error: e }), variant: 'btn-danger' });
            } finally {
              this.disabled = false;
              this.textContent = I18N.restore;
            }
          }
        });
      });

    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:var(--danger);">${_esc(_mt('memory.console.backups_load_failed', 'Failed to load backups.'))}</td></tr>`;
    }
  }

  toggleBtn.addEventListener('click', () => {
    const opening = deck.style.display === 'none';
    deck.style.display = opening ? 'block' : 'none';
    toggleBtn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    arrow.innerHTML = (window.KazmaIcons ? KazmaIcons.get(opening ? 'chevron-down' : 'chevron-right') : '');
    const label = toggleBtn.lastChild;
    if (label && label.nodeType === 3) {
      label.textContent = ' ' + (opening ? _mt('memory.page.backups_hide', 'Hide backups') : _mt('memory.page.backups_show', 'Show backups'));
    }
    if (opening) loadBackups();
  });

  // Action Buttons Triggers
  const backupBtn = document.getElementById('trigger-backup-btn');
  const backupSpinner = document.getElementById('backup-spinner');

  backupBtn.addEventListener('click', async () => {
    backupBtn.disabled = true;
    backupSpinner.style.display = 'inline-block';
    try {
      const resp = await fetch('/api/system/memory/backup', { method: 'POST' });
      const res = await resp.json().catch(() => ({}));
      if (resp.ok && res.status === 'success') {
        // The files the backup wrote (it answered "undefined" here before).
        const files = ((res.manifest && res.manifest.files) || []).join(', ');
        window.kazmaAlert({ title: _mt('memory.console.backup_complete', 'Backup complete'), message: _mt('memory.page.backup_made', 'Backed up: {files}', { files: files }) });
        loadBackups();
        pollMemoryStatus();
      } else {
        const why = res.detail === 'nothing_backed_up'
          ? _mt('memory.page.backup_nothing', 'Nothing was backed up.')
          : I18N.backupFailed + ': ' + (res.detail || ('HTTP ' + resp.status));
        window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: why, variant: 'btn-danger' });
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: I18N.backupFailed + ': ' + e, variant: 'btn-danger' });
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
      const res = await resp.json().catch(() => ({}));
      if (resp.ok && res.status === 'success') {
        window.kazmaAlert({ title: _mt('memory.console.optimize_complete', 'Optimization complete'), message: _mt('memory.page.optimize_done', 'Both databases were compacted.') });
        pollMemoryStatus();
      } else if (res.status === 'failed' && res.details) {
        // Each database's own outcome: one that failed is said, never
        // "completed successfully" (it was, whatever happened).
        const lines = Object.keys(res.details).map((k) => '• ' + res.details[k]);
        window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: _mt('memory.page.optimize_partial', 'Not every database could be compacted:') + '\n' + lines.join('\n'), variant: 'btn-danger' });
      } else {
        window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: I18N.optimizeFailed + ': ' + (res.detail || ('HTTP ' + resp.status)), variant: 'btn-danger' });
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: I18N.optimizeFailed + ': ' + e, variant: 'btn-danger' });
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
        confirmText: _mt('memory.console.clean_up', 'Clean up'),
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
          if (s.deleted != null) {
            parts.push(window.kazmaCount('memory.console.snapshots_deleted', s.deleted,
              { days: window.kazmaCount('common.ui.days', s.retention_days || 0) }));
          }
          if (s.reclaimed != null) parts.push(_mt('memory.console.snapshots_reclaimed', '{size} reclaimed', { size: formatBytes(s.reclaimed) }));
          if (s.prune && s.prune !== 'ok') parts.push(_mt('memory.console.snapshots_prune', 'prune: {state}', { state: s.prune }));
          if (s.vacuum && s.vacuum !== 'ok') parts.push(_mt('memory.console.snapshots_vacuum', 'vacuum: {state}', { state: s.vacuum }));
          window.kazmaAlert({
            title: _mt('memory.console.snapshots_done_title', 'Snapshot cleanup complete'),
            message: (parts.length ? parts.join('\n') : _mt('memory.console.snapshots_nothing', 'Nothing to clean — no snapshots outside the retention window.')) +
              (res.auto_maintain
                ? _mt('memory.console.auto_maintain_on', '\n\nAuto-maintenance is ON (daily).')
                : _mt('memory.console.auto_maintain_off', '\n\nAuto-maintenance is OFF — run manually here.')),
          });
        } else {
          window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: _mt('memory.console.cleanup_failed', 'Cleanup failed: {error}', { error: res.detail || _mt('memory.console.unknown_error', 'unknown error') }), variant: 'btn-danger' });
        }
      } catch (e) {
        window.kazmaAlert({ title: _mt('memory.console.error', 'Error'), message: _mt('memory.console.cleanup_failed', 'Cleanup failed: {error}', { error: e }), variant: 'btn-danger' });
      } finally {
        snapshotsBtn.disabled = false;
        snapshotsSpinner.style.display = 'none';
      }
    });
  }

  // Poll immediately and then every 5 seconds
  pollMemoryStatus();
  // Skip the poll tick while the tab is hidden (long-lived interval — audit).
  _memEvery(5000, function() { if (!document.hidden) pollMemoryStatus(); });

  // ── V2 Cognitive Engine panel ─────────────────────────────────
  function _v2ChipStyle(status) {
    const map = {
      ACTIVE:      { tone: 'success', label:_mt('memory.console.chip_active', 'Active') },
      DUAL_WRITE:  { tone: 'info', label:_mt('memory.console.chip_dual_write', 'Dual-write') },
      DEGRADED:    { tone: 'warning', label:_mt('memory.console.chip_degraded', 'Degraded') },
      OFF:         { tone: 'neutral', label:_mt('memory.console.chip_off', 'Off') },
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
        badge.textContent = chip.label + (h.use_new_stack ? ' · ' + _mt('memory.console.chip_live', 'LIVE') : '');
        _memSetBadge(badge, chip.tone);
      }
      const desc = document.getElementById('v2-status-desc');
      if (desc) {
        if (!h.db_available) {
          desc.textContent = _mt('memory.console.v2_no_db', 'V2 database not initialized yet. Beliefs will populate after the first turn.');
        } else if (h.use_new_stack) {
          desc.textContent = _mt('memory.console.v2_live_desc', 'V2 is the active read path (use_new_stack=true). Recall serves bi-temporal beliefs + tiered episodes.');
        } else {
          desc.textContent = _mt('memory.console.v2_off_desc', 'V2 is off (use_new_stack=false). Recall injection and post-turn consolidation are paused. There is no legacy reader to fall back to.');
        }
      }
      // KPIs
      const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
      setEl('v2-kpi-beliefs-active', fmtNum(h.beliefs.active));
      setEl('v2-kpi-beliefs-meta', _mt('memory.console.beliefs_meta', '{superseded} superseded · {archived} archived', { superseded: h.beliefs.superseded || 0, archived: h.beliefs.archived || 0 }));
      setEl('v2-kpi-episodes-recall', fmtNum(h.episodes.recall));
      setEl('v2-kpi-episodes-meta', _mt('memory.console.episodes_meta', '{episodic} episodic · {archived} archived', { episodic: h.episodes.episodic || 0, archived: h.episodes.archived || 0 }));
      setEl('v2-kpi-entities', fmtNum(h.entities));
      setEl('v2-kpi-procedural-meta', _mt('memory.console.procedural_meta', '{active} active · {quarantined} quarantined', { active: h.procedural_dags.active || 0, quarantined: h.procedural_dags.quarantine || 0 }));
      // A tenant's view (scope "tenant") counts that tenant's memories and
      // leaves out the install's own state: queue, post-turn, reconsolidation.
      const installView = h.scope !== 'tenant';
      const q = h.queue || null;
      setEl('v2-kpi-queue', q ? fmtNum((q.pending||0) + (q.processing||0)) : '–');
      setEl('v2-kpi-queue-meta', q
        ? window.kazmaCount('memory.console.queue_meta', h.recent_audits || 0, { failed: q.failed || 0 })
        : _mt('memory.console.queue_admins_only', 'install queue: admins only'));
      // Dead letters are a failure (the banner below says so too); work
      // waiting is information.
      _memSetTone(document.getElementById('v2-kpi-queue-tile'),
        q && q.failed ? 'danger' : (q && ((q.pending || 0) + (q.processing || 0)) ? 'info' : 'neutral'));
      // Post-turn / embedder strip
      const pt = h.post_turn || {};
      const okEl = document.getElementById('v2-post-turn-ok');
      if (okEl) {
        okEl.textContent = !installView
          ? _mt('memory.console.post_turn_none', 'post-turn: –')
          : _mt('memory.console.post_turn_ok', 'ok: {ok} · fail m/e/d/q: {m}/{e}/{d}/{q}', {
            ok: pt.ok || 0, m: pt.mirror_fail || 0, e: pt.extract_fail || 0,
            d: pt.deep_pass_failed || 0, q: pt.enqueue_fail || 0,
          });
        okEl.title = _mt('memory.console.post_turn_ok_title',
          "Failed post-turn steps: m = memory write, e = fact extraction, d = the model's deep pass (retried later), q = queue");
      }
      const errEl = document.getElementById('v2-post-turn-err');
      if (errEl) {
        const le = pt.last_error || h.last_error;
        errEl.textContent = !installView ? '' : (le
          ? _mt('memory.console.last_error', 'last error: {error}', { error: String(le).slice(0, 80) })
          : _mt('memory.console.last_error_none', 'last error: none'));
        errEl.style.color = le ? 'var(--danger)' : 'var(--text-muted)';
      }
      const embEl = document.getElementById('v2-embedder-ready');
      if (embEl) {
        embEl.textContent = h.embedder_ready
          ? _mt('memory.console.embedder_ready', 'embedder: ready')
          : (h.embedder_state === 'not_loaded'
            ? _mt('memory.console.embedder_not_loaded', 'embedder: loads at the first search')
            : _mt('memory.console.embedder_unavailable', 'embedder: unavailable'));
      }
      const rc = document.getElementById('v2-reconsol-meta');
      if (rc) {
        const lr = h.last_reconsolidation;
        if (!installView) {
          rc.textContent = _mt('memory.console.reconsol_none', 'reconsol: –');
        } else if (lr && lr.finished_at) {
          rc.textContent = _mt('memory.console.reconsol_done', 'reconsol: merged {merged} · emb {embedded}', {
            merged: lr.duplicate_beliefs_merged || 0,
            embedded: (lr.episodes_embedded || 0) + (lr.beliefs_embedded || 0),
          });
        } else {
          rc.textContent = _mt('memory.console.reconsol_never', 'reconsol: never');
        }
      }
      const gEl = document.getElementById('v2-graph-backend');
      if (gEl) {
        const g = h.graph || {};
        const prov = g.provider || 'sqlite';
        if (prov === 'neo4j') {
          const on = g.online
            ? _mt('memory.console.graph_online', 'online')
            : _mt('memory.console.graph_offline', 'offline→sqlite fallback');
          gEl.textContent = _mt('memory.console.graph_neo4j', 'graph: neo4j dual-write · {state} · paint sqlite', { state: on });
          gEl.style.color = g.online ? 'var(--success)' : 'var(--warning)';
        } else {
          gEl.textContent = _mt('memory.console.graph_sqlite', 'graph: sqlite');
          gEl.style.color = 'var(--text-muted)';
        }
      }
      const bmEl = document.getElementById('v2-backends-mode');
      if (bmEl) {
        bmEl.textContent = _mt('memory.console.backends', 'backends: {mode}', { mode: h.backends_mode || 'local' });
        const vc = h.vector_capability || {};
        if (vc.provider || vc.status) {
          bmEl.textContent += _mt('memory.console.backends_vector', ' · vector {provider}', { provider: vc.provider || vc.status || '' });
        }
      }
      const strip = document.getElementById('v2-post-turn-strip');
      const failedQ = (h.queue && h.queue.failed) || 0;
      const hasErr = !!(pt.last_error || h.last_error || h.status_detail);
      if (strip && (failedQ > 0 || hasErr)) {
        strip.classList.add('is-alert');
      }
      // Sticky alert banner (P0-2)
      const banner = document.getElementById('v2-alert-banner');
      const bannerText = document.getElementById('v2-alert-banner-text');
      if (banner) {
        if (failedQ > 0 || hasErr || h.status === 'DEGRADED') {
          banner.style.display = 'block';
          const parts = [];
          if (failedQ > 0) parts.push(window.kazmaCount('memory.console.banner_failed', failedQ));
          if (pt.last_error || h.last_error) parts.push(_mt('memory.console.banner_last_error', 'last post-turn error recorded'));
          if (h.status === 'DEGRADED') parts.push(_mt('memory.console.banner_degraded', 'status DEGRADED'));
          if (bannerText) bannerText.textContent = ' ' + (parts.join(' · ') || _mt('memory.console.banner_check', 'Check queue and post-turn strip.'));
        } else {
          banner.style.display = 'none';
        }
      }
      // Tier breakdown bars
      const tb = document.getElementById('v2-tier-breakdown');
      if (tb) {
        // Each tier is a category colour (--cat-*), the archived one muted.
        const tiers = [
          ['working', 'Working', 'Work', h.episodes.working, 'var(--cat-1)'],
          ['episodic', 'Episodic', 'Epis', h.episodes.episodic, 'var(--cat-6)'],
          ['recall', 'Recall', 'Reca', h.episodes.recall, 'var(--cat-2)'],
          ['archived', 'Archived', 'Arch', h.episodes.archived, 'var(--text-muted)'],
        ];
        const total = tiers.reduce((s,t) => s + (t[3]||0), 0) || 1;
        tb.innerHTML = tiers.map(([id, full, short, count, color]) => {
          const pct = Math.round(((count||0) / total) * 100);
          const name = _mt('memory.console.tier_' + id, full);
          const tip = _mt('memory.console.tier_title', '{name}: {count} ({pct}%)', { name: name, count: count || 0, pct: pct });
          return '<span title="' + _attr(tip) + '" class="mem-chip mem-chip-plain">' +
                 '<span class="mem-dot" style="--mem-c:' + color + ';"></span>' +
                 '<span style="color:var(--text-muted);">' + _esc(_mt('memory.console.tier_short_' + id, short)) + '</span>' +
                 '<span style="font-family:var(--font-mono);color:var(--text-primary);">' + (count||0) + '</span>' +
                 '</span>';
        }).join('');
      }
      const pb = document.getElementById('v2-procedural-breakdown');
      if (pb) {
        pb.textContent = _mt('memory.console.procedural_breakdown', '{active} active skills · {quarantined} quarantined', { active: h.procedural_dags.active || 0, quarantined: h.procedural_dags.quarantine || 0 });
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
  // A Forget button for a memory or a weekly summary a search found.
  function _hitForget(kind, id) {
    if (!id || (kind !== 'episode' && kind !== 'summary')) return '';
    return ' <button type="button" class="btn btn-xs btn-outline v2-hit-forget" data-kind="' + kind + '" data-id="' + _attr(id) +
      '">' + _esc(_mt('memory.console.forget', 'Forget')) + '</button>';
  }
  function _wireHitForgets(out) {
    out.querySelectorAll('.v2-hit-forget').forEach(function(btn) {
      btn.addEventListener('click', async function() {
        var id = btn.getAttribute('data-id');
        if (btn.getAttribute('data-kind') === 'summary') await _forgetSummary(id);
        else await _forgetEpisode(id);
      });
    });
  }
  async function runV2Probe() {
    const input = document.getElementById('v2-probe-input');
    const out = document.getElementById('v2-probe-results');
    if (!input || !out) return;
    const q = (input.value || '').trim();
    if (!q) { out.textContent = _mt('memory.console.enter_query', 'Enter a query.'); return; }
    out.textContent = _mt('memory.console.probing', 'Probing…');
    try {
      const resp = await fetch('/api/memory/v2/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: 5 }),
      });
      const data = await resp.json();
      if (!data.ok) { out.textContent = _errText('memory.console.probe_failed', 'Probe failed', data); return; }
      _v2gCollectSeedsFromHits([].concat(data.beliefs || [], data.episodes || []));
      // also seed from query words
      q.split(/\s+/).forEach(function(w) { if (w.length > 2) _v2gLastQuerySeeds.push(w); });
      const lines = [];
      function _srcChips(h) {
        var arr = (h.sources && h.sources.length) ? h.sources : (h.source ? [h.source] : []);
        if (!arr.length) return '';
        // Each retrieval channel is a category colour (--cat-*).
        return arr.map(function(s) {
          var color = 'var(--text-muted)';
          var key = String(s || '').toLowerCase();
          if (key.indexOf('ppr') >= 0 || key.indexOf('belief_ppr') >= 0) color = 'var(--cat-4)';
          else if (key.indexOf('dense') >= 0) color = 'var(--cat-1)';
          else if (key.indexOf('fts') >= 0 || key.indexOf('belief') >= 0) color = 'var(--cat-6)';
          else if (key.indexOf('session') >= 0) color = 'var(--cat-2)';
          return '<span translate="no" class="mem-chip" style="--mem-c:' + color + ';margin-inline-end:3px;font-size:0.62rem;padding:1px 6px;">' + _esc(s) + '</span>';
        }).join('');
      }
      (data.beliefs || []).forEach(function(h) {
        lines.push('<div class="mem-hit" style="--mem-c:var(--cat-6);"><span class="mem-hit-kind">' + _esc(_mt('memory.console.hit_belief', 'BELIEF')) + '</span> ' +
          '<span translate="no">' + _esc(h.content || '') + '</span>' +
          '<div style="margin-top:3px;">' + _srcChips(h) +
          ' <span class="mem-muted" style="font-size:0.68rem;">' +
          _esc(_mt('memory.console.score', 'score {n}', { n: h.score != null ? Number(h.score).toFixed(3) : '' })) + '</span></div></div>');
      });
      (data.episodes || []).forEach(function(h) {
        lines.push('<div class="mem-hit" style="--mem-c:var(--cat-1);"><span class="mem-hit-kind">' + _esc(_mt('memory.console.hit_episode', 'EPISODE')) + '</span> ' +
          '<span translate="no" dir="auto">' + _esc(h.content || '') + '</span>' + _hitForget('episode', h.id) +
          _wholeMemoryFold(h.id, Number(h.more || 0)) +
          '<div style="margin-top:3px;">' + _srcChips(h) + '</div></div>');
      });
      // Weekly topic summaries, as the model is shown them (Memory audit M32:
      // the panel dropped them).
      (data.weekly_summaries || []).forEach(function(h) {
        lines.push('<div class="mem-hit" style="--mem-c:var(--cat-2);"><span class="mem-hit-kind">' + _esc(_mt('memory.console.hit_summary', 'WEEKLY SUMMARY')) + '</span> ' +
          '<span translate="no">' + _esc((h.content || '').slice(0, 300)) + '</span>' + _hitForget('summary', h.id) + '</div>');
      });
      if (data.degraded) {
        lines.unshift(_memoryDegradedBanner(data.degraded));
      }
      // Beside the hits or "No hits.", never instead of them: this install's
      // own search was whole.
      const mirror = data.mirror_unread ? _mirrorUnreadBanner(!data.degraded) : '';
      if (!lines.length) {
        const hints = (data.hints || []).map(function(hh) {
          return '<div translate="no" class="mem-warn" style="margin-top:4px;">• ' + _esc(hh) + '</div>';
        }).join('');
        out.innerHTML = mirror + '<span class="mem-muted">' + _esc(_mt('memory.console.no_hits', 'No hits.')) + '</span>' + hints;
      } else {
        // After the degraded banner, as the federated search shows it.
        out.innerHTML = (data.degraded ? lines.shift() : '') + mirror + lines.join('') +
          '<div class="mem-muted" style="margin-top:6px;font-size:0.68rem;">' + _esc(_mt('memory.console.channels_hint', 'Channels: fts5 · dense · belief_ppr · session_boost (enable Explain recall in Settings → Memory)')) + '</div>' +
          '<button type="button" class="btn btn-sm btn-secondary" id="v2-probe-path-btn" style="margin-top:8px;">' + _esc(_mt('memory.console.show_path', 'Show path on graph →')) + '</button>' +
          '<button type="button" class="btn btn-sm btn-outline" id="v2-eval-golden-btn" style="margin-top:8px;margin-inline-start:6px;">' + _esc(_mt('memory.console.run_golden_eval', 'Quick recall check')) + '</button>';
        document.getElementById('v2-probe-path-btn')?.addEventListener('click', function() { _v2gApplyPathFromQuery(); });
        document.getElementById('v2-eval-golden-btn')?.addEventListener('click', runGoldenEval);
        _wireHitForgets(out);
        _bindWholeMemory(out);
      }
    } catch (e) {
      out.textContent = _mt('memory.console.probe_error', 'Probe error: {error}', { error: e });
    }
  }

  var _openBeliefId = null;
  async function openBeliefDrawer(beliefId) {
    _openBeliefId = beliefId;
    const drawer = document.getElementById('v2-belief-drawer');
    const body = document.getElementById('v2-belief-drawer-body');
    if (!drawer || !body) return;
    drawer.style.display = 'block';
    body.textContent = _mt('memory.console.loading', 'Loading…');
    try {
      const resp = await fetch('/api/memory/v2/beliefs/' + encodeURIComponent(beliefId));
      const data = await resp.json();
      if (!data.ok || !data.belief) {
        body.textContent = _errText('memory.console.not_found', 'Not found', data);
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
          const last = td.last_accessed ? _memWhen(td.last_accessed) : _mt('memory.console.never', 'never');
          const ep = td.origin && td.origin.episode;
          const originTxt = ep
            ? ' · ' + _esc(_mt('memory.console.from', 'from')) + ' <span translate="no" style="color:var(--text-secondary);">' + _esc(ep.preview) + '</span>'
            : (td.origin && td.origin.session ? ' · ' + _esc(_mt('memory.console.session', 'session')) + ' <span translate="no">' + _esc(td.origin.session) + '</span>' : '');
          trailHtml =
            '<div style="margin-top:6px;color:var(--text-muted);">' +
              _mt('memory.console.recall_trail', 'recalled {count} · last {last} · via {method}', {
                count: '<b style="color:var(--text-secondary);">' + (td.access_count || 0) + '×</b>',
                last: _esc(last),
                method: '<span translate="no">' + _esc(td.extraction_method || '?') + '</span>',
              }) +
              originTxt +
            '</div>';
        }
      } catch (_) { /* trail is optional */ }

      // "Probe from this belief" — seeds the probe box with the object text so
      // the operator can see what else this belief recalls alongside.
      const probeBtn = '<button type="button" id="v2-belief-probe" class="btn btn-xs btn-outline" style="margin-top:6px;">' + _esc(_mt('memory.console.probe_from_belief', 'Probe from this belief →')) + '</button>';

      body.innerHTML =
        '<div translate="no"><b>' + _esc(b.subject) + '</b> ' + _esc((b.predicate||'').replace(/_/g,' ')) +
        ' <b>' + _esc(b.object) + '</b></div>' +
        '<div style="color:var(--text-muted);margin-top:4px;">' +
        _esc(_mt('memory.console.belief_meta', 'Confidence {conf}% · {recalled}', {
          conf: Math.round((b.confidence || 0) * 100),
          recalled: window.kazmaCount('memory.page.recalled_n', b.access_count || 0),
        })) + '</div>' +
        trailHtml +
        (chain ? '<div style="margin-top:6px;color:var(--text-muted);">' + _mt('memory.console.supersedes_chain', 'supersedes chain: {chain}', { chain: '<span translate="no">' + chain + '</span>' }) + '</div>' : '') +
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
          // To the results it just asked for (it scrolled to the map).
          document.getElementById('v2-search-ops')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
      }
    } catch (e) {
      body.textContent = _mt('memory.console.load_failed', 'Load failed');
    }
  }
  document.getElementById('v2-belief-drawer-close')?.addEventListener('click', function() {
    const d = document.getElementById('v2-belief-drawer');
    if (d) d.style.display = 'none';
  });
  document.getElementById('v2-belief-invalidate')?.addEventListener('click', async function() {
    if (!_openBeliefId) return;
    const ok = window.kazmaConfirm
      ? await window.kazmaConfirm({
        title: _mt('memory.console.unlink_title', 'Unlink belief?'),
        message: _mt('memory.console.unlink_message', 'Soft-invalidate this edge from active memory.'),
        confirmText: _mt('memory.page.remove', 'Remove'),
        danger: true,
      })
      : await window.confirm(_mt('memory.console.unlink_native', 'Unlink (invalidate) belief?'));
    if (!ok) return;
    try {
      await window.kazmaSave('/api/memory/v2/beliefs/' + encodeURIComponent(_openBeliefId) + '/invalidate', { method: 'POST' });
    } catch (e) {
      // Keep the drawer open: the belief is still active.
      window.kazmaAlert({ title: _mt('memory.console.unlink_failed', 'Unlink failed'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
      return;
    }
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
      if (resp.status === 403) { el.textContent = _mt('memory.console.queue_admins', "The task queue is the install's: admins only."); return; }
      const data = await resp.json();
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const tasks = data.tasks || [];
      if (!tasks.length) { el.textContent = _mt('memory.console.queue_empty', 'Queue empty.'); return; }
      // A task's status is an interface word, in the page's language; it was
      // the queue's own English word in every language (2026-10-02). A status
      // the page does not know is shown as data.
      const QUEUE_STATUS = {
        pending: ['memory.console.queue_pending', 'pending'],
        processing: ['memory.console.queue_processing', 'running'],
        completed: ['memory.console.queue_completed', 'completed'],
        failed: ['memory.console.queue_failed', 'failed'],
      };
      el.innerHTML = tasks.map(function(t) {
        const st = t.status || '';
        const label = QUEUE_STATUS[st];
        const stHtml = label ? _esc(_mt(label[0], label[1])) : '<span translate="no">' + _esc(st) + '</span>';
        const retry = st === 'failed'
          ? ' <button type="button" data-retry="' + _attr(t.id) + '" class="btn btn-xs btn-outline v2-queue-retry">' + _esc(_mt('memory.console.retry', 'retry')) + '</button>'
          : '';
        return '<div class="mem-row" style="padding:3px 0;">' +
          '<span translate="no">' + _esc(t.task_type) + '</span> · ' + stHtml + ' · ' +
          _esc(window.kazmaCount('memory.console.attempts', t.attempts || 0)) + retry + '</div>';
      }).join('');
      el.querySelectorAll('.v2-queue-retry').forEach(function(btn) {
        btn.addEventListener('click', async function() {
          const id = btn.getAttribute('data-retry');
          try {
            await window.kazmaSave('/api/memory/v2/queue/' + encodeURIComponent(id) + '/retry', { method: 'POST' });
          } catch (e) {
            window.kazmaAlert({ title: _mt('memory.console.retry_failed', 'Retry failed'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
          }
          loadV2Queue();
        });
      });
    } catch (e) { el.textContent = _mt('memory.console.queue_load_failed', 'Queue load failed'); }
  }
  document.getElementById('v2-queue-clear-failed')?.addEventListener('click', async function() {
    const ok = window.kazmaConfirm
      ? await window.kazmaConfirm({
        title: _mt('memory.console.clear_failed_title', 'Clear failed tasks?'),
        message: _mt('memory.console.clear_failed_message', 'Permanently delete dead-letter queue rows.'),
      })
      : await window.confirm(_mt('memory.console.clear_failed_native', 'Clear all failed queue tasks?'));
    if (!ok) return;
    try {
      const r = await fetch('/api/memory/v2/queue/clear-failed', { method: 'POST' });
      const d = await r.json();
      if (window.showToast) window.showToast(d.ok ? window.kazmaCount('memory.console.cleared_failed', d.deleted || 0) : (d.error || d.detail || _mt('memory.console.failed', 'Failed')), d.ok ? 'success' : 'error');
      loadV2Queue();
      pollV2Health();
    } catch (e) {
      if (window.showToast) window.showToast(_mt('memory.console.failed', 'Failed'), 'error');
    }
  });

  async function loadV2Merges() {
    const el = document.getElementById('v2-merges-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/entity-merges?limit=20');
      const data = await resp.json();
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const merges = data.merges || [];
      if (!merges.length) { el.textContent = _mt('memory.console.no_pending_merges', 'No pending merges.'); return; }
      el.innerHTML = merges.map(function(m) {
        return '<div class="mem-row" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
          '<span translate="no">' + _esc(m.source_entity_id) + ' → ' + _esc(m.target_entity_id) +
          ' <span class="mem-muted">(' + _esc(m.merge_tier) + ' · ' +
          (m.confidence != null ? Number(m.confidence).toFixed(2) : '') + ')</span></span>' +
          '<button type="button" data-mid="' + _attr(m.id) + '" data-act="approve" class="v2-merge-act btn btn-xs btn-success" aria-label="' + _attr(_mt('memory.console.approve', 'Approve')) + '">' + KazmaIcons.span('check') + '</button>' +
          '<button type="button" data-mid="' + _attr(m.id) + '" data-act="reject" class="v2-merge-act btn btn-xs btn-outline" aria-label="' + _attr(_mt('memory.console.reject', 'Reject')) + '">' + KazmaIcons.span('x') + '</button>' +
          '</div>';
      }).join('');
      el.querySelectorAll('.v2-merge-act').forEach(function(btn) {
        btn.addEventListener('click', async function() {
          const id = btn.getAttribute('data-mid');
          const act = btn.getAttribute('data-act');
          try {
            await window.kazmaSave('/api/memory/v2/entity-merges/' + encodeURIComponent(id), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ action: act }),
            });
          } catch (e) {
            window.kazmaAlert({ title: _mt('memory.console.merge_action_failed', 'Merge action failed'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
          }
          loadV2Merges();
        });
      });
    } catch (e) { el.textContent = _mt('memory.console.merges_load_failed', 'Merges load failed'); }
  }

  // A search that failed or ran without meaning search says so -- never
  // "no hits" (audit 2026-10-26 AUD-026/AUD-057). Recall reports a failure
  // as `degraded: "recall_failed:<code>"`.
  function _memoryDegradedBanner(degraded) {
    const failed = String(degraded || '').indexOf('recall_failed:') === 0;
    const text = failed
      ? _mt('memory.console.memory_search_failed', 'Memory search failed: this is not an empty result.')
      : _mt('memory.console.probe_degraded', 'Meaning search was unavailable: keyword matches only.');
    // A failed search is a failure (danger); keyword-only is a warning.
    return '<div class="notice tone-' + (failed ? 'danger' : 'warning') + '" style="display:block;margin-bottom:6px;font-size:0.72rem;">' + _esc(text) + '</div>';
  }
  // Other installs' memories unread (the Postgres mirror away): a calm
  // note, never "keyword matches only" (recall-strict review M1). "All
  // searched" only when meaning search did not fall short too (delta Low 1).
  function _mirrorUnreadBanner(whole) {
    let text = _mt('memory.console.mirror_unread', 'Memories from other Kazma installs could not be read just now.');
    if (whole) text += ' ' + _mt('memory.console.mirror_unread_whole', "This install's own memories were all searched.");
    return '<div class="notice tone-info" style="display:block;margin-bottom:6px;font-size:0.72rem;">' +
      _esc(text) + '</div>';
  }
  function _knowledgeFailedBanner() {
    return '<div class="notice tone-danger" style="display:block;margin-bottom:6px;font-size:0.72rem;">' +
      _esc(_mt('memory.console.knowledge_search_failed', 'Knowledge search failed: this is not an empty result.')) + '</div>';
  }

  async function runFederatedSearch() {
    const input = document.getElementById('v2-probe-input');
    const out = document.getElementById('v2-probe-results');
    const sum = document.getElementById('v2-fed-summary');
    if (!input || !out) return;
    const q = (input.value || '').trim();
    if (!q) { out.textContent = _mt('memory.console.enter_query', 'Enter a query.'); return; }
    out.textContent = _mt('memory.console.searching', 'Searching memory + knowledge…');
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
      if (!data.ok) { out.textContent = _errText('memory.console.search_failed', 'Search failed', data); return; }
      const s = data.summary || {};
      if (sum) {
        sum.textContent = _mt('memory.console.fed_summary',
          'memory: {memory} · knowledge: {knowledge} · total: {total}  (stores stay separate — labels only)',
          { memory: s.memory || 0, knowledge: s.knowledge || 0, total: s.total || 0 });
      }
      const lines = (data.hits || []).map(function(h) {
        const store = h.store === 'knowledge' ? 'KB' : 'MEM';
        // Which store a hit came from is a category, not a status.
        const color = h.store === 'knowledge' ? 'var(--cat-2)' : 'var(--cat-6)';
        const kind = (h.kind || '').toUpperCase();
        const prov = h.provenance || {};
        const extra = h.store === 'knowledge'
          ? (prov.document_title || prov.source_url || prov.library_id || '')
          : ((h.sources && h.sources.join(',')) || h.source || '');
        return '<div class="mem-row" style="margin-bottom:6px;padding-bottom:6px;">' +
          '<span translate="no" class="mem-hit-kind" style="--mem-c:' + color + ';">' + store + '/' + _esc(kind) + '</span> ' +
          '<span translate="no">' + _esc((h.content || '').slice(0, 220)) + '</span>' +
          (extra ? ' <span translate="no" class="mem-muted" style="font-size:0.68rem;">[' + _esc(String(extra).slice(0, 80)) + ']</span>' : '') +
          (h.store === 'knowledge' ? '' : _hitForget(h.kind === 'weekly_summary' ? 'summary' : h.kind, h.id)) +
          '</div>';
      });
      _v2gCollectSeedsFromHits(data.hits || []);
      const qWords = (document.getElementById('v2-probe-input') || {}).value || '';
      qWords.split(/\s+/).forEach(function(w) { if (w.length > 2) _v2gLastQuerySeeds.push(w); });
      const banners = [];
      if (data.degraded) banners.push(_memoryDegradedBanner(data.degraded));
      if (data.knowledge_error) banners.push(_knowledgeFailedBanner());
      const mirror = data.mirror_unread ? _mirrorUnreadBanner(!data.degraded) : '';
      if (lines.length) {
        out.innerHTML = banners.join('') + mirror + lines.join('') +
          '<button type="button" class="btn btn-sm btn-secondary" id="v2-fed-path-btn" style="margin-top:8px;">' + _esc(_mt('memory.console.show_path', 'Show path on graph →')) + '</button>';
        document.getElementById('v2-fed-path-btn')?.addEventListener('click', function() { _v2gApplyPathFromQuery(); });
        _wireHitForgets(out);
      } else if (banners.length) {
        out.innerHTML = banners.join('') + mirror;
      } else {
        out.innerHTML = mirror + '<span class="mem-muted">' + _esc(_mt('memory.console.no_hits_either', 'No hits in either store.')) + '</span>';
      }
    } catch (e) {
      out.textContent = _mt('memory.console.fed_error', 'Federated search error: {error}', { error: e });
    }
  }
  async function runGoldenEval() {
    const out = document.getElementById('v2-probe-results');
    if (out) out.textContent = _mt('memory.console.running_eval', 'Running the quick recall check…');
    try {
      const resp = await fetch('/api/memory/v2/eval/golden', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ include_optional: false }),
      });
      const data = await resp.json();
      if (!out) return;
      if (!resp.ok) {
        // 403: the benchmark is the install's, admins only.
        out.textContent = data.detail || data.error || _mt('memory.console.eval_failed_http', 'Eval failed: HTTP {status}', { status: resp.status });
        return;
      }
      if (!data.ok && data.error && !data.total) {
        out.textContent = _errText('memory.console.eval_failed', 'Eval failed', data);
        return;
      }
      const lines = [
        '<div style="margin-bottom:6px;">' +
        _mt('memory.console.eval_summary', '<strong>Quick recall check</strong> — pass {passed}/{total} (rate {rate})', {
          passed: data.passed || 0,
          total: data.total || 0,
          rate: _esc((data.pass_rate != null) ? data.pass_rate : '—'),
        }) +
        (data.unrelated_per_case && window.kazmaCount
          ? ' · ' + _esc(window.kazmaCount('memory.console.eval_unrelated_n', data.unrelated_per_case)) : '') +
        (data.skipped ? ' · ' + _esc(_mt('memory.console.eval_skipped', 'skipped {n}', { n: data.skipped })) : '') + '</div>'
      ];
      (data.cases || []).forEach(function(c) {
        const color = c.status === 'pass' ? 'var(--success)' : (c.status === 'skipped' ? 'var(--text-muted)' : 'var(--danger)');
        lines.push('<div style="font-size:0.72rem;margin-bottom:2px;"><span style="color:' + color + ';font-weight:700;">' +
          _esc(_mt('memory.console.case_' + String(c.status || 'unknown').toLowerCase(), String(c.status || '?').toUpperCase())) + '</span> ' +
          '<span translate="no">' + _esc(c.id || '') + (c.query ? ' — ' + _esc(c.query) : '') + '</span>' +
          ((c.shown_wrongly && c.shown_wrongly.length)
            ? ' · ' + _esc(_mt('memory.console.eval_shown_wrongly', 'showed unrelated: {words}', { words: c.shown_wrongly.join(', ') }))
            : '') + '</div>');
      });
      out.innerHTML = lines.join('');
    } catch (e) {
      if (out) out.textContent = _mt('memory.console.eval_error', 'Eval error: {error}', { error: e });
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
    // It merges facts for everyone using this Kazma and cannot be undone:
    // asked first (it ran on one click before the 2026-11-18 review).
    const ok = await window.kazmaConfirm({
      title: _mt('memory.page.reconsolidate_confirm_title', 'Merge duplicate facts now?'),
      message: _mt('memory.page.reconsolidate_confirm', 'Facts stored twice are merged into one. This cannot be undone.'),
      confirmText: _mt('memory.page.reconsolidate_go', 'Merge now'),
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await fetch('/api/memory/v2/reconsolidate', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (window.showToast) window.showToast(d.ok ? _mt('memory.console.reconsol_enqueued', 'Reconsolidation enqueued') : (d.error || d.detail || _mt('memory.console.failed', 'Failed')), d.ok ? 'success' : 'error');
      loadV2Queue();
    } catch (e) {
      if (window.showToast) window.showToast(_mt('memory.console.failed', 'Failed'), 'error');
    }
  });
  async function loadV2Procedural() {
    const el = document.getElementById('v2-procedural-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/procedural?limit=15');
      const data = await resp.json();
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const dags = data.dags || [];
      if (!dags.length) { el.textContent = _mt('memory.console.no_active_skills', 'No active skills yet.'); return; }
      el.innerHTML = dags.map(function(d) {
        return '<div translate="no" class="mem-row" style="padding:3px 0;">' +
          '<b style="color:var(--text-primary);">' + _esc(d.name || d.id) + '</b> · C=' +
          (d.confidence != null ? Number(d.confidence).toFixed(2) : '?') +
          ' · n=' + (d.total_trials || 0) +
          '<div class="mem-muted">' + _esc((d.description || '').slice(0, 80)) + '</div></div>';
      }).join('');
    } catch (e) { el.textContent = _mt('memory.console.skills_load_failed', 'Skills load failed'); }
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
        const failing = (data.checks || []).filter(function(c) { return !c.ok; }).map(function(c) { return c.name; });
        detEl.innerHTML = _esc(window.kazmaCount('memory.console.quality_checks', data.total || 0, { passed: data.passed || 0 })) + ' · ' +
          (failing.length ? '<span translate="no">' + _esc(failing.join(', ')) + '</span>' : _esc(_mt('memory.console.all_green', 'all green')));
      }
    } catch (e) { if (scoreEl) scoreEl.textContent = '–'; }
  }
  document.getElementById('v2-procedural-refresh')?.addEventListener('click', loadV2Procedural);

  // What Kazma remembers of conversations, each with Forget (plan U1). A
  // forgotten memory keeps nothing it said, and nothing brings it back.
  // Every memory reachable from the page (Memory audit M23: it listed the
  // newest 30 and an older one could not be forgotten): a page at a time,
  // searchable, with where the page is in the whole.
  var _MEM_PAGE = 30;
  var _memOffset = 0;
  var _memQuery = '';
  function _pageRange(id, offset, shown, total) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = total ? _mt('memory.console.page_range', '{from}–{to} of {total}', {
      from: (window.KazmaFormat ? window.KazmaFormat.number(offset + 1) : offset + 1),
      to: (window.KazmaFormat ? window.KazmaFormat.number(offset + shown) : offset + shown),
      total: (window.KazmaFormat ? window.KazmaFormat.number(total) : total),
    }) : '';
  }
  function _pagerButtons(prefix, offset, shown, total) {
    var newer = document.getElementById(prefix + '-newer');
    var older = document.getElementById(prefix + '-older');
    if (newer) newer.disabled = offset <= 0;
    if (older) older.disabled = offset + shown >= total;
  }
  // Forget one memory: asked first, then said how it went (Memory audit M23:
  // the list's own button, a search result's and a probe hit's).
  async function _forgetEpisode(id) {
    const ok = await window.kazmaConfirm({
      title: _mt('memory.console.forget_memory_title', 'Forget this memory?'),
      message: _mt('memory.console.forget_memory_message', 'Kazma stops remembering this part of the conversation and the facts it learned from it, wherever a copy was kept. The chat itself stays as it is. Backups made before now keep it until they are replaced (up to a year for offsite backups).'),
      confirmText: _mt('memory.console.forget', 'Forget'),
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await window.kazmaSave('/api/memory/v2/episodes/' + encodeURIComponent(id) + '/forget', { method: 'POST' });
      if (window.showToast) {
        const forgotText = !r || !r.ok
          ? (_errText('memory.console.forget_failed', 'Forget failed', r))
          : (r.remote_vector_deleted === false
            // Forgotten here; the remote index's copy is retried each
            // maintenance pass -- partial, said so (Memory audit M32).
            ? _mt('memory.console.forgotten_remote_pending', 'Forgotten. The copy in the remote vector index is removed at the next maintenance pass.')
            : (!r.facts_forgotten
              ? _mt('memory.console.forgotten', 'Forgotten')
              : window.kazmaCount('memory.console.forgotten_with_facts', r.facts_forgotten)));
        window.showToast(forgotText, r && r.ok ? 'success' : 'error');
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.forget_failed', 'Forget failed'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
    }
    loadV2Memories();
    pollV2Health();
  }
  // A memory is kept whole and listed bounded (2026-11-19, memory/limits.py):
  // a folded "The whole memory" reads the rest, a page at a time, from
  // GET /api/memory/v2/episodes/{id}/text. Loaded when first opened.
  function _wholeMemoryFold(id, more) {
    if (!id || !(more > 0)) return '';
    return '<details class="v2-whole" data-id="' + _attr(id) + '" style="margin-top:4px;">' +
      '<summary class="mem-muted" style="cursor:pointer;font-size:0.72rem;">' +
      _esc(_mt('memory.console.whole_memory', 'The whole memory')) + ' · ' +
      // The number as the page writes numbers (18,770; Latin digits in
      // Arabic too), its plural form chosen by the number itself.
      _esc(window.kazmaCount('memory.console.more_chars', more,
        { n: window.KazmaFormat ? window.KazmaFormat.number(more) : String(more) })) + '</summary>' +
      '<div class="v2-whole-text mem-well" translate="no" dir="auto" style="white-space:pre-wrap;overflow-wrap:anywhere;' +
      'max-height:360px;overflow-y:auto;padding:6px 8px;font-size:0.78rem;margin-top:4px;"></div>' +
      '<div class="mem-muted v2-whole-range" style="font-size:0.68rem;margin-top:2px;"></div>' +
      '<button type="button" class="btn btn-xs btn-secondary v2-whole-next" hidden style="margin-top:4px;">' +
      _esc(_mt('memory.console.whole_next', 'Show the next part')) + '</button></details>';
  }

  function _bindWholeMemory(root) {
    if (!root) return;
    root.querySelectorAll('details.v2-whole').forEach(function(box) {
      var next = 0;
      var loading = false;
      async function readPage() {
        if (loading || next === null) return;
        loading = true;
        var text = box.querySelector('.v2-whole-text');
        var range = box.querySelector('.v2-whole-range');
        var btn = box.querySelector('.v2-whole-next');
        try {
          var resp = await fetch('/api/memory/v2/episodes/' + encodeURIComponent(box.getAttribute('data-id')) +
            '/text?offset=' + next);
          var data = await resp.json();
          if (!resp.ok || !data.ok) throw new Error('HTTP ' + resp.status);
          text.appendChild(document.createTextNode(data.text || ''));
          next = data.next_offset == null ? null : data.next_offset;
          var fmt = function(n) { return window.KazmaFormat ? window.KazmaFormat.number(n) : String(n); };
          range.textContent = _mt('memory.console.whole_range', 'Characters {start}-{end} of {total}',
            { start: fmt(data.total ? 1 : 0), end: fmt(data.end), total: fmt(data.total) });
          if (btn) btn.hidden = next === null;
        } catch (e) {
          range.textContent = _mt('memory.console.whole_failed', 'The memory could not be read. It is not gone.');
        } finally {
          loading = false;
        }
      }
      box.addEventListener('toggle', function() {
        if (box.open && next === 0 && !box.querySelector('.v2-whole-text').textContent) readPage();
      });
      var btn = box.querySelector('.v2-whole-next');
      if (btn) btn.addEventListener('click', readPage);
    });
  }

  // Each list draws the answer to what was asked last. A search of a large
  // memory answers after the shorter word typed next, and its rows (with their
  // Forget buttons) stood under a search box that says something else.
  let _memAsked = 0;
  let _beliefsAsked = 0;

  async function loadV2Memories() {
    const el = document.getElementById('v2-memories-list');
    if (!el) return;
    const asked = ++_memAsked;
    try {
      const url = '/api/memory/v2/episodes?limit=' + _MEM_PAGE + '&offset=' + _memOffset +
        (_memQuery ? '&q=' + encodeURIComponent(_memQuery) : '');
      const resp = await fetch(url);
      const data = await resp.json();
      if (asked !== _memAsked) return;
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const eps = data.episodes || [];
      const total = Number(data.total || 0);
      _pageRange('v2-memories-range', _memOffset, eps.length, total);
      _pagerButtons('v2-memories', _memOffset, eps.length, total);
      if (!eps.length) {
        el.textContent = _memQuery
          ? _mt('memory.console.no_memory_matches', 'No memory holds these words.')
          : _mt('memory.console.no_memories', 'No memories of conversations yet.');
        return;
      }
      el.innerHTML = eps.map(function(ep) {
        const when = ep.created_at ? _memWhen(ep.created_at) : '';
        // The id is isolated left to right: on an Arabic page its leading
        // digits joined the date's and the line read "29625 ... 2026/09/c31ba".
        const chat = ep.session_id ? ' · ' + _esc(_mt('memory.console.chat', 'chat')) + ' <bdi dir="ltr" translate="no">' + _esc(String(ep.session_id).slice(0, 12)) + '</bdi>' : '';
        return '<div class="mem-row" style="display:flex;gap:8px;align-items:flex-start;justify-content:space-between;">' +
          // The memory is its own line, laid out in its own language: inline in
          // the row it took the page's direction, so on an Arabic page an
          // English memory sat on the right (2026-10-02).
          '<span style="min-width:0;overflow-wrap:anywhere;"><span translate="no" style="display:block;color:var(--text-primary);">' + _esc(ep.preview || ep.id) + '</span>' +
          '<span class="mem-muted" style="display:block;font-size:0.65rem;"><bdi>' + _esc(when) + '</bdi>' + chat + '</span>' +
          _wholeMemoryFold(ep.id, Number(ep.more || 0)) + '</span>' +
          '<button type="button" class="btn btn-sm v2-memory-forget btn-outline" data-id="' + _attr(ep.id) + '" style="flex-shrink:0;">' + _esc(_mt('memory.console.forget', 'Forget')) + '</button></div>';
      }).join('');
      el.querySelectorAll('.v2-memory-forget').forEach(function(btn) {
        btn.addEventListener('click', function() { _forgetEpisode(btn.getAttribute('data-id')); });
      });
      _bindWholeMemory(el);
    } catch (e) {
      if (asked === _memAsked) el.textContent = _mt('memory.console.memories_load_failed', 'Memories load failed');
    }
  }
  document.getElementById('v2-memories-refresh')?.addEventListener('click', loadV2Memories);
  document.getElementById('v2-memories-newer')?.addEventListener('click', function() {
    _memOffset = Math.max(0, _memOffset - _MEM_PAGE); loadV2Memories();
  });
  document.getElementById('v2-memories-older')?.addEventListener('click', function() {
    _memOffset += _MEM_PAGE; loadV2Memories();
  });
  (function() {
    var box = document.getElementById('v2-memories-search');
    var timer = null;
    if (box) box.addEventListener('input', function() {
      clearTimeout(timer);
      timer = setTimeout(function() { _memQuery = (box.value || '').trim(); _memOffset = 0; loadV2Memories(); }, 300);
    });
  })();
  loadV2Memories();

  // Changes to facts the user stated (the audit's F1, memory/changes.py):
  // one Kazma made from the user's words, which the user can undo, and one
  // Kazma was not sure was the user's, which waits for their answer.
  var _CHANGE_DONE = {
    undo: ['memory.console.change_undone', 'Change undone'],
    confirm: ['memory.console.change_confirmed', 'Changed'],
    dismiss: ['memory.console.change_dismissed', 'Kept as it was'],
  };
  // Kazma's reading of a fact the user gave none of: never a question; the
  // user may say it is right, or not them.
  var _READING_DONE = {
    confirm: ['memory.console.change_reading_kept', 'Kept as yours'],
    dismiss: ['memory.console.change_reading_removed', 'Removed. Kazma will not file it again'],
  };
  async function _decideChange(id, decision, reading) {
    var done = (reading && _READING_DONE[decision]) || _CHANGE_DONE[decision];
    try {
      const r = await window.kazmaSave('/api/memory/v2/changes/' + encodeURIComponent(id) + '/' + decision, { method: 'POST' });
      if (window.showToast) {
        window.showToast(r && r.ok ? _mt(done[0], done[1]) : _errText('memory.console.change_failed', 'That change could not be made', r), r && r.ok ? 'success' : 'error');
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.change_failed', 'That change could not be made'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
    }
    loadV2Changes();
  }
  // One line of the interface with the data's value in it, isolated and
  // marked as content: the value keeps its own direction on either page.
  function _changeValue(value) {
    return { value: '<bdi translate="no">' + _esc(value) + '</bdi>' };
  }
  function _changeLine(html) {
    return '<span style="display:block;">' + html + '</span>';
  }
  async function loadV2Changes() {
    const el = document.getElementById('v2-changes-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/changes');
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error('HTTP ' + resp.status);
      const rows = data.changes || [];
      if (!rows.length) {
        el.textContent = _mt('memory.console.no_changes', 'Nothing has changed in the facts you told Kazma, and nothing is waiting for your answer.');
        return;
      }
      el.innerHTML = rows.map(function(c) {
        const reading = c.kind === 'reading';
        const pending = c.kind === 'pending' || reading;
        // An ending: the fact stopped being true, with no new value (the
        // memory framework audit's part C2, retractions).
        const ending = !reading && !c.new_value;
        const title = reading
          ? _mt('memory.console.change_reading_title', "Kazma's reading of your message")
          : pending
            ? (ending ? _mt('memory.console.change_pending_end_title', 'Has this ended? Kazma kept the fact')
                      : _mt('memory.console.change_pending_title', 'Is this a change? Kazma kept the earlier fact'))
            : (ending ? _mt('memory.console.change_ended_title', 'Ended from what you said')
                      : _mt('memory.console.change_update_title', 'Changed from what you said'));
        const lines = reading
          ? _changeLine(_mtHtml('memory.console.change_read', 'Kazma read: {value}', _changeValue(c.new_value)))
          : pending
            ? _changeLine(_mtHtml('memory.console.change_kept', 'Kept: {value}', _changeValue(c.old_value))) +
              (ending ? _changeLine(_esc(_mt('memory.console.change_heard_end', 'You may have said it is no longer true')))
                      : _changeLine(_mtHtml('memory.console.change_heard', 'You may have said: {value}', _changeValue(c.new_value))))
            : ending
              ? _changeLine(_mtHtml('memory.console.change_no_longer', 'No longer: {value}', _changeValue(c.old_value)))
              : _changeLine(_mtHtml('memory.console.change_was', 'Before: {value}', _changeValue(c.old_value))) +
                _changeLine(_mtHtml('memory.console.change_now', 'Now: {value}', _changeValue(c.new_value)));
        const readingAttr = reading ? ' data-reading="1"' : '';
        const yes = reading ? _mt('memory.console.change_reading_right', 'This is right')
          : ending ? _mt('memory.console.change_confirm_end', 'Yes, it ended')
            : _mt('memory.console.change_confirm', 'Yes, change it');
        const no = reading ? _mt('memory.console.change_reading_not_me', 'Not me') : _mt('memory.console.change_dismiss', 'No, keep it');
        const buttons = pending
          ? '<button type="button" class="btn btn-sm btn-primary v2-change-act" data-id="' + _attr(c.id) + '"' + readingAttr + ' data-decision="confirm">' + _esc(yes) + '</button>' +
            '<button type="button" class="btn btn-sm btn-outline v2-change-act" data-id="' + _attr(c.id) + '"' + readingAttr + ' data-decision="dismiss">' + _esc(no) + '</button>'
          : '<button type="button" class="btn btn-sm btn-outline v2-change-act" data-id="' + _attr(c.id) + '" data-decision="undo">' + _esc(_mt('memory.console.change_undo', 'Undo')) + '</button>';
        // The fact's name: the page's word for one of the user's own facts
        // (home, name, employer...), in the page's language; any other name
        // is the data's own word, its own block.
        const fact = _mtData('memory.console.fact_', c.predicate || '');
        const factText = fact.attr ? String(c.predicate || '').replace(/_/g, ' ') : fact.text;
        return '<div class="mem-row v2-change" data-kind="' + _attr(c.kind) + '" style="display:flex;gap:8px;align-items:flex-start;justify-content:space-between;padding:6px 0;">' +
          '<span style="min-width:0;overflow-wrap:anywhere;">' +
          '<span style="display:block;color:var(--text-primary);font-weight:600;">' + _esc(title) + '</span>' +
          '<span class="v2-change-fact"' + fact.attr + ' style="display:block;">' + _esc(factText) + '</span>' +
          lines +
          '<span class="mem-muted" style="display:block;font-size:0.65rem;"><bdi>' + _esc(c.created_at ? _memWhen(c.created_at) : '') + '</bdi></span>' +
          '</span>' +
          '<span style="display:inline-flex;gap:4px;flex-shrink:0;flex-wrap:wrap;">' + buttons + '</span></div>';
      }).join('');
      el.querySelectorAll('.v2-change-act').forEach(function(btn) {
        btn.addEventListener('click', function() {
          btn.disabled = true;
          _decideChange(btn.getAttribute('data-id'), btn.getAttribute('data-decision'), btn.getAttribute('data-reading') === '1');
        });
      });
    } catch (e) { el.textContent = _mt('memory.console.changes_load_failed', 'Changes load failed'); }
  }
  document.getElementById('v2-changes-refresh')?.addEventListener('click', loadV2Changes);
  loadV2Changes();

  // Lines Kazma suggests for About me, from facts the user stated
  // (memory/profile_suggestions.py, the memory framework audit's F13,
  // decision 10): nothing joins About me without the user's yes. The line
  // is the user's own data, in the language they said it in.
  async function _decideSuggestion(id, decision) {
    try {
      const r = await window.kazmaSave('/api/memory/v2/profile/suggestions/' + encodeURIComponent(id) + '/' + decision, { method: 'POST' });
      if (window.showToast) {
        const done = decision === 'add'
          ? _mt('memory.console.suggestion_added', 'Added to About me')
          : _mt('memory.console.suggestion_dismissed', 'Dismissed. Kazma will not suggest it again');
        window.showToast(r && r.ok ? done : _errText('memory.console.suggestion_failed', 'That could not be done', r), r && r.ok ? 'success' : 'error');
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.suggestion_failed', 'That could not be done'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
    }
    loadV2Suggestions();
  }
  async function loadV2Suggestions() {
    const el = document.getElementById('v2-suggestions-list');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/profile/suggestions');
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error('HTTP ' + resp.status);
      const rows = data.suggestions || [];
      if (!rows.length) {
        el.textContent = _mt('memory.console.no_suggestions', 'Nothing to suggest for About me right now.');
        return;
      }
      el.innerHTML = rows.map(function(s) {
        return '<div class="mem-row v2-suggestion" data-id="' + _attr(s.id) + '" style="display:flex;gap:8px;align-items:flex-start;justify-content:space-between;padding:6px 0;">' +
          '<span style="min-width:0;overflow-wrap:anywhere;">' +
          '<span class="v2-suggestion-line" translate="no" style="display:block;color:var(--text-primary);">' + _esc(s.line) + '</span>' +
          '<span class="mem-muted" style="display:block;font-size:0.65rem;"><bdi>' + _esc(s.said_at ? _memWhen(s.said_at) : '') + '</bdi></span>' +
          '</span>' +
          '<span style="display:inline-flex;gap:4px;flex-shrink:0;flex-wrap:wrap;">' +
          '<button type="button" class="btn btn-sm btn-primary v2-suggestion-act" data-id="' + _attr(s.id) + '" data-decision="add">' + _esc(_mt('memory.console.suggestion_add', 'Add to About me')) + '</button>' +
          '<button type="button" class="btn btn-sm btn-outline v2-suggestion-act" data-id="' + _attr(s.id) + '" data-decision="dismiss">' + _esc(_mt('memory.console.suggestion_dismiss', 'Dismiss')) + '</button>' +
          '</span></div>';
      }).join('');
      el.querySelectorAll('.v2-suggestion-act').forEach(function(btn) {
        btn.addEventListener('click', function() {
          btn.disabled = true;
          _decideSuggestion(btn.getAttribute('data-id'), btn.getAttribute('data-decision'));
        });
      });
    } catch (e) { el.textContent = _mt('memory.console.suggestions_load_failed', 'Suggestions load failed'); }
  }
  document.getElementById('v2-suggestions-refresh')?.addEventListener('click', loadV2Suggestions);
  loadV2Suggestions();

  // Weekly topic summaries (plan C2): what each week's conversations said
  // about each topic. Forget one and its week never writes it again; the
  // conversations themselves stay.
  async function _forgetSummary(id) {
    const ok = await window.kazmaConfirm({
      title: _mt('memory.console.forget_summary_title', 'Forget this summary?'),
      message: _mt('memory.console.forget_summary_message', 'Kazma stops using this summary and never writes it again. The conversations it was written from stay as they are.'),
      confirmText: _mt('memory.console.forget', 'Forget'),
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await window.kazmaSave('/api/memory/v2/summaries/' + encodeURIComponent(id) + '/forget', { method: 'POST' });
      if (window.showToast) {
        window.showToast(r && r.ok ? _mt('memory.console.summary_forgotten', 'Summary forgotten') : (_errText('memory.console.forget_failed', 'Forget failed', r)), r && r.ok ? 'success' : 'error');
      }
    } catch (e) {
      window.kazmaAlert({ title: _mt('memory.console.forget_failed', 'Forget failed'), message: (e && e.body) ? _errText('memory.console.failed', 'Failed', e.body) : e.message, variant: 'btn-danger' });
    }
    loadV2Summaries();
  }
  var _SUM_PAGE = 40;
  var _sumOffset = 0;
  async function loadV2Summaries() {
    const el = document.getElementById('v2-summaries-list');
    const stateEl = document.getElementById('v2-summaries-state');
    if (!el) return;
    try {
      const resp = await fetch('/api/memory/v2/summaries?limit=' + _SUM_PAGE + '&offset=' + _sumOffset);
      const data = await resp.json();
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const st = data.state || {};
      _pageRange('v2-summaries-range', _sumOffset, (data.summaries || []).length, Number(data.total || 0));
      _pagerButtons('v2-summaries', _sumOffset, (data.summaries || []).length, Number(data.total || 0));
      if (stateEl) {
        const parts = [window.kazmaCount('memory.console.weeks_done', st.weeks_done || 0)];
        if (st.weeks_queued) parts.push(_mt('memory.console.weeks_queued', '{n} in progress', { n: st.weeks_queued }));
        if (st.weeks_failed) parts.push(_mt('memory.console.weeks_failed', '{n} failed', { n: st.weeks_failed }));
        stateEl.textContent = parts.join(' · ');
      }
      const rows = data.summaries || [];
      if (!rows.length) {
        el.textContent = _mt('memory.console.no_summaries', 'No weekly summaries yet. Kazma writes them a day after each week ends.');
        return;
      }
      // A week, or a month or project roll-up above the weeks (part G):
      // named by its stretch of time, in the page's language.
      function _summaryWhen(s) {
        const fmt = window.KazmaFormat;
        const month = function(ts) { return fmt ? fmt.dateTime(ts, { month: 'long', year: 'numeric' }) : ''; };
        if (s.level === 'month') {
          return _mt('memory.console.month_of', 'Month of {month}', { month: month(s.period_start) });
        }
        if (s.level === 'project') {
          return _mt('memory.console.project_span', 'Over several weeks, {from} to {to}',
            { from: month(s.period_start), to: month(Math.max(s.period_start, (s.period_end || 0) - 1)) });
        }
        return _mt('memory.console.week_of', 'Week of {date}', { date: s.period_start ? _memDay(s.period_start) : '' });
      }
      el.innerHTML = rows.map(function(s) {
        const size = window.kazmaCount('memory.console.summary_turns', s.turn_count || 0) +
          ((s.chat_count || 0) > 1 ? window.kazmaCount('memory.console.summary_chats', s.chat_count) : '');
        const pending = s.status === 'rebuild';
        const title = pending ? _mt('memory.console.summary_rebuilding', 'Being rewritten without a forgotten conversation') : (s.title || s.id);
        return '<div class="mem-row" style="display:flex;gap:8px;align-items:flex-start;justify-content:space-between;padding:6px 0;">' +
          '<span style="min-width:0;overflow-wrap:anywhere;">' +
          // The title is its own line, in its own language (as the memories
          // list above): inline in the row it took the page's direction.
          '<span' + (pending ? '' : ' translate="no"') + ' style="display:block;color:var(--text-primary);font-weight:600;">' + _esc(title) + '</span>' +
          '<span class="mem-muted v2-summary-when" data-level="' + _attr(s.level || 'week') + '" style="display:block;font-size:0.65rem;">' + _esc(_summaryWhen(s)) + ' · ' + _esc(size) + '</span>' +
          (pending ? '' : '<span translate="no" style="display:block;margin-top:2px;white-space:pre-wrap;">' + _esc(s.summary_text || '') + '</span>') +
          '</span>' +
          '<button type="button" class="btn btn-sm v2-summary-forget btn-outline" data-id="' + _attr(s.id) + '" style="flex-shrink:0;">' + _esc(_mt('memory.console.forget', 'Forget')) + '</button></div>';
      }).join('');
      el.querySelectorAll('.v2-summary-forget').forEach(function(btn) {
        btn.addEventListener('click', function() { _forgetSummary(btn.getAttribute('data-id')); });
      });
    } catch (e) { el.textContent = _mt('memory.console.summaries_load_failed', 'Weekly summaries load failed'); }
  }
  document.getElementById('v2-summaries-refresh')?.addEventListener('click', loadV2Summaries);
  document.getElementById('v2-summaries-newer')?.addEventListener('click', function() {
    _sumOffset = Math.max(0, _sumOffset - _SUM_PAGE); loadV2Summaries();
  });
  document.getElementById('v2-summaries-older')?.addEventListener('click', function() {
    _sumOffset += _SUM_PAGE; loadV2Summaries();
  });
  loadV2Summaries();
  loadV2Queue();
  loadV2Merges();
  loadV2Procedural();
  loadV2Quality();
  // Through _memEvery, which the leave hook clears: a raw setInterval kept
  // polling from every later page (audit 2026-10-26 AUD-030).
  _memEvery(15000, function() { if (document.hidden) return; loadV2Queue(); loadV2Merges(); loadV2Procedural(); loadV2Quality(); });

  async function loadV2Beliefs(q) {
    const list = document.getElementById('v2-belief-list');
    if (!list) return;
    const emptyCta = document.getElementById('v2-belief-empty-cta');
    const asked = ++_beliefsAsked;
    try {
      const url = '/api/memory/v2/beliefs' + (q ? ('?q=' + encodeURIComponent(q)) : '');
      const resp = await fetch(url);
      const data = await resp.json();
      if (asked !== _beliefsAsked) return;
      // A refused read is not "No facts yet".
      if (!resp.ok || data.error) throw new Error('HTTP ' + resp.status);
      const beliefs = data.beliefs || [];
      if (!beliefs.length) {
        // A search that matched nothing is not an empty memory.
        list.innerHTML = '<div class="mem-muted" style="padding:12px;font-size:0.82rem;">' + _esc(q
          ? _mt('memory.page.known_none_match', 'No fact holds these words.')
          : _mt('memory.page.known_empty', 'No facts yet.')) + '</div>';
        if (emptyCta) emptyCta.style.display = q ? 'none' : 'block';
        if (!q) list.innerHTML = '';
        return;
      }
      if (emptyCta) emptyCta.style.display = 'none';
      list.innerHTML = beliefs.map((b, idx) => {
        const conf = Math.round((b.confidence||0) * 100);
        // The graph's predicate palette, so a badge here matches its edges.
        const pc = _v2gPredVar(b.predicate_type) || 'var(--text-muted)';
        const ptype = _mtData('memory.console.ptype_', b.predicate_type || '?');
        const objRaw = String(b.object || '');
        const objFull = _esc(objRaw);
        const objShown = objRaw.length > 160 ? objFull.slice(0, 160) + '…' : objFull;
        const confText = _mt('memory.console.edge_conf', ' · confidence {n}%', { n: conf }).replace(/^\s*·\s*/, '');
        return '<div class="v2-belief-row" role="button" tabindex="0" data-id="' + _attr(b.id) + '" data-subject="' + _attr(b.subject) + '" data-object="' + _attr(b.object) + '" data-predicate="' + _attr(b.predicate) + '">' +
               '<span' + ptype.attr + ' class="mem-ptype" style="--mem-c:' + pc + ';">' + _esc(ptype.text) + '</span>' +
               '<div translate="no" dir="auto" style="flex:1;color:var(--text-primary);min-width:0;word-break:break-word;line-height:1.45;"><bdi><b>' + _esc(b.subject) + '</b></bdi> <bdi dir="ltr">' + _esc(String(b.predicate || '').replace(/_/g,' ')) + '</bdi> <bdi><b title="' + objFull + '">' + objShown + '</b></bdi></div>' +
               '<span class="mem-muted" style="font-size:0.72rem;flex-shrink:0;margin-top:2px;">' + _esc(confText) + '</span>' +
               '</div>';
      }).join('');
      // Each fact row opens its drawer and shows it on the map, by click or
      // by keyboard (Enter / Space).
      list.querySelectorAll('.v2-belief-row').forEach(function(row) {
        function open() {
          var subj = row.getAttribute('data-subject');
          var obj = row.getAttribute('data-object');
          var bid = row.getAttribute('data-id');
          if (bid) openBeliefDrawer(bid);
          _v2gSelectByBelief(subj, obj);
          list.querySelectorAll('.v2-belief-row').forEach(function(r) { r.classList.remove('is-selected'); });
          row.classList.add('is-selected');
        }
        row.addEventListener('click', open);
        row.addEventListener('keydown', function(ev) {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
        });
      });
    } catch (e) {
      if (asked !== _beliefsAsked) return;
      if (emptyCta) emptyCta.style.display = 'none';
      list.innerHTML = '<div role="alert" style="padding:12px;font-size:0.82rem;color:var(--danger);">' + _esc(_mt('memory.page.known_failed', 'The facts could not be loaded. This is not an empty memory.')) + '</div>';
    }
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
      var label = opts.name || _mt('memory.mc.legend_you', 'You');
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
  // For an attribute value: _esc leaves quotes as they are (Memory audit M36 --
  // a migrated entity type holding a quote wrote its own attributes).
  function _attr(s) { return _esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

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
    // _memEvery (cleared on leaving): /api/memory/v2/health was polled every
    // 5 s from every page after a visit (audit 2026-10-26 AUD-030).
    _memEvery(5000, function() { if (!document.hidden) pollV2Health(); });
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
  var _v2gInspectAllEdges = false;
  // As many dots as the filter keeps (_LOD_MAX, 200): at 80 the rest of a
  // larger memory was never drawn, and no line said so.
  var _v2gCap = 200, _v2gNodeBaseR = 7;
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

  // Palette: every colour is a token, read for the theme on screen. Entity
  // types are the category tokens (--cat-1..6), the hub ("You") the warning
  // colour, states the status tokens. HTML (filters, legend, inspector) uses
  // the tokens as var() so it follows a theme change by itself; the canvas
  // and the SVG export need resolved colours, cached per theme and dropped
  // on kazma:themechange (or a data-theme/style change on <html>).
  var _V2G_USER_TOKEN = '--warning';
  // The major things: the accent, as the tier palette's "major" below. It was
  // --cat-2, an amber next to the hub's yellow: the hub and its majors read
  // as one colour (2026-11-20).
  var _V2G_MAJOR_TOKEN = '--accent';
  var _V2G_TYPE_TOKENS = {
    person: '--cat-1',
    tool: '--cat-5',
    concept: '--cat-6',
    location: '--cat-3',
    project: '--cat-4',
    // entity, organization, event and any other type: the accent.
  };
  var _V2G_PRED_TOKENS = {
    functional: '--accent',
    set: '--cat-5',
    state: '--cat-6',
  };
  // F: tier palette. The operator organizes nodes into a tiered tree
  // (0=main/hub, 1=major, 2=sub, 3=leaf). Grouped nodes take their tier
  // colour; ungrouped nodes (tier -1/undefined) fall back to the entity-type
  // colour. The hub keeps its own colour regardless.
  var _V2G_TIER_TOKENS = {
    0: _V2G_USER_TOKEN,   // main — the hub's colour
    1: '--accent',        // major
    2: '--accent',        // sub
    3: '--text-muted',    // leaf
  };
  // A token's value is the text it was declared with: the accent shades are
  // color-mix() expressions since 2026-11-09 (kazma.css derives them from
  // --accent), and _v2gHexAlpha only adds alpha to #rrggbb. So any value
  // that is not a #rrggbb is painted on a 1x1 canvas and read back as one
  // (opaque tokens only: a translucent one would lose its alpha).
  var _v2gProbe = null;
  function _v2gToHex(v) {
    if (!v) return '';
    if (/^#[0-9a-f]{6}$/i.test(v)) return v;
    try {
      if (window.CSS && CSS.supports && !CSS.supports('color', v)) return '';
      if (!_v2gProbe) {
        var cv = document.createElement('canvas');
        cv.width = cv.height = 1;
        _v2gProbe = cv.getContext('2d', { willReadFrequently: true });
      }
      _v2gProbe.clearRect(0, 0, 1, 1);
      _v2gProbe.fillStyle = v;
      _v2gProbe.fillRect(0, 0, 1, 1);
      var d = _v2gProbe.getImageData(0, 0, 1, 1).data;
      return '#' + [d[0], d[1], d[2]].map(function (c) { return c.toString(16).padStart(2, '0'); }).join('');
    } catch (e) { return ''; }
  }
  // A token's colour for the theme on screen (#rrggbb), else the fallback
  // token's, else ''.
  function _v2gCss(name, fallbackName) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      var hex = _v2gToHex(v);
      if (hex) return hex;
    } catch (e) { /* fall through */ }
    return fallbackName ? _v2gCss(fallbackName) : '';
  }
  var _v2gThemeCache = null;
  function _v2gThemeColor(token) {
    var t = _v2gTheme();
    if (!(token in t.byToken)) t.byToken[token] = _v2gCss(token, '--accent');
    return t.byToken[token];
  }
  function _v2gTheme() {
    if (_v2gThemeCache) return _v2gThemeCache;
    var t = { byToken: {} };
    _v2gThemeCache = t;
    var c = function (name, fb) { return (t.byToken[name] = _v2gCss(name, fb || '--text-muted')); };
    t.accent = c('--accent');
    t.accentLight = c('--accent-light', '--accent');
    t.secondary = c('--secondary', '--accent');
    t.secondaryLight = c('--secondary-light', '--secondary');
    t.user = c(_V2G_USER_TOKEN);
    t.warning = c('--warning');
    t.danger = c('--danger');
    t.muted = c('--text-muted');
    t.ink = c('--text-primary');
    t.ink2 = c('--text-secondary');
    t.card = c('--surface-card');
    t.well = c('--surface-sunken', '--surface-card');
    t.major = c(_V2G_MAJOR_TOKEN, '--accent');
    t.type = {};
    Object.keys(_V2G_TYPE_TOKENS).forEach(function (k) { t.type[k] = c(_V2G_TYPE_TOKENS[k], '--accent'); });
    t.pred = {};
    Object.keys(_V2G_PRED_TOKENS).forEach(function (k) { t.pred[k] = c(_V2G_PRED_TOKENS[k], '--accent'); });
    return t;
  }
  // Drop the resolved colours and paint again (theme or accent changed).
  function _v2gThemeChanged() {
    _v2gThemeCache = null;
    _v2gRefreshPalette();
    if (typeof _v2gRepaint === 'function' && _v2gPts && _v2gPts.length) _v2gRepaint();
  }
  // A token as a CSS value, for HTML: follows a theme change with no repaint.
  function _v2gTokenVar(token) { return 'var(' + token + ')'; }
  // The same token as text ink: the accent's text-strength shade, which
  // reads 4.5:1 on the card and on a tint (the accent itself is a fill).
  function _v2gInkVar(token) { return _v2gTokenVar(token === '--accent' ? '--accent-light' : token); }
  function _v2gTypeVar(type) { return _v2gTokenVar(_V2G_TYPE_TOKENS[type] || '--accent'); }
  // Predicate colour as text (badges, inspector rows, filter dots).
  function _v2gPredVar(type) { return _V2G_PRED_TOKENS[type] ? _v2gInkVar(_V2G_PRED_TOKENS[type]) : ''; }
  // Legacy maps rebuilt from theme (canvas paint reads them)
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
      if (!raw || low === 'user' || low === 'you' || low === 'me') return _mt('memory.mc.legend_you', 'You');
      return raw; // e.g. Mubder / Kazma
    }
    return raw || String(p.id || '');
  }
  function _v2gTierColor(tier) {
    return _V2G_TIER_TOKENS[tier] ? _v2gThemeColor(_V2G_TIER_TOKENS[tier]) : undefined;
  }

  // The token a node is drawn in: the hub its own; a major node the
  // category colour kept for it, so the operator's marked nodes stand out
  // from the tier palette and the hub; a grouped node its tier's (0-3);
  // anything else its entity type's.
  function _v2gNodeToken(p) {
    if (_v2gIsUser(p)) return _V2G_USER_TOKEN;
    if (p && p.isMajor) return _V2G_MAJOR_TOKEN;
    if (p && p.tier !== undefined && p.tier >= 0 && _V2G_TIER_TOKENS[p.tier]) {
      return _V2G_TIER_TOKENS[p.tier];
    }
    return (p && _V2G_TYPE_TOKENS[p.type]) || '--accent';
  }
  // For the canvas and the SVG export: the resolved colour.
  function _v2gNodeColor(p) {
    return _v2gThemeColor(_v2gNodeToken(p));
  }
  // For HTML text (tooltip, inspector title): the token as ink.
  function _v2gNodeVar(p) {
    return _v2gInkVar(_v2gNodeToken(p));
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

  // ── The radial hierarchy (2026-11-20, the owner: "make the MAIN node
  // central and the Major nodes little smaller but little far from the
  // center, and the smaller nodes which is the sub-nodes"). Three ranks:
  //   1  the hub (you), held at the centre;
  //   2  the major things -- the ones marked major, or, when none is marked,
  //      the hub's most connected neighbours -- evenly spaced on an inner ring;
  //   3  everything else, on an outer band, drawn towards the side of the
  //      major thing it shares most lines with.
  // Each rank has its size, its label and its pull; a dot the person placed
  // stays where they put it.
  var _V2G_RING = { r2: 170, r3: 340, lanes: 1, hub: -1, majors: [] };
  var _V2G_AUTO_MAJORS = 5;
  var _V2G_LANE = 58;

  function _v2gRadiusFor(q) {
    var bonus = Math.sqrt(q.beliefCount || 1);
    if (q.rank === 1) return 22 + Math.min(6, bonus);
    if (q.rank === 2) return 14 + Math.min(5, bonus * 0.8);
    if (q.isBlob) return _v2gNodeBaseR - 3 + Math.min(4, bonus);
    return (q.isEpisode ? _v2gNodeBaseR - 1 : _v2gNodeBaseR) + Math.min(5, bonus * 1.2);
  }

  function _v2gAssignRanks(W, H) {
    var n = _v2gPts.length;
    var hub = -1, i, q;
    for (i = 0; i < n && hub < 0; i++) {
      q = _v2gPts[i];
      if (_v2gIsUser(q) && (q.isHub || String(q.id).toLowerCase() === 'user')) hub = i;
    }
    for (i = 0; i < n && hub < 0; i++) if (_v2gIsUser(_v2gPts[i])) hub = i;
    var majors = [];
    for (i = 0; i < n; i++) {
      q = _v2gPts[i];
      q.rank = 3; q.anchor = -1; q.autoMajor = false; q.ringAngle = undefined;
      if (i === hub) { q.rank = 1; continue; }
      if (q.isMajor && !_v2gIsUser(q)) { q.rank = 2; majors.push(i); }
    }
    if (!majors.length && hub >= 0) {
      // Nothing marked major: the hub's most connected neighbours stand in.
      var near = {};
      _v2gEdges.forEach(function(ed) {
        if (ed.a === hub) near[ed.b] = true;
        if (ed.b === hub) near[ed.a] = true;
      });
      Object.keys(near).map(Number).filter(function(k) {
        var c = _v2gPts[k];
        return c && !c.isValue && !c.isVirtual && !c.isEpisode && !c.isBlob && (c.degree || 0) >= 3;
      }).sort(function(a, b) {
        return (_v2gPts[b].degree || 0) - (_v2gPts[a].degree || 0);
      }).slice(0, _V2G_AUTO_MAJORS).forEach(function(k) {
        _v2gPts[k].rank = 2; _v2gPts[k].autoMajor = true; majors.push(k);
      });
    }
    // A sub-node's anchor: the major thing it shares most lines with.
    var shared = {};
    _v2gEdges.forEach(function(ed) {
      var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) return;
      var minor = A.rank === 3 && B.rank === 2 ? ed.a : (B.rank === 3 && A.rank === 2 ? ed.b : -1);
      if (minor < 0) return;
      var major = minor === ed.a ? ed.b : ed.a;
      shared[minor] = shared[minor] || {};
      shared[minor][major] = (shared[minor][major] || 0) + 1;
    });
    Object.keys(shared).forEach(function(k) {
      var best = -1, most = 0;
      Object.keys(shared[k]).forEach(function(m) {
        if (shared[k][m] > most) { most = shared[k][m]; best = Number(m); }
      });
      _v2gPts[Number(k)].anchor = best;
    });
    // The rings are as large as what they hold: majors at least 160px
    // apart; the outer band about 54px of circle per dot, in up to four
    // lanes, the most connected on the inner lane.
    var m = majors.length;
    var minors = [];
    for (i = 0; i < n; i++) if (_v2gPts[i].rank === 3) minors.push(i);
    var r2 = Math.max(170, (m * 160) / (2 * Math.PI));
    var lanes = Math.max(1, Math.min(4, Math.ceil(minors.length / 60)));
    var r3 = Math.max(r2 + 160, (minors.length * 54) / (2 * Math.PI * lanes) + (lanes - 1) * _V2G_LANE / 2);
    _V2G_RING = { r2: r2, r3: r3, lanes: lanes, hub: hub, majors: majors };
    majors.forEach(function(k, idx) {
      _v2gPts[k].ringAngle = -Math.PI / 2 + idx * 2 * Math.PI / Math.max(1, m);
      _v2gPts[k].ringR = r2;
    });
    minors.sort(function(a, b) { return (_v2gPts[b].degree || 0) - (_v2gPts[a].degree || 0); });
    minors.forEach(function(k, idx) {
      _v2gPts[k].ringR = r3 + ((idx % lanes) - (lanes - 1) / 2) * _V2G_LANE;
    });
    if (hub >= 0) _v2gPts[hub].ringR = 0;
    for (i = 0; i < n; i++) _v2gPts[i].r = _v2gRadiusFor(_v2gPts[i]);
    // First places: the hub at the centre, the majors on their ring, each
    // sub-node on its lane by its major's side (a dot already placed, by the
    // person or an earlier layout, and a grouped one keep theirs).
    var cx = W / 2, cy = H / 2;
    var spread = m ? (Math.PI / m) * 0.85 : Math.PI;
    for (i = 0; i < n; i++) {
      q = _v2gPts[i];
      if (q.fromCache || (q.groupParent !== undefined && q.groupParent >= 0)) continue;
      var ang;
      if (q.rank === 1) { q.x = cx; q.y = cy; continue; }
      if (q.rank === 2) ang = q.ringAngle;
      else if (q.anchor >= 0 && _v2gPts[q.anchor].ringAngle !== undefined) {
        // A deterministic place across the major's own sector.
        var frac = ((i * 0.618034) % 1) * 2 - 1;
        ang = _v2gPts[q.anchor].ringAngle + frac * spread;
      } else {
        ang = i * 2.39996;
      }
      q.x = cx + Math.cos(ang) * q.ringR;
      q.y = cy + Math.sin(ang) * q.ringR;
    }
  }

  // The length a line between two ranks rests at.
  function _v2gLinkLen(A, B) {
    var lo = Math.min(A.rank || 3, B.rank || 3), hi = Math.max(A.rank || 3, B.rank || 3);
    if (lo === 1 && hi === 2) return _V2G_RING.r2;
    if (lo === 1 && hi === 3) return _V2G_RING.r3;
    if (lo === 2 && hi === 3) return Math.max(90, _V2G_RING.r3 - _V2G_RING.r2);
    if (lo === 2 && hi === 2) return _V2G_RING.r2;
    return 80;
  }

  // Force sim step: repulsion + spring + gravity + collision. Spaced for
  // labels (2026-11-18 review): a dot's label sits under it, so lines are
  // longer and dots keep a gap a label fits in.
  function _v2gStep(W, H) {
    var n = _v2gPts.length; if (!n) return;
    var cx = W / 2, cy = H / 2;
    // Repulsion (Coulomb) — O(n²) but capped at _v2gCap nodes. A larger
    // dot pushes harder (the hub, then the majors), so the small ones fan out
    // around them instead of crowding them.
    var charge = n > 120 ? 1400 : 2200;
    var push = { 1: 1.8, 2: 1.35, 3: 0.85 };
    for (var i = 0; i < n; i++) {
      for (var j = i + 1; j < n; j++) {
        var a = _v2gPts[i], b = _v2gPts[j];
        var dx = b.x - a.x, dy = b.y - a.y;
        var d2 = dx * dx + dy * dy + 0.01;
        var d = Math.sqrt(d2);
        var force = charge * (push[a.rank] || 1) * (push[b.rank] || 1) * _v2gAlpha / d2;
        // Collision: two dots closer than their radii and a label's gap push
        // apart hard, so neither hides the other.
        // Two dots of the outer band keep a label's width between them.
        var minD = a.r + b.r + (a.rank === 3 && b.rank === 3 ? 56 : 26);
        if (d < minD) force += (minD - d) * 0.35 * _v2gAlpha;
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
      var targetLen = _v2gLinkLen(A, B);
      // A line from the hub to a sub-node is weak: the sub-node's ring holds
      // it, and many such lines would pull the outer band inwards.
      var hubToMinor = (A.rank === 1 && B.rank === 3) || (A.rank === 3 && B.rank === 1);
      var k = (hubToMinor ? 0.008 : 0.035) * (0.5 + (ed.confidence || 0.5));
      var f = (d - targetLen) * k * _v2gAlpha;
      var fx = (dx / d) * f, fy = (dy / d) * f;
      if (!(_v2gDrag && _v2gDrag.idx === ed.a) && !_v2gIsPinned(A)) { A.vx += fx; A.vy += fy; }
      if (!(_v2gDrag && _v2gDrag.idx === ed.b) && !_v2gIsPinned(B)) { B.vx -= fx; B.vy -= fy; }
    }
    // P2d: group-spring — holds grouped children in a tier-relative orbit
    // around their parent. Stiffer than belief-edges so a cross-cluster
    // belief edge doesn't pull a child out of its cluster. Mutates vx/vy only
    // (matches the existing force contract); reuses the same pinned/dragged
    // guards. Ungrouped nodes (groupParent === -1) skip.
    for (var gi = 0; gi < n; gi++) {
      var gp = _v2gPts[gi];
      if (!gp || gp.groupParent === undefined || gp.groupParent < 0) continue;
      var par = _v2gPts[gp.groupParent];
      if (!par) continue;
      var gdx = par.x - gp.x, gdy = par.y - gp.y;
      var gd = Math.sqrt(gdx * gdx + gdy * gdy + 0.01);
      var gTarget = 60 + (gp.tier >= 0 ? gp.tier : 1) * 34;
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
    // Gravity + integration. The map is not boxed into the canvas: Show all
    // fits whatever the layout needs (it squeezed a large map into the box).
    for (var p = 0; p < n; p++) {
      var pt = _v2gPts[p];
      if (_v2gDrag && _v2gDrag.idx === p) { pt.vx = 0; pt.vy = 0; continue; }
      if (_v2gIsPinned(pt)) {
        // Stay exactly where the user left it (refresh cache continuously)
        pt.vx = 0; pt.vy = 0;
        _v2gRememberPos(pt);
        continue;
      }
      if (pt.rank === 1) {
        // The hub: held at the centre.
        pt.vx += (cx - pt.x) * 0.25;
        pt.vy += (cy - pt.y) * 0.25;
      } else if (typeof pt.ringR === 'number') {
        // Every other dot: pulled to its ring's radius...
        var rx = pt.x - cx, ry = pt.y - cy;
        var rd = Math.sqrt(rx * rx + ry * ry) + 0.01;
        var rk = pt.rank === 2 ? 0.12 : 0.06;
        var rf = (pt.ringR - rd) * rk * _v2gAlpha;
        pt.vx += (rx / rd) * rf; pt.vy += (ry / rd) * rf;
        // ...a major to its own place on the inner ring (evenly spaced), a
        // sub-node towards the side of its major.
        var side = pt.rank === 2 ? pt.ringAngle
          : (pt.anchor >= 0 && _v2gPts[pt.anchor]
            ? Math.atan2(_v2gPts[pt.anchor].y - cy, _v2gPts[pt.anchor].x - cx) : undefined);
        if (side !== undefined) {
          var sk = (pt.rank === 2 ? 0.05 : 0.012) * _v2gAlpha;
          pt.vx += (cx + Math.cos(side) * pt.ringR - pt.x) * sk;
          pt.vy += (cy + Math.sin(side) * pt.ringR - pt.y) * sk;
        }
      } else {
        pt.vx += (cx - pt.x) * 0.003 * _v2gAlpha;
        pt.vy += (cy - pt.y) * 0.003 * _v2gAlpha;
      }
      pt.vx *= 0.8; pt.vy *= 0.8;
      var sp = Math.sqrt(pt.vx * pt.vx + pt.vy * pt.vy);
      if (sp > 16) { pt.vx = pt.vx / sp * 16; pt.vy = pt.vy / sp * 16; }
      pt.x += pt.vx; pt.y += pt.vy;
    }
    _v2gAlpha *= 0.982;
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
        if (!rawName || low === 'user' || low === 'you' || low === 'me') display = _mt('memory.mc.legend_you', 'You');
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
        p.isValue = !!nd.isValue;
        p.isHighStakes = !!nd.isHighStakes;
        p.isolated = !!nd.isolated;
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
          beliefCount: bc,
          // Placed by the person or by an earlier layout of this page: the
          // radial seed (_v2gAssignRanks) leaves it where it is.
          fromCache: !!(cached && typeof cached.x === 'number' && typeof cached.y === 'number'),
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
          // One fact's literal value (true, 4): its id is not an entity.
          isValue: !!nd.isValue,
          isEpisode: !!nd.isEpisode,
          // A thing the filter kept although none of its lines survived it.
          isolated: !!nd.isolated,
          degree: 0,
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
      // How many lines meet each dot: the labels of the most connected are
      // drawn first when they would overlap.
      _v2gEdges.forEach(function(ed) {
        if (_v2gPts[ed.a]) _v2gPts[ed.a].degree++;
        if (_v2gPts[ed.b]) _v2gPts[ed.b].degree++;
      });
      // The radial hierarchy: you at the centre, the major things on the
      // inner ring, the rest beyond them (sizes, rings and first places).
      _v2gAssignRanks(W, H);
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
        // The first layout is fitted in view once it settles.
        _v2gFitPending = true;
      }
      _v2gSelectedId = keepSel && _v2gIds[keepSel] !== undefined ? keepSel : null;
    }
    _v2gBindPointer(canvas, wrap);
    if (!_v2gAnim) _v2gTick();
  }

  // The map moves only while its layout settles or something is dragged:
  // no idle animation (a "breathing" hub and a pulsing ring kept a frame
  // loop running for as long as the page was open). Once the first layout
  // has settled, the whole map is fitted in view unless the person has
  // already moved or zoomed it.
  var _v2gFitPending = true;
  var _v2gUserView = false;

  function _v2gTick() {
    var size = _v2gCanvasSize(); if (!size) { _v2gAnim = null; return; }
    var ctx = size.ctx, W = size.w, H = size.h;
    if (_v2gAlpha > 0) _v2gStep(W, H);
    if (_v2gFitPending && !_v2gUserView && _v2gPts.length && _v2gAlpha < 0.05) {
      _v2gFitPending = false;
      _v2gFitView(W, H);
    }
    _v2gPaint(ctx, W, H);
    if (_v2gAlpha > 0 || _v2gDrag) {
      _v2gAnim = requestAnimationFrame(_v2gTick);
    } else {
      _v2gAnim = null;
    }
  }

  function _v2gRepaint() { if (!_v2gAnim) _v2gAnim = requestAnimationFrame(_v2gTick); }

  // Fit every dot (and the label under it) in view, centred.
  function _v2gFitView(W, H) {
    if (!_v2gPts.length) return;
    if (!W || !H) {
      var size = _v2gCanvasSize();
      if (!size) return;
      W = size.w; H = size.h;
    }
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    var padX = 70, padTop = 36, padBottom = 54;
    var bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
    var scale = Math.min((W - padX * 2) / bw, (H - padTop - padBottom) / bh);
    scale = Math.max(_v2gMinScale, Math.min(1.6, scale));
    _v2gView.scale = scale;
    _v2gView.ox = W / 2 - ((minX + maxX) / 2) * scale;
    _v2gView.oy = (padTop + (H - padBottom)) / 2 - ((minY + maxY) / 2) * scale;
    _v2gRepaint();
  }

  // Zoom around a point of the canvas (the centre, from the buttons).
  function _v2gZoomAt(factor, sx, sy) {
    var size = _v2gCanvasSize();
    if (!size) return;
    if (sx == null) { sx = size.w / 2; sy = size.h / 2; }
    var ns = Math.max(_v2gMinScale, Math.min(_v2gMaxScale, _v2gView.scale * factor));
    if (ns === _v2gView.scale) return;
    var wx = _v2gWX(sx), wy = _v2gWY(sy);
    _v2gView.scale = ns; _v2gView.ox = sx - wx * ns; _v2gView.oy = sy - wy * ns;
    _v2gUserView = true;
    _v2gRepaint();
  }

  // Whether text reads right to left: its first strong letter is Arabic,
  // Hebrew, Syriac, Thaana or N'Ko (the label is drawn and cut from that end).
  var _V2G_RTL = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  var _V2G_LTR = /[A-Za-zÀ-ɏͰ-ϿЀ-ӿ]/;
  function _v2gIsRtl(text) {
    var s = String(text || '');
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (_V2G_RTL.test(ch)) return true;
      if (_V2G_LTR.test(ch)) return false;
    }
    return false;
  }

  // A label cut to *max* pixels, from its logical end: in a right-to-left
  // label the ellipsis lands on the left, where that text ends.
  function _v2gFit(ctx, text, max) {
    var s = String(text || '');
    if (ctx.measureText(s).width <= max) return s;
    while (s.length > 2 && ctx.measureText(s + '…').width > max) s = s.slice(0, -1);
    return s.replace(/\s+$/, '') + '…';
  }

  function _v2gRoundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    if (typeof ctx.roundRect === 'function') { ctx.roundRect(x, y, w, h, r); return; }
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function _v2gOverlaps(rect, placed) {
    for (var i = 0; i < placed.length; i++) {
      var q = placed[i];
      if (rect.x < q.x + q.w && rect.x + rect.w > q.x && rect.y < q.y + q.h && rect.y + rect.h > q.y) return true;
    }
    return false;
  }

  // The map, drawn flat in the theme's tokens: lines in the secondary ink,
  // a dot in its kind's colour with a ring of the card colour, labels on a
  // plate under the dot. A line's relation is written on it only when it
  // matters (its dot chosen or hovered, the line chosen, or zoomed in on a
  // small map); a label that would overlap another is left out, the most
  // connected and the chosen ones first (2026-11-18 review: glows, a
  // pulsing hub, hidden arrowheads and labels on top of each other).
  function _v2gPaint(ctx, W, H) {
    _v2gRefreshPalette();
    var theme = _v2gTheme();
    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = 'round';
    ctx.shadowBlur = 0;

    var hoverId = _v2gHover >= 0 && _v2gPts[_v2gHover] ? _v2gPts[_v2gHover].id : null;
    var focusId = _v2gSelectedId || hoverId;
    // The focus's neighbours, once per frame (dots, labels and lines ask).
    var focusNbr = {};
    if (focusId) {
      for (var fe = 0; fe < _v2gEdges.length; fe++) {
        var fa = _v2gPts[_v2gEdges[fe].a], fb = _v2gPts[_v2gEdges[fe].b];
        if (!fa || !fb) continue;
        if (fa.id === focusId) focusNbr[fb.id] = true;
        if (fb.id === focusId) focusNbr[fa.id] = true;
      }
    }
    var smallMap = _v2gEdges.length <= 60;
    var edgeLabels = [];

    // ── The orbits: faint rings where the majors and the rest sit, around
    // the hub, so the hierarchy reads at a glance. ──
    var hubPt = _V2G_RING.hub >= 0 ? _v2gPts[_V2G_RING.hub] : null;
    if (hubPt) {
      var hx = _v2gSX(hubPt.x), hy = _v2gSY(hubPt.y);
      var orbits = [];
      if (_V2G_RING.majors.length) orbits.push(_V2G_RING.r2);
      if (_v2gPts.length > _V2G_RING.majors.length + 1) orbits.push(_V2G_RING.r3);
      ctx.setLineDash([2, 7]);
      ctx.lineWidth = 1;
      ctx.strokeStyle = _v2gHexAlpha(theme.ink2, focusId ? 0.07 : 0.14);
      orbits.forEach(function(rad) {
        ctx.beginPath(); ctx.arc(hx, hy, rad * _v2gView.scale, 0, 2 * Math.PI); ctx.stroke();
      });
      ctx.setLineDash([]);
    }

    // ── Lines ──
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e]; var A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      var ax = _v2gSX(A.x), ay = _v2gSY(A.y), bx = _v2gSX(B.x), by = _v2gSY(B.y);
      var len = Math.sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay));
      if (len < 1) continue;
      var ux = (bx - ax) / len, uy = (by - ay) / len;
      var edgeSelected = e === _v2gOps.selectedEdgeIdx;
      var beliefHot = _v2gHighlightSubj && _v2gHighlightObj &&
        ((A.id === _v2gHighlightSubj && B.id === _v2gHighlightObj) ||
         (A.id === _v2gHighlightObj && B.id === _v2gHighlightSubj));
      var pathHot = !!ed.pathHot || (_v2gPathIds[A.id] && _v2gPathIds[B.id]);
      var opsEdge = (_v2gOps.sourceId && _v2gOps.targetId &&
        ((A.id === _v2gOps.sourceId && B.id === _v2gOps.targetId) ||
         (A.id === _v2gOps.targetId && B.id === _v2gOps.sourceId)));
      var touchesFocus = focusId && (A.id === focusId || B.id === focusId);
      var hot = edgeSelected || beliefHot || pathHot || touchesFocus;
      var dimmed = focusId && !hot && !opsEdge;
      var color, width, alpha;
      if (edgeSelected || beliefHot) { color = theme.accent; width = 3; alpha = 1; }
      else if (opsEdge) { color = theme.user; width = 2.6; alpha = 1; }
      else if (hot) { color = theme.accent; width = 2.2; alpha = 0.9; }
      else if (ed.superseded) { color = theme.muted; width = 1.2; alpha = 0.5; }
      else if ((A.rank === 1 && B.rank === 2) || (A.rank === 2 && B.rank === 1)) {
        // A spoke: the hub to one of its major things.
        color = theme.major; width = 2.6; alpha = dimmed ? 0.15 : 0.55;
      } else if ((A.rank === 2 && B.rank === 3) || (A.rank === 3 && B.rank === 2)) {
        color = theme.ink2; width = 1.4; alpha = dimmed ? 0.12 : 0.4;
      }
      else { color = theme.ink2; width = 1.1 + (ed.confidence || 0.5) * 0.7; alpha = dimmed ? 0.12 : 0.28; }
      // Start and end at the dots' edges, so the arrowhead is seen.
      var x1 = ax + ux * (A.r + 2), y1 = ay + uy * (A.r + 2);
      var x2 = bx - ux * (B.r + 3), y2 = by - uy * (B.r + 3);
      ctx.strokeStyle = _v2gHexAlpha(color, alpha);
      ctx.lineWidth = width;
      ctx.setLineDash(ed.superseded ? [5, 4] : []);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.setLineDash([]);
      // Arrowhead: from the fact's subject to its value.
      var ah = hot ? 8 : 6, aw = 0.42;
      var ang = Math.atan2(uy, ux);
      ctx.fillStyle = _v2gHexAlpha(color, Math.min(1, alpha + 0.2));
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - ah * Math.cos(ang - aw), y2 - ah * Math.sin(ang - aw));
      ctx.lineTo(x2 - ah * Math.cos(ang + aw), y2 - ah * Math.sin(ang + aw));
      ctx.closePath();
      ctx.fill();
      if (ed.fullLabel && (edgeSelected || beliefHot || touchesFocus || (smallMap && _v2gView.scale >= 1.5))) {
        edgeLabels.push({ text: ed.fullLabel.replace(/_/g, ' '), x: (x1 + x2) / 2, y: (y1 + y2) / 2, hot: hot });
      }
    }

    // ── Dots ──
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var x = _v2gSX(p.x), y = _v2gSY(p.y);
      var r = p.r;
      var isHover = (i === _v2gHover), isSel = (p.id === _v2gSelectedId);
      var isUser = _v2gIsUser(p);
      var onPath = !!_v2gPathIds[p.id];
      // A value or a conversation has one colour each, as the legend says;
      // a thing its kind's.
      var color = p.isEpisode ? theme.secondary
        : ((p.isValue || p.isVirtual) && !isUser ? theme.accent : _v2gNodeColor(p));
      var faded = (p.isolated && !isSel && !isHover);
      // Dots that do not touch the focus fade, so the eye goes to the chosen one.
      var dim = focusId && !isSel && !isHover && p.id !== focusId && !focusNbr[p.id];
      ctx.globalAlpha = faded ? 0.35 : (dim ? 0.45 : 1);
      if (p.rank === 1 || p.rank === 2) {
        // The hub glows; a major thing wears a soft halo of its colour.
        var halo = p.rank === 1 ? r + 16 : r + 8;
        var glow = ctx.createRadialGradient(x, y, r * 0.6, x, y, halo);
        glow.addColorStop(0, _v2gHexAlpha(color, p.rank === 1 ? 0.32 : 0.24));
        glow.addColorStop(1, _v2gHexAlpha(color, 0));
        ctx.beginPath(); ctx.arc(x, y, halo, 0, 2 * Math.PI);
        ctx.fillStyle = glow; ctx.fill();
      }
      if (p.isBlob && !isUser) {
        // A long note: a small rounded chip, dashed, so it never reads as a
        // person or a place.
        var chipW = r * 2.6, chipH = r * 1.6;
        _v2gRoundRect(ctx, x - chipW / 2, y - chipH / 2, chipW, chipH, 4);
        ctx.fillStyle = theme.card; ctx.fill();
        ctx.setLineDash([3, 2]); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]);
      } else if (p.isVirtual || p.isValue || p.isEpisode) {
        // A value (a fact's answer) or a conversation: a hollow dot.
        ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
        ctx.fillStyle = theme.card; ctx.fill();
        ctx.setLineDash(p.isEpisode ? [3, 2] : []);
        ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI);
        ctx.fillStyle = color; ctx.fill();
        ctx.strokeStyle = theme.card; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.globalAlpha = 1;
      if (isUser) {
        ctx.beginPath(); ctx.arc(x, y, r + 4, 0, 2 * Math.PI);
        ctx.strokeStyle = _v2gHexAlpha(theme.user, 0.55); ctx.lineWidth = 2; ctx.stroke();
      }
      if (p.isHighStakes) {
        ctx.beginPath(); ctx.arc(x, y, r + 5, 0, 2 * Math.PI);
        ctx.setLineDash([3, 3]); ctx.strokeStyle = theme.danger; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]);
      }
      if (p.isolated && !p.isVirtual) {
        ctx.beginPath(); ctx.arc(x, y, r + 3, 0, 2 * Math.PI);
        ctx.setLineDash([2, 3]); ctx.strokeStyle = theme.warning; ctx.lineWidth = 1.5; ctx.stroke(); ctx.setLineDash([]);
      }
      if (isSel || isHover || onPath) {
        ctx.beginPath(); ctx.arc(x, y, r + (isUser ? 8 : 5), 0, 2 * Math.PI);
        ctx.strokeStyle = isSel || onPath ? theme.accent : _v2gHexAlpha(theme.ink2, 0.6);
        ctx.lineWidth = isSel ? 2.5 : 2; ctx.stroke();
      }
      if (_v2gOps.sourceId && p.id === _v2gOps.sourceId) {
        ctx.beginPath(); ctx.arc(x, y, r + 10, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.accent; ctx.lineWidth = 2.4; ctx.stroke();
      }
      if (_v2gOps.targetId && p.id === _v2gOps.targetId) {
        ctx.beginPath(); ctx.arc(x, y, r + 12, 0, 2 * Math.PI);
        ctx.strokeStyle = theme.secondary; ctx.lineWidth = 2; ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]);
      }
    }

    // ── Labels: the chosen, the hovered, you, then the most connected;
    // one that would overlap a label already placed is left out. ──
    if (_v2gView.scale < 0.4) return;
    var order = [];
    for (var o = 0; o < _v2gPts.length; o++) order.push(o);
    function rank(idx) {
      var q = _v2gPts[idx];
      if (q.id === _v2gSelectedId) return 1e9;
      if (idx === _v2gHover) return 1e8;
      if (_v2gIsUser(q)) return 1e7;
      if (q.rank === 2) return 5e6 + (q.degree || 0);
      if (focusNbr[q.id]) return 1e6 + (q.degree || 0);
      return (q.isMajor ? 1e5 : 0) + (q.degree || 0) * 10 + (q.isVirtual ? 0 : 5);
    }
    var ranks = {};
    for (var rk = 0; rk < order.length; rk++) ranks[rk] = rank(rk);
    order.sort(function(a, b) { return ranks[b] - ranks[a]; });
    var placed = [];
    // Edge labels claim their space first when a dot is chosen (they are why
    // it was chosen); the dots' own discs are never covered.
    for (var di = 0; di < _v2gPts.length; di++) {
      var dp = _v2gPts[di];
      var ddx = _v2gSX(dp.x), ddy = _v2gSY(dp.y);
      placed.push({ x: ddx - dp.r, y: ddy - dp.r, w: dp.r * 2, h: dp.r * 2 });
    }
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    for (var li = 0; li < edgeLabels.length; li++) {
      var el = edgeLabels[li];
      ctx.font = _v2gFont(11);
      ctx.direction = _v2gIsRtl(el.text) ? 'rtl' : 'ltr';
      var et = _v2gFit(ctx, el.text, 160);
      var ew = ctx.measureText(et).width + 12, eh = 18;
      var er = { x: el.x - ew / 2, y: el.y - eh / 2, w: ew, h: eh };
      if (!el.hot && _v2gOverlaps(er, placed)) continue;
      placed.push(er);
      _v2gRoundRect(ctx, er.x, er.y, er.w, er.h, 9);
      ctx.fillStyle = _v2gHexAlpha(theme.card, 0.94); ctx.fill();
      ctx.strokeStyle = _v2gHexAlpha(el.hot ? theme.accent : theme.ink2, el.hot ? 0.55 : 0.2);
      ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = el.hot ? theme.ink : theme.ink2;
      ctx.fillText(et, el.x, el.y + 0.5);
    }
    for (var n2 = 0; n2 < order.length; n2++) {
      var idx = order[n2];
      var q = _v2gPts[idx];
      var qx = _v2gSX(q.x), qy = _v2gSY(q.y);
      // You and the major things always have their names; their type is
      // larger and bolder, the rest smaller.
      var must = q.id === _v2gSelectedId || idx === _v2gHover || _v2gIsUser(q) || q.rank === 2;
      var bold = _v2gIsUser(q) || q.id === _v2gSelectedId;
      var size = q.rank === 1 ? 15 : (q.rank === 2 ? 13 : (bold ? 13 : 11));
      var weight = q.rank === 1 ? '700 ' : (q.rank === 2 || bold ? '600 ' : '');
      ctx.font = weight + _v2gFont(size);
      var full = q.fullLabel || q.label || String(q.id || '');
      ctx.direction = _v2gIsRtl(full) ? 'rtl' : 'ltr';
      var lab = _v2gFit(ctx, full, must ? 220 : 150);
      var lw = ctx.measureText(lab).width + 10, lh = size + 7;
      // Under the dot first; when that place is taken, above it, then beside
      // it (a crowded fan of small dots hid most of its names).
      var spots = [
        { x: qx - lw / 2, y: qy + q.r + 6 },
        { x: qx - lw / 2, y: qy - q.r - 6 - lh },
        { x: qx + q.r + 6, y: qy - lh / 2 },
        { x: qx - q.r - 6 - lw, y: qy - lh / 2 },
      ];
      var rect = null;
      for (var sp2 = 0; sp2 < (must ? 1 : spots.length); sp2++) {
        var cand = { x: spots[sp2].x, y: spots[sp2].y, w: lw, h: lh };
        if (must || !_v2gOverlaps(cand, placed)) { rect = cand; break; }
      }
      if (!rect) continue;
      placed.push(rect);
      var dimLabel = focusId && !must && ranks[idx] < 1e6;
      _v2gRoundRect(ctx, rect.x, rect.y, rect.w, rect.h, 6);
      ctx.fillStyle = _v2gHexAlpha(theme.card, dimLabel ? 0.7 : 0.9); ctx.fill();
      ctx.fillStyle = dimLabel ? _v2gHexAlpha(theme.ink, 0.55) : theme.ink;
      ctx.fillText(lab, rect.x + rect.w / 2, rect.y + lh / 2 + 0.5);
    }
    ctx.direction = 'inherit';
    ctx.textBaseline = 'alphabetic';
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
      var res = native.call(window, (opts && (opts.message || opts.title)) || _mt('memory.console.confirm_q', 'Confirm?'));
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
    // The two things a person picked, by the name the map shows (the ids
    // "src: shipx" read as code).
    if (srcEl) {
      srcEl.textContent = _v2gOps.sourceId
        ? _mt('memory.page.connect_from', 'From: {name}', { name: _v2gNameOf(_v2gOps.sourceId) })
        : _mt('memory.page.connect_from_empty', 'From: none');
      srcEl.title = _v2gOps.sourceId || _mt('memory.page.connect_from_hint', 'The first thing');
      srcEl.classList.toggle('is-set', !!_v2gOps.sourceId);
    }
    if (tgtEl) {
      tgtEl.textContent = _v2gOps.targetId
        ? _mt('memory.page.connect_to', 'To: {name}', { name: _v2gNameOf(_v2gOps.targetId) })
        : _mt('memory.page.connect_to_empty', 'To: none');
      tgtEl.title = _v2gOps.targetId || _mt('memory.page.connect_to_hint', 'The second thing');
      tgtEl.classList.toggle('is-set', !!_v2gOps.targetId);
    }
    // A pick or a mode opens the fold that holds these controls, so what was
    // picked is never out of sight.
    if (_v2gOps.sourceId || _v2gOps.targetId || _v2gOps.mode) {
      var fold = document.getElementById('v2g-edit');
      if (fold && !fold.open) fold.open = true;
    }
    if (hint) {
      if (_v2gOps.mode === 'link') {
        hint.textContent = _v2gOps.sourceId
          ? _mt('memory.console.hint_link_target', 'Link mode: click the target node…')
          : _mt('memory.console.hint_link_source', 'Link mode: click the source node…');
        hint.style.color = 'var(--accent-light)';
      } else if (_v2gOps.mode === 'merge') {
        hint.textContent = _v2gOps.sourceId
          ? _mt('memory.console.hint_merge_target', 'Merge mode: click the target (survivor)…')
          : _mt('memory.console.hint_merge_source', 'Merge mode: click the source (will be retired)…');
        hint.style.color = 'var(--warning)';
      } else if (_v2gOps.mode === 'repoint') {
        hint.textContent = _mt('memory.console.hint_move', 'Move mode: click the new endpoint node… (Clear to cancel)');
        hint.style.color = 'var(--accent-light)';
      } else if (_v2gOps.mode === 'group') {
        hint.textContent = _mt('memory.console.hint_group', 'Group mode: click the PARENT node… (Clear to cancel)');
        hint.style.color = 'var(--accent-light)';
      } else if (_v2gOps.sourceId && _v2gOps.targetId) {
        hint.textContent = _mt('memory.console.hint_ready', 'Ready — press Link or Merge, or click an edge to edit/unlink.');
        hint.style.color = 'var(--text-secondary)';
      } else {
        hint.textContent = _mt('memory.console.hint_idle', 'Click node → inspect. Click edge → edit/unlink. Link: set src+tgt or use pick mode.');
        hint.style.color = 'var(--text-muted)';
      }
    }
    if (linkBtn) {
      var linkOn = _v2gOps.mode === 'link' || !!(!_v2gOps.mode && _v2gOps.sourceId && _v2gOps.targetId);
      // Soft until armed, filled when it will act (never no variant at all).
      linkBtn.classList.toggle('btn-primary', linkOn);
      linkBtn.classList.toggle('btn-secondary', !linkOn);
      linkBtn.textContent = _v2gOps.mode === 'link' ? _mt('memory.console.linking', 'Linking…') : _mt('memory.page.connect_link', 'Connect');
    }
    if (mergeBtn) {
      mergeBtn.classList.toggle('btn-primary', _v2gOps.mode === 'merge');
      mergeBtn.classList.toggle('btn-secondary', _v2gOps.mode !== 'merge');
      mergeBtn.textContent = _v2gOps.mode === 'merge' ? _mt('memory.console.merging', 'Merging…') : _mt('memory.page.connect_merge', 'Join into one');
    }
  }

  // The name the map shows for a node id, else the id.
  function _v2gNameOf(id) {
    for (var i = 0; i < _v2gPts.length; i++) {
      if (_v2gPts[i].id === id) return _v2gDisplayName(_v2gPts[i]);
    }
    return _v2gShortId(id);
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
        ? _mt('memory.console.link_mode_hint', 'Link mode: click the source, then the target')
        : _mt('memory.console.merge_mode_hint', 'Merge mode: click the one to retire, then the one to keep'),
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
      _v2gToast(_mt('memory.console.need_slots', 'Set source and target first'), 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast(_mt('memory.console.slots_differ', 'Source and target must differ'), 'error');
      return false;
    }
    try {
      var data = await _v2gApiJson('/api/memory/v2/entities/link', {
        method: 'POST',
        body: JSON.stringify({ subject: src, predicate: pred, object: tgt }),
      });
      if (!data.ok) {
        _v2gToast(_errText('memory.console.link_failed', 'Link failed', data), 'error');
        console.warn('[v2g] link failed', data);
        return false;
      }
      _v2gToast(
        _mt(data.already ? 'memory.console.already_linked' : 'memory.console.linked',
          data.already ? 'Already linked · {subject} —{predicate}→ {object}' : 'Linked {subject} —{predicate}→ {object}',
          { subject: _v2gShortId(data.subject || src), predicate: pred, object: _v2gShortId(data.object || tgt) }),
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
      _v2gToast(_mt('memory.console.link_failed_error', 'Link failed: {error}', { error: err && err.message ? err.message : err }), 'error');
      console.warn('[v2g] link exception', err);
      return false;
    }
  }

  async function _v2gDoMerge(src, tgt) {
    src = String(src || '').trim();
    tgt = String(tgt || '').trim();
    if (!src || !tgt) {
      _v2gToast(_mt('memory.console.need_slots', 'Set source and target first'), 'error');
      return false;
    }
    if (src === tgt) {
      _v2gToast(_mt('memory.console.slots_differ', 'Source and target must differ'), 'error');
      return false;
    }
    // It cannot be undone: said, and asked as a destructive action.
    var ok = await _v2gConfirm({
      title: _mt('memory.page.merge_confirm_title', 'Join into one? This cannot be undone'),
      message: _mt('memory.page.merge_confirm', 'Every fact about {from} moves to {to}, and {from} goes. This cannot be undone.', { from: _v2gNameOf(src), to: _v2gNameOf(tgt) }),
      confirmText: _mt('memory.page.merge_confirm_button', 'Join them'),
      danger: true,
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
        _v2gToast(_errText('memory.console.merge_failed', 'Merge failed', data), 'error');
        return false;
      }
      _v2gToast(_mt('memory.console.merged', 'Merged {source} → {target}', { source: _v2gShortId(src), target: _v2gShortId(tgt) }), 'success');
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
      _v2gToast(_mt('memory.console.merge_failed', 'Merge failed'), 'error');
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
      if (!opts.silent) _v2gToast(_mt('memory.console.cut_missing', 'Cannot cut — missing belief id and edge triple'), 'error');
      return false;
    }
    if (!opts.skipConfirm) {
      var ok = await _v2gConfirm({
        title: _mt('memory.console.cut_title', 'Cut connection'),
        message:
          _mt('memory.console.cut_message', 'Remove this edge from active memory?') + '\n' +
          (subject && predicate
            ? subject + ' —' + predicate + '→ ' + object
            : _mt('memory.console.cut_message_soft', 'It stays in the record of past facts.')),
        confirmText: _mt('memory.console.cut', 'Cut'),
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
          _v2gToast(_errText('memory.console.cut_failed', 'Cut failed', data), 'error');
          console.warn('[v2g] unlink failed', data, { beliefId: beliefId, seed: seed });
        }
        return false;
      }
      if (!opts.silent && !opts.skipReload) {
        _v2gToast(data.already ? _mt('memory.console.already_cut', 'Already cut') : _mt('memory.console.connection_cut', 'Connection cut'), 'success');
      }
      if (!opts.skipReload) {
        _v2gOps.selectedEdgeIdx = -1;
        var insp = document.getElementById('v2g-inspect');
        if (insp) {
          _v2gInspectClose();
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
        _v2gToast(_mt('memory.console.cut_failed_error', 'Cut failed: {error}', { error: err && err.message ? err.message : err }), 'error');
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
      _v2gToast(_mt('memory.console.no_edges_to_cut', 'No edges to cut'), 'info');
      return 0;
    }
    var msg =
      opts.message ||
      window.kazmaCount('memory.console.cut_n_message', items.length);
    var ok = await _v2gConfirm({
      title: opts.title || _mt('memory.console.cut_connections_title', 'Cut connections'),
      message: msg,
      confirmText: opts.confirmText || _mt('memory.console.cut_n', 'Cut {n}', { n: items.length }),
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
      _v2gToast(window.kazmaCount('memory.console.cut_done', n), 'success');
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
      _v2gToast(_mt('memory.console.no_edges_cut', 'No edges were cut'), 'error');
    }
    return n;
  }

  /** Cut every direct edge between this node and the memory hub (You/Mubder). */
  async function _v2gCutHubLinks(nodeId) {
    var edges = _v2gEdgesForNode(nodeId).filter(function(x) { return x.toHub; });
    if (!edges.length) {
      _v2gToast(_mt('memory.console.no_hub_link', 'No direct hub link on this node'), 'info');
      return 0;
    }
    var otherLinks = _v2gEdgesForNode(nodeId).filter(function(x) { return !x.toHub; });
    var hint = otherLinks.length
      ? '\n\n' + _mt('memory.console.hub_keeps', 'Keeps links to: {names}', {
          names: otherLinks
            .map(function(x) { return _v2gDisplayName(x.other); })
            .slice(0, 6)
            .join(', ') +
            (otherLinks.length > 6 ? '…' : ''),
        }) +
        '\n' + _mt('memory.console.hub_chain_hint', 'Useful when the chain should be leaf → parent → hub (not leaf → hub).')
      : '';
    return _v2gCutEdges(
      edges.map(function(x) {
        return { beliefId: x.ed.beliefId, seed: x.seed, ed: x.ed };
      }),
      {
        title: _mt('memory.console.cut_hub_title', 'Cut hub shortcut'),
        message:
          window.kazmaCount('memory.console.cut_hub_message', edges.length) + hint,
        confirmText: edges.length > 1
          ? _mt('memory.console.cut_hub_links', 'Cut hub links')
          : _mt('memory.console.cut_hub_link', 'Cut hub link'),
      }
    );
  }

  async function _v2gEditBeliefById(beliefId, seed) {
    seed = seed || {};
    if (!beliefId) {
      _v2gToast(_mt('memory.console.no_belief_id_edit', 'No belief id — cannot edit'), 'error');
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
      title: _mt('memory.console.edit_object_title', 'Edit belief — object'),
      message: _mt('memory.console.edit_object_message', 'Fact / object text. Cancel aborts.'),
      defaultValue: b.object || '',
      confirmText: _mt('memory.console.next', 'Next'),
      placeholder: _mt('memory.console.edit_object_ph', 'e.g. Paris'),
    });
    if (object == null) return false;
    var predicate = await _v2gPrompt({
      title: _mt('memory.console.edit_predicate_title', 'Edit belief — predicate'),
      message: _mt('memory.console.edit_predicate_message', 'Relation name (snake_case ok).'),
      defaultValue: b.predicate || '',
      confirmText: _mt('memory.console.next', 'Next'),
      placeholder: 'lives_in',
    });
    if (predicate == null) return false;
    var subject = await _v2gPrompt({
      title: _mt('memory.console.edit_subject_title', 'Edit belief — subject'),
      message: _mt('memory.console.edit_subject_message', 'Subject entity id.'),
      defaultValue: b.subject || '',
      confirmText: _mt('memory.console.save', 'Save'),
      placeholder: 'user',
    });
    if (subject == null) return false;
    subject = String(subject).trim();
    predicate = String(predicate).trim();
    object = String(object).trim();
    if (!subject || !predicate || !object) {
      _v2gToast(_mt('memory.console.spo_required', 'Subject, predicate, and object are required'), 'error');
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
        _v2gToast(_errText('memory.console.edit_failed', 'Edit failed', data), 'error');
        return false;
      }
      _v2gToast(_mt('memory.console.belief_updated', 'Belief updated'), 'success');
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
      _v2gToast(_mt('memory.console.edit_failed', 'Edit failed'), 'error');
      return false;
    }
  }

  // F1: repoint (move) a belief's subject or object to a different node.
  // Flow: click "Move" on an edge → choose which side (subject/object) →
  // enter pick mode → click the new endpoint node → POST /repoint. Replaces
  // the destructive cut+relink two-step that left nodes adrift.
  function _v2gRepointBelief(beliefId, seed) {
    seed = seed || {};
    if (!beliefId) { _v2gToast(_mt('memory.console.no_belief_id_move', 'No belief id — cannot move'), 'error'); return false; }
    var subj = seed.subject || '';
    var obj = seed.object || '';
    var fromId = _v2gSelectedId;
    // Ask which endpoint to move. Default to the non-selected endpoint so the
    // most common case (move the OTHER end away from the inspected node) is
    // one click.
    _v2gConfirm({
      title: _mt('memory.console.move_title', 'Move which end of the edge?'),
      message: _mt('memory.console.move_message', 'Subject = {subject} · Object = {object}. Pick the end to move to another node.', {
        subject: _v2gShortId(subj), object: _v2gShortId(obj),
      }),
      confirmText: _mt('memory.console.move_subject', 'Move subject'),
      cancelText: _mt('memory.console.move_object', 'Move object'),
    }).then(function (moveSubject) {
      _v2gOps.mode = 'repoint';
      _v2gOps.repoint = { beliefId: beliefId, side: moveSubject ? 'subject' : 'object' };
      _v2gSyncOpsBar();
      _v2gToast(_mt('memory.console.click_new_endpoint', 'Click the new endpoint node…'), 'info');
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
        _v2gToast(_errText('memory.console.move_failed', 'Move failed', data), 'error');
        return false;
      }
      var warn = data.warn_orphaned && data.warn_orphaned.length
        ? _mt('memory.console.moved_stranded', ' · stranded: {ids}', { ids: data.warn_orphaned.join(', ') })
        : '';
      _v2gToast(_mt('memory.console.moved_edge', 'Moved edge to {id}', { id: _v2gShortId(newEndpoint) }) +
        warn, 'success');
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
      _v2gToast(_mt('memory.console.move_failed', 'Move failed'), 'error');
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
      if (!data.ok) { _v2gToast(_errText('memory.console.toggle_failed', 'Toggle failed', data), 'error'); return; }
      p.isMajor = newMajor;
      _v2gToast(newMajor
        ? _mt('memory.console.marked_major', 'Marked {id} as major', { id: _v2gShortId(p.id) })
        : _mt('memory.console.unmarked_major', 'Unmarked {id}', { id: _v2gShortId(p.id) }), 'success');
      _v2gRepaint();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'major', id: p.id, major: newMajor } }));
      } catch (e) { /* ignore */ }
    } catch (e) {
      _v2gToast(_mt('memory.console.toggle_failed', 'Toggle failed'), 'error');
    }
  }

  function _v2gGroupUnder(memberId) {
    memberId = String(memberId || '').trim();
    if (!memberId) { _v2gToast(_mt('memory.console.pick_node_first', 'Pick a node first'), 'error'); return false; }
    if (_v2gIsUser({ id: memberId })) {
      _v2gToast(_mt('memory.console.hub_not_groupable', 'The hub is the top-level root — not groupable'), 'info');
      return false;
    }
    _v2gOps.mode = 'group';
    _v2gOps.group = { member: memberId };
    _v2gSyncOpsBar();
    _v2gToast(_mt('memory.console.group_pick_parent', 'Click the PARENT node to group "{id}" under…', { id: _v2gShortId(memberId) }), 'info');
    return true;
  }

  async function _v2gDoGroup(memberId, rootId) {
    // A node already in a group MOVES: the move route replaces its old
    // parent and re-tiers the nodes below it. The create route refuses a
    // second parent (it failed outright here until 2026-10-01).
    var current = (_v2gGroups || []).find(function(g) { return g.member === memberId; });
    if (current && current.group_root === rootId) {
      _v2gToast(_mt('memory.console.already_in_group', '{member} is already grouped under {root}', {
        member: _v2gShortId(memberId), root: _v2gShortId(rootId),
      }), 'info');
      return false;
    }
    try {
      var data = current
        ? await _v2gApiJson('/api/memory/v2/graph/groups/member/' + encodeURIComponent(memberId) + '/move', {
          method: 'POST',
          body: JSON.stringify({ new_root: rootId }),
        })
        : await _v2gApiJson('/api/memory/v2/graph/groups', {
          method: 'POST',
          body: JSON.stringify({ group_root: rootId, member: memberId }),
        });
      if (!data.ok) {
        _v2gToast(_errText('memory.console.group_failed', 'Group failed', data), 'error');
        return false;
      }
      var tier = current ? data.new_tier : data.member_tier;
      _v2gToast(
        current
          ? _mt('memory.console.moved_group', 'Moved {member} under {root} (tier {tier}; {n} below re-tiered) · view-only, memory untouched', {
            member: _v2gShortId(memberId), root: _v2gShortId(rootId), tier: tier, n: data.subtree_retiered || 0,
          })
          : _mt('memory.console.grouped', 'Grouped {member} under {root} (tier {tier}) · view-only, memory untouched', {
            member: _v2gShortId(memberId), root: _v2gShortId(rootId), tier: tier,
          }),
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
          detail: { op: current ? 'move' : 'group', member: memberId, root: rootId, tier: tier },
        }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast(_mt('memory.console.group_failed', 'Group failed'), 'error');
      return false;
    }
  }

  // Override a grouped node's tier (when parent + 1 is the wrong depth).
  async function _v2gSetTier(memberId) {
    memberId = String(memberId || '').trim();
    var row = (_v2gGroups || []).find(function(g) { return g.member === memberId; });
    if (!row) {
      _v2gToast(_mt('memory.console.not_grouped', 'Not grouped'), 'info');
      return false;
    }
    var answer = await _v2gPrompt({
      title: _mt('memory.console.tier_prompt_title', 'Node tier'),
      message: _mt('memory.console.tier_prompt_message', 'Tier for {id}, from 0 to 4. Nodes below it keep their tiers.', {
        id: _v2gShortId(memberId),
      }),
      defaultValue: String(row.member_tier != null ? row.member_tier : ''),
    });
    if (answer == null) return false;
    var text = String(answer).trim();
    if (!/^[0-4]$/.test(text)) {
      _v2gToast(_mt('memory.console.tier_invalid', 'Enter a whole number from 0 to 4'), 'error');
      return false;
    }
    try {
      var data = await _v2gApiJson('/api/memory/v2/graph/groups/node/' + encodeURIComponent(memberId) + '/tier', {
        method: 'POST',
        body: JSON.stringify({ tier: Number(text) }),
      });
      if (!data.ok) {
        _v2gToast(_errText('memory.console.tier_failed', 'Setting the tier failed', data), 'error');
        return false;
      }
      _v2gToast(_mt('memory.console.tier_set', '{id} is now tier {tier} · view-only, memory untouched', {
        id: _v2gShortId(memberId), tier: data.member_tier,
      }), 'success');
      await _v2gReloadGraph();
      _v2gSelectEntity(memberId, { notify: false });
      return true;
    } catch (err) {
      _v2gToast(_mt('memory.console.tier_failed', 'Setting the tier failed'), 'error');
      return false;
    }
  }

  async function _v2gUngroup(memberId) {
    memberId = String(memberId || '').trim();
    if (!memberId) return false;
    var row = (_v2gGroups || []).find(function(g) { return g.member === memberId; });
    if (!row || !row.id) {
      _v2gToast(_mt('memory.console.not_grouped', 'Not grouped'), 'info');
      return false;
    }
    try {
      var data = await _v2gApiJson('/api/memory/v2/graph/groups/' + encodeURIComponent(row.id), {
        method: 'DELETE',
      });
      if (!data.ok) {
        _v2gToast(_errText('memory.console.ungroup_failed', 'Ungroup failed', data), 'error');
        return false;
      }
      _v2gToast(_mt('memory.console.ungrouped', 'Ungrouped {id} · view-only, memory untouched', { id: _v2gShortId(memberId) }), 'success');
      await _v2gReloadGraph();
      return true;
    } catch (err) {
      _v2gToast(_mt('memory.console.ungroup_failed', 'Ungroup failed'), 'error');
      return false;
    }
  }

  async function _v2gDeleteEntity(id) {
    if (!id || id === 'user') {
      _v2gToast(_mt('memory.console.cannot_delete_hub', 'Cannot delete protected hub'), 'error');
      return false;
    }
    var ok = await _v2gConfirm({
      title: _mt('memory.console.delete_title', 'Delete entity'),
      message: _mt('memory.console.delete_message', 'Delete entity shell “{id}”? (Protected / non-empty may fail.)', { id: id }),
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
        _v2gToast(_errText('memory.console.delete_failed', 'Delete failed', data), 'error');
        return false;
      }
      _v2gToast(_mt('memory.console.deleted', 'Deleted {id}', { id: _v2gShortId(id) }), 'success');
      if (_v2gOps.sourceId === id) _v2gOps.sourceId = null;
      if (_v2gOps.targetId === id) _v2gOps.targetId = null;
      _v2gBroadcastSlots();
      await _v2gReloadGraph();
      try {
        window.dispatchEvent(new CustomEvent('kazma:memory-ops-done', { detail: { op: 'delete', id: id } }));
      } catch (e) { /* ignore */ }
      return true;
    } catch (err) {
      _v2gToast(_mt('memory.console.delete_failed', 'Delete failed'), 'error');
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
      if (p.id === _v2gSelectedId) { _v2gToast(_mt('memory.console.pick_different_node', 'Pick a different node'), 'info'); return true; }
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
      if (!member || p.id === member) { _v2gToast(_mt('memory.console.pick_different_parent', 'Pick a different parent node'), 'info'); return true; }
      _v2gDoGroup(member, p.id);
      return true;
    }
    if (!_v2gOps.sourceId) {
      _v2gSetSlot('source', p.id);
      _v2gSyncOpsBar();
      return true;
    }
    if (p.id === _v2gOps.sourceId) {
      _v2gToast(_mt('memory.console.pick_different_target', 'Pick a different node as target'), 'info');
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

  function _v2gIsPhone() {
    try {
      return !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches);
    } catch (e) {
      return false;
    }
  }

  function _v2gInspectClose() {
    _v2gSelectedId = null;
    _v2gHighlightSubj = null;
    _v2gHighlightObj = null;
    _v2gOps.selectedEdgeIdx = -1;
    _v2gInspectAllEdges = false;
    _v2gInspectSet('', false);
    _v2gRepaint();
  }

  function _v2gInspectSet(bodyHtml, open) {
    var el = document.getElementById('v2g-inspect');
    if (!el) return null;
    if (!open) {
      el.classList.remove('is-open');
      el.hidden = true;
      el.innerHTML = '';
      return el;
    }
    el.hidden = false;
    el.classList.add('is-open');
    el.innerHTML =
      '<div class="v2g-inspect-head">' +
        '<button type="button" class="v2g-inspect-close" aria-label="' + _attr(_mt('memory.console.close', 'Close')) + '">×</button>' +
      '</div>' + (bodyHtml || '');
    var close = el.querySelector('.v2g-inspect-close');
    if (close) {
      close.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        _v2gInspectClose();
      });
    }
    return el;
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
    var pcolor = _v2gPredVar(ed.type) || 'var(--accent-light)';
    var pred = ed.fullLabel || ed.label || '';
    // One fact: its sentence first, in the page's own reading order, then
    // what kind of fact it is, then what can be done with it.
    var html = '<div class="mem-section-label" style="margin-bottom:6px;">' + _esc(_mt('memory.console.edge_belief', 'Fact')) + '</div>';
    html += '<div translate="no" dir="auto" style="font-size:0.85rem;line-height:1.5;margin-bottom:8px;color:var(--text-primary);">';
    html += '<bdi><b>' + _v2gEsc(_v2gDisplayName(A)) + '</b></bdi> ';
    html += '<bdi dir="ltr" style="color:' + pcolor + ';">' + _v2gEsc(String(pred).replace(/_/g, ' ')) + '</bdi> ';
    html += '<bdi><b>' + _v2gEsc(_v2gDisplayName(B)) + '</b></bdi>';
    html += '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.75rem;margin-bottom:10px;">';
    var edType = _mtData('memory.console.ptype_', ed.type || '?');
    html += '<span' + edType.attr + '>' + _v2gEsc(edType.text) + '</span>' +
      _v2gEsc(_mt('memory.console.edge_conf', ' · conf {n}%', { n: Math.round((ed.confidence || 0) * 100) }));
    if (ed.superseded) html += _v2gEsc(_mt('memory.console.superseded', ' · superseded'));
    if (!ed.beliefId) html += ' · ' + _v2gEsc(_mt('memory.console.edge_id_missing', 'id: (missing — unlink may fail)'));
    html += '</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px;">';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="edit">' + _v2gEsc(_mt('memory.console.edit', 'Edit')) + '</button>';
    html += '<button type="button" class="btn btn-sm btn-secondary v2g-edge-act" data-act="list" title="' + _v2gEsc(_mt('memory.console.act_in_list_title', 'Show in list')) + '">' + _v2gEsc(_mt('memory.console.in_list', 'In list')) + '</button>';
    html += '<button type="button" class="btn btn-sm btn-danger v2g-edge-act" data-act="unlink" title="' + _v2gEsc(_mt('memory.console.cut_this_edge', 'Cut this edge')) + '">' + _v2gEsc(_mt('memory.console.cut_this_edge', 'Remove this fact')) + '</button>';
    html += '</div>';
    // Stash edge triple on the panel for delegated clicks (survives re-renders better)
    el.setAttribute('data-edge-belief-id', ed.beliefId || '');
    el.setAttribute('data-edge-subject', A.id || '');
    el.setAttribute('data-edge-target', B.id || '');
    el.setAttribute('data-edge-predicate', pred || '');
    el.setAttribute('data-edge-object', ed.objectText || B.id || '');
    el = _v2gInspectSet(html, true);
    if (!el) return;
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
    // Two fingers on a phone: pinch to zoom, and move the map with them.
    var touches = {};
    var pinch = null;
    function touchCount() { return Object.keys(touches).length; }
    function pinchState() {
      var ids = Object.keys(touches);
      var a = touches[ids[0]], b = touches[ids[1]];
      return {
        d: Math.max(1, Math.sqrt((b.sx - a.sx) * (b.sx - a.sx) + (b.sy - a.sy) * (b.sy - a.sy))),
        mx: (a.sx + b.sx) / 2,
        my: (a.sy + b.sy) / 2,
      };
    }
    function liftTouch(ev) {
      delete touches[ev.pointerId];
      if (touchCount() < 2) pinch = null;
    }
    canvas.addEventListener('pointerdown', function(ev) {
      var c = evToCanvas(ev);
      if (ev.pointerType === 'touch') {
        touches[ev.pointerId] = c;
        if (touchCount() === 2) {
          var st = pinchState();
          pinch = { d0: st.d, scale0: _v2gView.scale, wx: _v2gWX(st.mx), wy: _v2gWY(st.my) };
          // The first finger's drag (a dot or the map) gives way to the pinch.
          _v2gDrag = null;
          _v2gUserView = true;
          return;
        }
      }
      var idx = _v2gHit(c.sx, c.sy);
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
        _v2gSelectedId = p.id; _v2gInspectAllEdges = false; _v2gInspect(p); canvas.setPointerCapture(ev.pointerId);
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
        _v2gInspectClose();
      }
      // Heat lightly so free nodes can settle; pinned nodes stay fixed.
      _v2gHeated(); _v2gRepaint();
    });
    canvas.addEventListener('pointermove', function(ev) {
      var c = evToCanvas(ev);
      if (ev.pointerType === 'touch' && touches[ev.pointerId]) touches[ev.pointerId] = c;
      if (pinch && touchCount() >= 2) {
        var ps = pinchState();
        var pscale = Math.max(_v2gMinScale, Math.min(_v2gMaxScale, pinch.scale0 * ps.d / pinch.d0));
        _v2gView.scale = pscale;
        _v2gView.ox = ps.mx - pinch.wx * pscale;
        _v2gView.oy = ps.my - pinch.wy * pscale;
        _v2gRepaint();
        return;
      }
      if (_v2gDrag) {
        if (_v2gDrag.pan) {
          _v2gView.ox = _v2gDrag.ox + (c.sx - _v2gDrag.sx);
          _v2gView.oy = _v2gDrag.oy + (c.sy - _v2gDrag.sy);
          _v2gUserView = true;
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
        if (_v2gIsPhone()) {
          var tipOff = document.getElementById('v2g-tooltip');
          if (tipOff) tipOff.style.display = 'none';
          return;
        }
        var tip = document.getElementById('v2g-tooltip');
        if (idx >= 0 && tip) {
          var p = _v2gPts[idx];
          var tc = _v2gNodeVar(p);
          var tLabel = _v2gTitle(_v2gDisplayName(p));
          var modeHint = '';
          if (_v2gOps.mode === 'link') modeHint = '<br><span style="color:var(--accent-light);">' + _v2gEsc(_mt('memory.console.tip_link_pick', 'link pick')) + '</span>';
          else if (_v2gOps.mode === 'merge') modeHint = '<br><span style="color:var(--warning);">' + _v2gEsc(_mt('memory.console.tip_merge_pick', 'merge pick')) + '</span>';
          // Name, then what it is and how many facts touch it, then what a
          // click does. The id ("id: ent_4f2a…") meant nothing to anyone.
          var kindText;
          if (_v2gIsUser(p)) kindText = _mt('memory.page.tip_you', 'You: everything else connects to you');
          else if (p.isEpisode) kindText = _mt('memory.page.legend_conversation', 'a conversation');
          else if (p.isValue || p.isVirtual) kindText = _mt('memory.page.legend_value', 'a value');
          else {
            kindText = _mt('memory.page.tip_kind', '{kind} · {facts}', {
              kind: _mtData('memory.console.etype_', p.type || 'entity').text,
              facts: window.kazmaCount('memory.page.facts_n', p.degree || 0),
            });
          }
          tip.innerHTML = '<b translate="no" dir="auto" style="display:block;color:' + tc + ';word-break:break-word;">' + _v2gEsc(tLabel) + '</b>' +
            '<span style="color:var(--text-secondary);">' + _v2gEsc(kindText) +
            (p.isHighStakes ? ' · ' + _v2gEsc(_mt('memory.console.tip_high_stakes', 'high-stakes')) : '') +
            '</span><br><span style="color:var(--text-muted);font-size:0.72rem;">' +
            _v2gEsc(_mt('memory.page.tip_click', 'Click to see its facts')) + '</span>' + modeHint;
          tip.style.display = 'block';
          tip.style.borderColor = '';
          var rect = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (eHover >= 0 && tip) {
          var edh = _v2gEdges[eHover];
          var Ah = _v2gPts[edh.a], Bh = _v2gPts[edh.b];
          // The fact as a sentence (who, the relation, what), each part
          // isolated so an Arabic name and an English relation keep their order.
          tip.innerHTML = '<span translate="no" dir="auto" style="display:block;color:var(--text-primary);">' +
            '<bdi><b>' + _v2gEsc(Ah ? _v2gDisplayName(Ah) : '?') + '</b></bdi> ' +
            '<bdi dir="ltr" style="color:var(--accent-light);">' + _v2gEsc((edh.fullLabel || edh.label || '').replace(/_/g, ' ')) + '</bdi> ' +
            '<bdi><b>' + _v2gEsc(Bh ? _v2gDisplayName(Bh) : '?') + '</b></bdi></span>' +
            (edh.superseded ? '<span style="color:var(--text-muted);">' + _v2gEsc(_mt('memory.page.legend_ended', 'a fact that ended')) + '</span><br>' : '') +
            '<span style="color:var(--text-muted);font-size:0.72rem;">' + _v2gEsc(_mt('memory.page.tip_line_click', 'Click to change or remove this fact')) + '</span>';
          tip.style.display = 'block';
          tip.style.borderColor = '';
          var rect2 = canvas.getBoundingClientRect();
          tip.style.left = Math.min(c.sx + 12, rect2.width - 200) + 'px';
          tip.style.top = (c.sy + 12) + 'px';
        } else if (tip) { tip.style.display = 'none'; }
      }
    });
    canvas.addEventListener('pointerup', function(ev) {
      if (ev.pointerType === 'touch') liftTouch(ev);
      // After a real drag, pin the node so physics / refresh cannot yank it back.
      if (_v2gDrag && !_v2gDrag.pan && _v2gDrag.moved && _v2gPts[_v2gDrag.idx]) {
        var placed = _v2gPts[_v2gDrag.idx];
        placed.pinned = true;
        placed.vx = 0; placed.vy = 0;
        _v2gRememberPos(placed);
      }
      _v2gDrag = null; canvas.style.cursor = 'grab'; _v2gRepaint();
    });
    canvas.addEventListener('pointercancel', function(ev) {
      if (ev.pointerType === 'touch') liftTouch(ev);
      _v2gDrag = null;
    });
    canvas.addEventListener('wheel', function(ev) {
      ev.preventDefault(); var c = evToCanvas(ev);
      _v2gZoomAt(ev.deltaY < 0 ? 1.15 : 1 / 1.15, c.sx, c.sy);
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
        return '<div class="mem-contents-line">' + html + '</div>';
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
    var phone = _v2gIsPhone();
    var color = _v2gNodeVar(p);
    var fullName = _v2gDisplayName(p);
    var title = _v2gTitle(fullName);
    var html = '<div translate="no" style="color:' + color + ';font-weight:700;font-size:0.82rem;word-break:break-word;margin-bottom:4px;padding-inline-end:28px;">' + _v2gEsc(title) + '</div>';
    html += '<div style="color:var(--text-muted);font-size:0.68rem;margin-bottom:6px;">';
    var pType = _mtData('memory.console.etype_', p.type);
    html += _v2gIsUser(p)
      ? _v2gEsc(_mt('memory.console.insp_you_hub', 'you · memory hub'))
      : _mtHtml('memory.console.insp_type', 'type: {type}', { type: '<span' + pType.attr + '>' + _v2gEsc(pType.text) + '</span>' });
    if (p.id) html += ' · ' + _v2gEsc(_mt('memory.console.insp_id', 'id:')) + ' <code style="font-size:0.65rem;">' + _v2gEsc(String(p.id)) + '</code>';
    if (p.isHighStakes) html += ' · <span style="color:var(--danger);display:inline-flex;align-items:center;gap:3px;">' + KazmaIcons.span('alert') + ' ' + _v2gEsc(_mt('memory.console.high_stakes', 'high-stakes')) + '</span>';
    if (p.isVirtual) html += _v2gEsc(_mt('memory.console.fact_node', ' · fact node'));
    html += '</div>';
    // Collect connections once for cut-hub + list UI
    var nodeEdges = _v2gEdgesForNode(p.id);
    var hubEdges = nodeEdges.filter(function(x) { return x.toHub; });
    var nonHubEdges = nodeEdges.filter(function(x) { return !x.toHub; });
    // Hub shortcut: leaf linked to hub AND to another node (should be leaf→parent→hub)
    var hubShortcut = !_v2gIsUser(p) && hubEdges.length > 0 && nonHubEdges.length > 0;

    // Node ops — phone keeps Link + Cut; extra actions sit behind More.
    // A value node is one fact's literal (id value:<belief>): there is no
    // entity to link, merge, rename or group, so it shows its edges only.
    if (!p.isEpisode && !p.isValue) {
      function _actBtn(act, cls, label, title) {
        return '<button type="button" class="btn btn-sm ' + cls + ' v2g-node-act" data-act="' + act +
          '" style="font-size:0.65rem;padding:2px 8px;" title="' + _v2gEsc(title) + '">' + _v2gEsc(label) + '</button>';
      }
      var extra = '';
      extra += _actBtn('src', 'btn-secondary', _mt('memory.console.act_src', 'Src'), _mt('memory.console.act_src_title', 'Set as link/merge source'));
      extra += _actBtn('tgt', 'btn-secondary', _mt('memory.console.act_tgt', 'Tgt'), _mt('memory.console.act_tgt_title', 'Set as link/merge target'));
      extra += _actBtn('merge-from', 'btn-secondary', _mt('memory.console.act_merge_from', 'Merge→'), _mt('memory.console.act_merge_from_title', 'Start merge from this node (will be retired)'));
      if (!_v2gIsUser(p)) {
        extra += _actBtn('group-under', 'btn-secondary', _mt('memory.console.act_group_under', 'Group under→'), _mt('memory.console.act_group_under_title', 'Group this node under a parent'));
        var grouped = (_v2gGroups || []).some(function(g) { return g.member === p.id; });
        if (grouped) {
          extra += _actBtn('set-tier', 'btn-secondary', _mt('memory.console.act_set_tier', 'Tier…'), _mt('memory.console.act_set_tier_title', "Set this node's tier in its group (0 = hub … 4)"));
          extra += _actBtn('ungroup', 'btn-secondary', _mt('memory.console.act_ungroup', 'Ungroup'), _mt('memory.console.act_ungroup_title', 'Remove view-only grouping for this node'));
        }
      }
      if (nodeEdges.length >= 1 && !_v2gIsUser(p)) {
        extra += _actBtn('cut-all', 'btn-secondary', _mt('memory.console.act_cut_all', 'Cut all ({n})', { n: nodeEdges.length }), _mt('memory.console.act_cut_all_title', 'Cut every edge on this node'));
      }
      if (!_v2gIsUser(p) && !p.isVirtual) {
        var isMaj = !!p.isMajor;
        extra += '<button type="button" class="btn btn-sm btn-outline v2g-node-act' + (isMaj ? ' mem-major-on' : '') + '" data-act="major" style="font-size:0.65rem;padding:2px 8px;' +
          '" title="' + _v2gEsc(_mt('memory.console.act_major_title', 'Mark as major')) + '">' +
          (isMaj ? KazmaIcons.span('star') + ' ' : '') + _v2gEsc(_mt('memory.console.act_major', 'Major')) + '</button>';
      }
      extra += _actBtn('rename', 'btn-secondary', _mt('memory.console.act_rename', 'Rename'), _mt('memory.console.act_rename_title', 'Change display name'));
      extra += _actBtn('list', 'btn-secondary', _mt('memory.console.in_list', 'In list'), _mt('memory.console.act_in_list_title', 'Highlight in entities list'));
      if (!_v2gIsUser(p) && !p.isVirtual) {
        extra += _actBtn('delete', 'btn-danger', _mt('memory.console.act_delete', 'Del'), _mt('memory.console.act_delete_title', 'Delete empty entity shell'));
      }
      html += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;">';
      html += _actBtn('link-from', 'btn-primary', _mt('memory.console.act_link_from', 'Link→'), _mt('memory.console.act_link_from_title', 'Start link from this node — click target next'));
      if (hubEdges.length && !_v2gIsUser(p)) {
        html += _actBtn('cut-hub', 'btn-danger', _mt('memory.console.act_cut_hub', 'Cut hub'), _mt('memory.console.act_cut_hub_title', 'Remove direct link(s) to the hub (you)'));
      }
      if (phone) {
        html += '<button type="button" class="btn btn-sm btn-secondary" id="v2g-inspect-more-btn" style="font-size:0.65rem;padding:2px 8px;">' + _v2gEsc(_mt('memory.console.more', 'More')) + '</button>';
        html += '</div><div id="v2g-inspect-extra" hidden style="display:flex;flex-wrap:wrap;gap:4px;margin:-4px 0 8px;">' + extra + '</div>';
      } else {
        html += extra + '</div>';
      }
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
      html += '<div class="notice tone-warning" style="margin-bottom:8px;font-size:0.7rem;line-height:1.4;">';
      html += _mt('memory.console.hub_shortcut', '<strong>Hub shortcut</strong> — direct link to hub while also linked to {parents}.', {
        parents: '<b translate="no">' + _v2gEsc(uniqParents.slice(0, 4).map(function(n) { return _v2gShortLabel(n, 30); }).join(', ')) + (uniqParents.length > 4 ? '…' : '') + '</b>',
      });
      html += '<div style="margin-top:4px;color:var(--text-secondary);font-size:0.65rem;">' + _mt('memory.console.hub_shortcut_hint', 'Preferred: this → parent → hub (you). Use <b>Cut hub</b> to drop the shortcut edge.') + '</div>';
      html += '<button type="button" class="btn btn-sm btn-danger-solid v2g-node-act" data-act="cut-hub" style="margin-top:6px;font-size:0.68rem;padding:3px 10px;">' +
        _v2gEsc(hubEdges.length > 1 ? _mt('memory.console.cut_hub_links', 'Cut hub links') : _mt('memory.console.cut_hub_link', 'Cut hub link')) + '</button>';
      html += '</div>';
    } else if (hubEdges.length && !_v2gIsUser(p) && nonHubEdges.length === 0) {
      html += '<div class="notice tone-neutral" style="margin-bottom:8px;font-size:0.68rem;">' + _mt('memory.console.hub_only', 'Linked only to hub. Use <b>Cut</b> on the connection row to detach.') + '</div>';
    }

    // Contents — the full text of this belief/entity, shown exactly once.
    if (fullName !== title) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">' + _v2gEsc(_mt('memory.console.contents', 'Contents')) + '</div>';
      html += '<div class="v2g-inspect-contents mem-well" translate="no" style="padding:6px 8px;font-size:0.72rem;color:var(--text-secondary);word-break:break-word;margin-bottom:8px;">' + _v2gContents(fullName) + '</div>';
    }
    // Connections — one Cut per neighbor (easy topology cleanup)
    if (nodeEdges.length) {
      html += '<div style="font-size:0.68rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:4px;">' + _v2gEsc(_mt('memory.console.connections', 'Connections ({n})', { n: nodeEdges.length })) + '</div>';
      html += '<div class="v2g-inspect-edges" style="display:flex;flex-direction:column;gap:3px;">';
      var edgeLimit = (phone && !_v2gInspectAllEdges) ? 4 : nodeEdges.length;
      for (var r = 0; r < Math.min(edgeLimit, nodeEdges.length); r++) {
        var row = nodeEdges[r];
        var ed = row.ed;
        var pcolor = _v2gPredVar(ed.type) || 'var(--accent-light)';
        var predLabel = _v2gEsc((ed.fullLabel || ed.label || '').replace(/_/g, ' '));
        var neighLabel = _v2gEsc(_v2gShortLabel(_v2gDisplayName(row.other), 30));
        var dir = row.outbound ? '→' : '←';
        var hubBadge = row.toHub
          ? ' <span class="badge badge-warning" style="font-size:0.58rem;padding:1px 5px;">' + _v2gEsc(_mt('memory.console.hub_badge', 'hub')) + '</span>'
          : '';
        var rowCls = row.toHub && hubShortcut ? ' is-hub-shortcut' : '';
        html += '<div class="v2g-belief-row' + rowCls + '" data-edge-idx="' + row.idx + '">';
        html += '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px;">';
        html += '<div style="min-width:0;flex:1;">';
        html += '<span style="color:var(--text-muted);">' + dir + '</span> <b translate="no">' + neighLabel + '</b>' + hubBadge;
        html += '<div style="font-size:0.62rem;color:var(--text-muted);margin-top:1px;"><span translate="no" style="color:' + pcolor + ';">' + predLabel + '</span>' +
          (ed.superseded ? _v2gEsc(_mt('memory.console.superseded', ' · superseded')) : '') + '</div>';
        html += '</div>';
        html += '<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;" onclick="event.stopPropagation()">';
        html += '<button type="button" class="btn btn-sm btn-danger v2g-rel-act" data-act="cut" data-edge-idx="' + row.idx + '" style="font-size:0.62rem;padding:2px 8px;" title="' + _v2gEsc(_mt('memory.console.cut_this_edge_only', 'Cut this edge only')) + '">' + _v2gEsc(_mt('memory.console.cut', 'Cut')) + '</button>';
        if (!phone) {
          html += '<button type="button" class="btn btn-sm btn-secondary v2g-rel-act" data-act="edit" data-edge-idx="' + row.idx + '" style="font-size:0.6rem;padding:1px 6px;">' + _v2gEsc(_mt('memory.console.edit', 'Edit')) + '</button>';
          html += '<button type="button" class="btn btn-sm btn-secondary v2g-rel-act" data-act="move" data-edge-idx="' + row.idx + '" style="font-size:0.6rem;padding:1px 6px;" title="' + _v2gEsc(_mt('memory.console.move_title_btn', 'Move this edge to another node (repoint)')) + '">' + _v2gEsc(_mt('memory.console.move', 'Move')) + '</button>';
        }
        html += '</div></div></div>';
      }
      if (phone && nodeEdges.length > 4 && !_v2gInspectAllEdges) {
        html += '<button type="button" class="btn btn-sm btn-secondary" id="v2g-inspect-more-edges" style="font-size:0.65rem;padding:3px 8px;margin-top:2px;">' + _v2gEsc(_mt('memory.console.more_edges', '+{n} more', { n: nodeEdges.length - 4 })) + '</button>';
      }
      html += '</div>';
    } else {
      html += '<div style="color:var(--text-muted);font-size:0.7rem;">' + _v2gEsc(_mt('memory.console.no_direct_beliefs', 'No direct beliefs — use Link→ to connect it.')) + '</div>';
    }
    el = _v2gInspectSet(html, true);
    if (!el) return;
    var moreBtn = el.querySelector('#v2g-inspect-more-btn');
    if (moreBtn) {
      moreBtn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var extraBox = el.querySelector('#v2g-inspect-extra');
        if (extraBox) extraBox.hidden = !extraBox.hidden;
        moreBtn.textContent = extraBox && !extraBox.hidden ? _mt('memory.console.less', 'Less') : _mt('memory.console.more', 'More');
      });
    }
    var moreEdges = el.querySelector('#v2g-inspect-more-edges');
    if (moreEdges) {
      moreEdges.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        _v2gInspectAllEdges = true;
        _v2gInspect(p);
      });
    }

    el.querySelectorAll('.v2g-node-act').forEach(function(btn) {
      btn.addEventListener('click', function(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var act = btn.getAttribute('data-act');
        if (act === 'src') {
          _v2gSetSlot('source', p.id);
          _v2gToast(_mt('memory.console.toast_source', 'Source = {id}', { id: _v2gShortId(p.id) }), 'info');
        } else if (act === 'tgt') {
          _v2gSetSlot('target', p.id);
          _v2gToast(_mt('memory.console.toast_target', 'Target = {id}', { id: _v2gShortId(p.id) }), 'info');
        } else if (act === 'link-from') {
          _v2gOps.sourceId = p.id;
          _v2gOps.targetId = null;
          _v2gOps.mode = 'link';
          _v2gBroadcastSlots();
          _v2gToast(_mt('memory.console.toast_link_from', 'Link from {id} — click target on graph', { id: _v2gShortId(p.id) }), 'info');
        } else if (act === 'merge-from') {
          _v2gOps.sourceId = p.id;
          _v2gOps.targetId = null;
          _v2gOps.mode = 'merge';
          _v2gBroadcastSlots();
          _v2gToast(_mt('memory.console.toast_merge_from', 'Merge from {id} — click survivor target', { id: _v2gShortId(p.id) }), 'info');
        } else if (act === 'group-under') {
          // F: view-only grouping — pick the parent next.
          _v2gGroupUnder(p.id);
        } else if (act === 'set-tier') {
          _v2gSetTier(p.id);
        } else if (act === 'ungroup') {
          _v2gUngroup(p.id);
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
              title: _mt('memory.console.cut_all_title', 'Cut all connections'),
              message: window.kazmaCount('memory.console.cut_all_message', all.length, {
                name: _v2gDisplayName(p),
              }),
              confirmText: _mt('memory.console.cut_all', 'Cut all'),
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
        el.innerHTML = '<div style="color:var(--danger);font-size:0.75rem;">' +
          _v2gEsc(_mt('memory.console.inspect_error', 'Inspect error: {error}', { error: String(inspectErr && inspectErr.message ? inspectErr.message : inspectErr) })) +
          '</div>';
      } catch (e2) { /* ignore */ }
    }
  }

  async function _v2gRenameNode(p) {
    if (!p || p.isEpisode) return;
    var current = _v2gDisplayName(p);
    var msg = _mt('memory.console.rename_message', 'Display name for this node. The id stays "{id}" so all beliefs keep linking correctly.', { id: String(p.id) });
    var name;
    if (window.kazmaPrompt) {
      name = await window.kazmaPrompt({
        title: _mt('memory.console.rename_title', 'Rename node'),
        message: msg,
        defaultValue: current,
        confirmText: _mt('memory.console.rename', 'Rename'),
        placeholder: _v2gIsUser(p) ? _mt('memory.console.rename_ph_user', 'e.g. your name') : _mt('memory.console.rename_ph', 'e.g. a project name'),
      });
    } else {
      name = window.prompt(msg, current);
    }
    if (name == null) return;
    name = String(name).trim();
    if (!name) {
      if (window.showToast) window.showToast(_mt('memory.console.name_empty', 'Name cannot be empty'), 'error');
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
        if (window.showToast) window.showToast(_errText('memory.console.rename_failed', 'Rename failed', data), 'error');
        return;
      }
      if (window.showToast) window.showToast(_mt('memory.console.renamed_to', 'Renamed to “{name}”', { name: name }), 'success');
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
      if (window.showToast) window.showToast(_mt('memory.console.rename_failed', 'Rename failed'), 'error');
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
    if (stats && stats.truncated && stats.total_nodes > (stats.nodes || 0)) {
      var msg = window.kazmaCount('dashboard.memory_graph_truncated', stats.total_nodes, { shown: stats.nodes || 0 });
      // M-08 honesty: slicing also amputates edges — surface the delta so
      // operators know missing connections are a view limit, not lost data.
      var hiddenEdges = 0;
      if (stats.total_links != null && stats.links != null) {
        hiddenEdges = Math.max(0, Number(stats.total_links) - Number(stats.links));
      }
      if (hiddenEdges > 0) {
        msg += ' · ' + _mt('memory.console.hidden_connections', 'connections hidden: {n}', { n: hiddenEdges });
      }
      el.textContent = _mt('memory.console.graph_trunc_notice', '{summary} — filter to narrow the view.', { summary: msg });
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
    var base = _mt('memory.page.map_aria', 'Memory map. Arrow keys move it, plus and minus zoom, Home shows everything. Click a line to change or remove its fact.');
    canvas.setAttribute('aria-label', base + ' ' +
      _mt('memory.console.aria_showing', 'Currently showing {nodes} and {links}.', {
        nodes: window.kazmaCount('memory.page.things_n', nodes),
        links: window.kazmaCount('memory.page.facts_n', links),
      }) +
      (focus ? ' ' + _mt('memory.console.aria_focused', 'Focused on {id}.', { id: focus }) : ''));
  }

  // The map's own state over the canvas: loading (first load only), failed
  // (nothing drawn yet: never shown as an empty memory) or neither.
  function _v2gShowState(state) {
    var loading = document.getElementById('v2g-loading');
    var failed = document.getElementById('v2g-failed');
    if (loading) loading.style.display = state === 'loading' ? 'flex' : 'none';
    if (failed) failed.style.display = state === 'failed' ? 'flex' : 'none';
    if (state === 'failed') {
      var empty = document.getElementById('v2g-empty');
      if (empty) empty.style.display = 'none';
    }
  }

  var _v2gEverLoaded = false;

  // A failed load: before anything was drawn, the map says it failed; after,
  // the map keeps what it shows and the line under it says it is not current
  // (once: the map is reloaded every 30 s).
  function _v2gLoadFailed() {
    if (!_v2gEverLoaded) { _v2gShowState('failed'); return; }
    _v2gShowState(null);
    var sl = document.getElementById('v2g-stats-line');
    if (sl && !sl.getAttribute('data-stale')) {
      sl.setAttribute('data-stale', '1');
      sl.textContent += ' · ' + _mt('memory.page.map_stale', 'could not refresh: this is what was last loaded');
    }
  }

  async function _v2gLoad() {
    if (!_v2gEverLoaded) _v2gShowState('loading');
    try {
      var resp = await fetch(_v2gBuildUrl());
      var data = null;
      try { data = await resp.json(); } catch (eJson) { data = null; }
      // The route answers 200 with `error` and no dots when it cannot read
      // the memory: that is a failure, not an empty map.
      if (!resp.ok || !data || data.error) {
        _v2gLoadFailed();
        return;
      }
      _v2gEverLoaded = true;
      _v2gShowState(null);
      var stats = data.stats || {};
      _v2gLastStats = stats;
      _v2gRawNodes = data.nodes || [];
      _v2gRawLinks = data.links || [];
      // M-17: graph payload already embeds groups — do not double-fetch
      // GET /graph/groups on the 30s poll (POST create/DELETE ungroup remain).
      _v2gGroups = Array.isArray(data.groups) ? data.groups : [];
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
    } catch (e) {
      // A network failure is said like any other (it was swallowed, and the
      // map sat empty).
      _v2gLoadFailed();
    }
  }

  // thingsOnly (the legend): a fact's value and a conversation have legend
  // entries of their own, and counted as "idea" they made its count wrong.
  // The filter counts every dot its checkbox filters.
  function _v2gTypeCountsFromData(thingsOnly) {
    var counts = {};
    (_v2gRawNodes || []).forEach(function(n) {
      if (!n) return;
      if (thingsOnly && (n.isValue || n.isVirtual || n.isEpisode)) return;
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

  // What the map's marks mean: only the ones on the map now -- you, each kind
  // of thing with how many, a fact (a line), a fact that ended (dashed), a
  // value (a hollow dot), a conversation (dashed hollow). It listed every
  // kind's dot and ended with "· site accent", which explained nothing.
  function _v2gUpdateLegend(entCounts) {
    var leg = document.getElementById('v2g-legend');
    if (!leg) return;
    var raw = _v2gRawNodes || [];
    var hasUser = raw.some(function(n) { return n && (n.isHub || String(n.id || '').toLowerCase() === 'user'); });
    var hasValue = raw.some(function(n) { return n && (n.isValue || n.isVirtual) && !n.isEpisode; });
    var hasEpisode = raw.some(function(n) { return n && n.isEpisode; });
    var hasEnded = (_v2gRawLinks || []).some(function(l) { return l && l.superseded; });
    function item(mark, text, count) {
      return '<span class="mem-legend-item">' + mark + '<span>' + _esc(text) + '</span>' +
        (count ? '<span class="mem-legend-count">' + _esc(window.KazmaFormat ? window.KazmaFormat.number(count) : String(count)) + '</span>' : '') +
        '</span>';
    }
    // *classes*: a list of class names (never one string of several: a
    // string of words handed to an HTML builder reads as English text).
    function dot(token, classes) {
      var extra = (classes || []).join(' ');
      return '<span class="mem-legend-dot' + (extra ? ' ' + extra : '') + '" aria-hidden="true" style="--mem-c:' + _v2gTokenVar(token) + ';"></span>';
    }
    var parts = [];
    if (hasUser) parts.push(item(dot(_V2G_USER_TOKEN), _mt('memory.page.legend_you', 'You')));
    var order = ['person', 'organization', 'location', 'project', 'tool', 'event', 'concept', 'entity'];
    var keys = order.filter(function(k) { return (entCounts[k] || 0) > 0; });
    Object.keys(entCounts || {}).forEach(function(k) {
      if (keys.indexOf(k) < 0 && entCounts[k] > 0 && k !== 'episode') keys.push(k);
    });
    keys.forEach(function(k) {
      var kl = _mtData('memory.console.etype_', k);
      parts.push(item(dot(_V2G_TYPE_TOKENS[k] || '--accent'), kl.text, entCounts[k] || 0));
    });
    parts.push(item('<span class="mem-legend-line" aria-hidden="true"></span>', _mt('memory.page.legend_line', 'a fact')));
    if (hasEnded) parts.push(item('<span class="mem-legend-line is-ended" aria-hidden="true"></span>', _mt('memory.page.legend_ended', 'a fact that ended')));
    if (hasValue) parts.push(item(dot('--accent', ['is-hollow']), _mt('memory.page.legend_value', 'a value')));
    if (hasEpisode) parts.push(item(dot('--secondary', ['is-hollow', 'is-dashed']), _mt('memory.page.legend_conversation', 'a conversation')));
    leg.innerHTML = parts.join('');
  }

  function _v2gApplyFilters() {
    // Apply client-side entity-type + predicate-type + search filters
    var activeEnt = Object.keys(_v2gFilters.entity);
    var activePred = Object.keys(_v2gFilters.predicate);
    var search = '';
    var searchEl = document.getElementById('v2g-search');
    // Folded, both sides: an Arabic name in any usual spelling.
    if (searchEl) search = window.kazmaSearchFold(searchEl.value);

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
          var hay = window.kazmaSearchFold((l.label || '') + ' ' + (l.source || '') + ' ' + (l.target || '') + ' ' + (l.object_text || ''));
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
        var nm = window.kazmaSearchFold(n.name || n.label || n.id || '');
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
      // What is on the map, in the page's words. Where the map was read
      // from ("paint sqlite", "neo4j dual-write online") is the health
      // panel's to say, not a line under the map.
      var parts = [_mt('memory.page.stats', '{things} · {facts}', {
        things: window.kazmaCount('memory.page.things_n', nodes.length),
        facts: window.kazmaCount('memory.page.facts_n', links.length),
      })];
      if (isolatedCount > 0) parts.push(window.kazmaCount('memory.page.unconnected_n', isolatedCount));
      if (activeEnt.length || activePred.length || search) {
        parts.push(_mt('memory.page.stats_filtered', 'filtered, out of {all}', {
          all: window.kazmaCount('memory.page.things_n', _v2gRawNodes.length),
        }));
      } else if (_v2gRawNodes.length > _LOD_MAX) {
        parts.push(_mt('memory.page.stats_capped', 'the first {shown} of {all} are drawn', {
          shown: window.KazmaFormat ? window.KazmaFormat.number(_LOD_MAX) : _LOD_MAX,
          all: window.kazmaCount('memory.page.things_n', _v2gRawNodes.length),
        }));
      }
      sl.textContent = parts.join(' · ');
      sl.removeAttribute('data-stale');
    }
    // Nothing to draw: say whether the memory is empty or the filter hid it.
    var emptyText = document.getElementById('v2g-empty-text');
    var emptyTeach = document.getElementById('v2g-empty-teach');
    var timeSlider = document.getElementById('v2g-time-slider');
    var filtered = (!!(activeEnt.length || activePred.length || search) && _v2gRawNodes.length > 0) ||
      !!(timeSlider && parseFloat(timeSlider.value) < 100);
    if (emptyText) {
      emptyText.textContent = emptyText.getAttribute(filtered ? 'data-filtered' : 'data-none') || '';
    }
    if (emptyTeach) emptyTeach.style.display = filtered ? 'none' : '';
    _v2gUpdateLegend(_v2gTypeCountsFromData(true));
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
      var esc = _esc(String(p));
      // .v2g-chip / .chip-sel are styled in memory_console.html (tokens).
      return '<button type="button" class="v2g-chip ' + active + '" data-pred="' + esc + '">'
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
        hintEl.textContent = _mt('memory.console.reuse_predicate', 'Reusing existing predicate.');
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
        hintEl.innerHTML = _mtHtml('memory.console.similar_predicate', 'Similar to {name}? Click to reuse.', {
          name: '<b translate="no" style="color:var(--accent-light);cursor:pointer;" data-adopt="' + _attr(name) + '">' + _esc(name) + '</b>',
        });
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
    function makeToggle(value, group, key, color, count) {
      var id = 'v2g-ft-' + group + '-' + key;
      var active = !!_v2gFilters[group][key];
      var lbl = _mtData(group === 'entity' ? 'memory.console.etype_' : 'memory.console.ptype_', value);
      var label = _esc(lbl.text);
      var cnt = (count != null && count > 0) ? ' <span style="font-family:var(--font-mono);">' + count + '</span>' : '';
      // The pill is styled in memory_console.html; *color* is a token (var()).
      return '<label' + lbl.attr + (active ? ' class="is-on"' : '') + ' title="' + _attr(lbl.text) + (count != null ? ': ' + count : '') + '" style="--mem-c:' + color + ';">' +
             '<span class="mem-filter-dot"></span>' +
             '<input type="checkbox" id="' + _attr(id) + '" ' + (active ? 'checked' : '') + ' style="display:none;">' + label + cnt + '</label>';
    }
    if (entBox) entBox.innerHTML = entTypes.map(function(t) {
      return makeToggle(t, 'entity', t, _v2gTypeVar(t), entCounts[t] || 0);
    }).join('');
    if (predBox) predBox.innerHTML = predTypes.map(function(t) {
      return makeToggle(t, 'predicate', t, _v2gPredVar(t) || 'var(--text-muted)', predCounts[t] || 0);
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
      var all = Object.keys(_v2gFilters.entity).map(function(k) {
        return { group: 'entity', key: k, label: _esc(_mt('memory.console.filter_entity', 'entity:{type}', { type: _mtData('memory.console.etype_', k).text })) };
      }).concat(Object.keys(_v2gFilters.predicate).map(function(k) {
        return { group: 'predicate', key: k, label: _esc(_mt('memory.console.filter_pred', 'pred:{type}', { type: _mtData('memory.console.ptype_', k).text })) };
      }));
      // Buttons, so a keyboard reaches them; each says what pressing it does.
      var html = all.map(function(c) {
        return '<button type="button" data-fg="' + _attr(c.group) + '" data-fk="' + _attr(c.key) + '" class="badge badge-accent mem-filter-chip" ' +
          'title="' + _attr(_mt('memory.page.filter_remove', 'Remove this filter')) + '" ' +
          'style="font-size:0.72rem;padding:3px 8px;display:inline-flex;align-items:center;gap:4px;border:0;cursor:pointer;">' +
          c.label + ' ' + KazmaIcons.span('x') + '</button>';
      }).join('');
      if (all.length) html += '<button type="button" id="v2g-reset-filters" class="btn btn-ghost btn-xs">' + _esc(_mt('memory.page.filter_reset', 'Clear filters')) + '</button>';
      chips.innerHTML = html;
      chips.querySelectorAll('[data-fg]').forEach(function(span) {
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
    if (playBtn) playBtn.textContent = playBtn.getAttribute('data-play-label') || _mt('dashboard.memory_play', 'Play');
  }

  var _v2gPathIds = {};
  var _v2gEpisodeNodes = [];
  var _v2gShowEpisodes = false;

  function _v2gApplyPathFromQuery() {
    var seeds = (_v2gLastQuerySeeds || []).map(function(s) { return window.kazmaSearchFold(s); });
    if (!seeds.length) {
      var q = ((document.getElementById('v2g-search') || {}).value || (document.getElementById('v2-probe-input') || {}).value || '').trim();
      if (q) seeds = window.kazmaSearchFold(q).split(/\s+/).filter(function(w) { return w.length > 2; });
    }
    _v2gPathIds = {};
    var matched = 0;
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var hay = window.kazmaSearchFold((p.id || '') + ' ' + (p.fullLabel || '') + ' ' + (p.label || ''));
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
      window.showToast(matched
        ? window.kazmaCount('memory.console.path_highlighted', matched)
        : _mt('memory.console.path_no_match', 'No matching nodes for the query path'), matched ? 'success' : 'info');
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
      // The canvas is transparent; the export gets the theme's well behind
      // it, so its labels (this theme's ink) stay readable on any viewer.
      var out = document.createElement('canvas');
      out.width = canvas.width; out.height = canvas.height;
      var octx = out.getContext('2d');
      var theme = _v2gTheme();
      octx.fillStyle = theme.well || theme.card;
      octx.fillRect(0, 0, out.width, out.height);
      octx.drawImage(canvas, 0, 0);
      var a = document.createElement('a');
      a.download = 'kazma-v2-topology.png';
      a.href = out.toDataURL('image/png');
      a.click();
      if (window.showToast) window.showToast(_mt('memory.console.png_downloaded', 'PNG downloaded'), 'success');
    } catch (e) {
      if (window.showToast) window.showToast(_mt('memory.console.png_export_failed', 'PNG export failed'), 'error');
    }
  }

  function _v2gExportSvg() {
    var W = 800, H = 500;
    var size = _v2gCanvasSize();
    if (size) { W = size.w; H = size.h; }
    // The theme on screen: its well behind, its accent on edges, its ink on
    // labels (the export used to be always dark).
    var theme = _v2gTheme();
    var parts = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '">',
      '<rect width="100%" height="100%" fill="' + (theme.well || theme.card) + '"/>'];
    for (var e = 0; e < _v2gEdges.length; e++) {
      var ed = _v2gEdges[e], A = _v2gPts[ed.a], B = _v2gPts[ed.b];
      if (!A || !B) continue;
      parts.push('<line x1="' + _v2gSX(A.x).toFixed(1) + '" y1="' + _v2gSY(A.y).toFixed(1) +
        '" x2="' + _v2gSX(B.x).toFixed(1) + '" y2="' + _v2gSY(B.y).toFixed(1) +
        '" stroke="' + theme.accent + '" stroke-opacity="0.45" stroke-width="1.5"/>');
    }
    for (var i = 0; i < _v2gPts.length; i++) {
      var p = _v2gPts[i];
      var col = _v2gNodeColor(p);
      parts.push('<circle cx="' + _v2gSX(p.x).toFixed(1) + '" cy="' + _v2gSY(p.y).toFixed(1) +
        '" r="' + p.r + '" fill="' + col + '" fill-opacity="0.9"/>');
      parts.push('<text x="' + _v2gSX(p.x).toFixed(1) + '" y="' + (_v2gSY(p.y) + p.r + 12).toFixed(1) +
        '" fill="' + theme.ink + '" font-size="11" text-anchor="middle" font-family="sans-serif">' +
        _v2gEsc(p.label || p.id).replace(/&/g, '&amp;') + '</text>');
    }
    parts.push('</svg>');
    var blob = new Blob([parts.join('')], { type: 'image/svg+xml' });
    var a = document.createElement('a');
    a.download = 'kazma-v2-topology.svg';
    a.href = URL.createObjectURL(blob);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(a.href); }, 2000);
    if (window.showToast) window.showToast(_mt('memory.console.svg_downloaded', 'SVG downloaded'), 'success');
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
          _v2gToast(_mt('memory.console.link_mode_cancelled', 'Link mode cancelled'), 'info');
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
          _v2gToast(_mt('memory.console.merge_mode_cancelled', 'Merge mode cancelled'), 'info');
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
        _v2gToast(_mt('memory.console.slots_cleared', 'Slots cleared'), 'info');
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
    _memOnWin('kazma:memory-ops-slots', function(ev) {
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
    // The map's own buttons: zoom around the centre, and show everything.
    var zoomIn = document.getElementById('v2g-zoom-in');
    if (zoomIn) zoomIn.addEventListener('click', function() { _v2gZoomAt(1.25); });
    var zoomOut = document.getElementById('v2g-zoom-out');
    if (zoomOut) zoomOut.addEventListener('click', function() { _v2gZoomAt(1 / 1.25); });
    var fitBtn = document.getElementById('v2g-fit');
    if (fitBtn) fitBtn.addEventListener('click', function() { _v2gFitView(); });
    var retryBtn = document.getElementById('v2g-retry');
    if (retryBtn) retryBtn.addEventListener('click', function() { _v2gLoad(); });
    // The warning above the map opens the health panel it points to.
    document.querySelectorAll('.mem-open-health').forEach(function(a) {
      a.addEventListener('click', function(ev) {
        var health = document.getElementById('memory-health');
        if (!health) return;
        ev.preventDefault();
        health.open = true;
        health.scrollIntoView({ behavior: _v2gPreferReducedMotion ? 'auto' : 'smooth', block: 'start' });
      });
    });
    // Keyboard a11y: pan / zoom / reset
    var canvas = document.getElementById('v2g-canvas');
    if (canvas && !canvas._v2gKeys) {
      canvas._v2gKeys = true;
      canvas.addEventListener('keydown', function(ev) {
        var step = 28;
        var handled = true;
        if (ev.key === 'ArrowLeft') { _v2gView.ox += step; _v2gUserView = true; }
        else if (ev.key === 'ArrowRight') { _v2gView.ox -= step; _v2gUserView = true; }
        else if (ev.key === 'ArrowUp') { _v2gView.oy += step; _v2gUserView = true; }
        else if (ev.key === 'ArrowDown') { _v2gView.oy -= step; _v2gUserView = true; }
        else if (ev.key === '+' || ev.key === '=') {
          _v2gZoomAt(1.15);
        } else if (ev.key === '-' || ev.key === '_') {
          _v2gZoomAt(1 / 1.15);
        } else if (ev.key === 'Home') {
          // Show everything (it set the view to 1:1 at the canvas's corner,
          // which could put the whole map out of sight).
          _v2gFitView();
        } else if (ev.key === 'Escape') {
          _v2gPathIds = {};
          _v2gOps.mode = null;
          _v2gInspectClose();
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
    if (playBtn) playBtn.setAttribute('data-play-label', playBtn.textContent || _mt('dashboard.memory_play', 'Play'));
    function _updateTimeLabel() {
      if (!slider || !label) return;
      var v = parseFloat(slider.value);
      if (v >= 99.5) label.textContent = _mt('dashboard.memory_live_now', 'Live (now)');
      else if (_v2gTimeRange.max > 0) {
        var ts = _v2gTimeRange.min + (v / 100) * (_v2gTimeRange.max - _v2gTimeRange.min);
        label.textContent = _memDay(ts);
      } else label.textContent = '—';
    }
    if (slider) {
      slider.addEventListener('input', _updateTimeLabel);
      slider.addEventListener('change', function() { _v2gStopPlay(); _v2gLoad(); });
    }
    if (liveBtn) liveBtn.addEventListener('click', function() {
      _v2gStopPlay();
      if (slider) slider.value = 100;
      if (label) label.textContent = _mt('dashboard.memory_live_now', 'Live (now)');
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
        playBtn.textContent = playBtn.getAttribute('data-pause-label') || _mt('dashboard.memory_pause', 'Pause');
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
    _memOnWin('resize', function() { clearTimeout(rto); rto = setTimeout(function() { if (_v2gPts.length) _v2gRepaint(); }, 200); });
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
      if (_v2gPts[i].id === id) {
        return { x: _v2gPts[i].x, y: _v2gPts[i].y, tier: _v2gPts[i].tier, groupParent: _v2gPts[i].groupParent,
                 rank: _v2gPts[i].rank, r: _v2gPts[i].r, ringR: _v2gPts[i].ringR };
      }
    }
    return null;
  };
  // The radial hierarchy's rings and who sits where (for tests).
  window._v2gGetRings = function() {
    return { r2: _V2G_RING.r2, r3: _V2G_RING.r3, lanes: _V2G_RING.lanes,
             hub: _V2G_RING.hub >= 0 && _v2gPts[_V2G_RING.hub] ? _v2gPts[_V2G_RING.hub].id : null,
             majors: _V2G_RING.majors.map(function(k) { return _v2gPts[k] && _v2gPts[k].id; }) };
  };
  // The view (scale and offset) and the drawn dots' screen boxes, for tests:
  // zoom, Show all and label placement are read from here.
  window._v2gGetView = function() {
    var wrap = document.getElementById('v2g-canvas-wrap');
    var rect = wrap ? wrap.getBoundingClientRect() : { width: 0, height: 0 };
    var inView = 0;
    _v2gPts.forEach(function(p) {
      var x = _v2gSX(p.x), y = _v2gSY(p.y);
      if (x >= 0 && y >= 0 && x <= rect.width && y <= rect.height) inView++;
    });
    return { scale: _v2gView.scale, ox: _v2gView.ox, oy: _v2gView.oy, dots: _v2gPts.length,
             inView: inView, settled: _v2gAlpha === 0 };
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
  window._v2gUngroup = _v2gUngroup;
  window._v2gToggleMajor = _v2gToggleMajor;

  try {
    _v2gRenderFilters();
    _v2gWireControls();
    _v2gLoad();
    _memEvery(30000, function() { if (!document.hidden) _v2gLoad(); });
    // A theme or accent change repaints the canvas in the new tokens. The
    // shell announces it (kazma:themechange on document); a change of
    // <html data-theme> or its inline accent seeds is watched too, so a
    // switch the event does not cover still repaints. One repaint a frame.
    var _v2gThemeQueued = false;
    function _v2gOnTheme() {
      if (_v2gThemeQueued) return;
      _v2gThemeQueued = true;
      requestAnimationFrame(function() { _v2gThemeQueued = false; _v2gThemeChanged(); });
    }
    document.addEventListener('kazma:themechange', _v2gOnTheme);
    var _v2gThemeObserver = null;
    if (window.MutationObserver) {
      _v2gThemeObserver = new MutationObserver(_v2gOnTheme);
      _v2gThemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
    }
    var _prevCleanup = window.__kazmaMemConsoleCleanup;
    window.__kazmaMemConsoleCleanup = function () {
      if (_v2gAnim) { try { cancelAnimationFrame(_v2gAnim); } catch (e2) { /* ignore */ } _v2gAnim = null; }
      if (_v2gPlayTimer) { clearInterval(_v2gPlayTimer); _v2gPlayTimer = null; }
      document.removeEventListener('kazma:themechange', _v2gOnTheme);
      if (_v2gThemeObserver) { _v2gThemeObserver.disconnect(); _v2gThemeObserver = null; }
      if (typeof _prevCleanup === 'function') _prevCleanup();
    };
  } catch (e) { /* V2 graph optional */ }
})();
