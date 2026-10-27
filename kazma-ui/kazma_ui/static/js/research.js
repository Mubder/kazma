/* Research Results panel — vanilla IIFE (mirrors replay.js / swarm.js).
 * Exposes window.KazmaResearch with:
 *   init()                — bootstrap on page load
 *   switchTab(name)       — tab switcher
 *   load()                — GET tasks/papers/sessions → render cards
 *   startDeep()           — POST /api/research/sessions + SSE progress
 *   search(event)         — filter by search text
 *   viewDetail(id)        — GET /api/research/tasks/{id} → detail
 *   exportCurrent(fmt)    — POST /api/research/{id}/export
 *   compare()             — POST /api/research/compare → diff
 */
(function () {
  'use strict';

  var allTasks = [];
  var archivedTasks = [];
  var currentId = null;
  var pollTimer = null;
  var liveSource = null;
  var liveSessionId = null;
  var liveVersionId = null;
  var liveUpdatedAt = 0;

  // Sessions live in research_sessions.db, tasks in the swarm TaskStore —
  // route mutations by id prefix so delete/archive work for BOTH.
  function researchDeleteUrl(id) {
    var s = String(id);
    return s.indexOf('session:') === 0
      ? '/api/research/sessions/' + encodeURIComponent(s.slice('session:'.length))
      : '/api/research/tasks/' + encodeURIComponent(s);
  }

  function researchArchiveUrl(id, action) {
    var s = String(id);
    return s.indexOf('session:') === 0
      ? '/api/research/sessions/' + encodeURIComponent(s.slice('session:'.length)) + '/' + action
      : '/api/research/tasks/' + encodeURIComponent(s) + '/' + action;
  }

  function $(id) { return document.getElementById(id); }
  function warning(id, visible, key) {
    var el = $(id);
    if (el) { el.textContent = visible ? i18n(key) : ''; el.style.display = visible ? 'block' : 'none'; }
  }
  function readResults(url, field) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('Unavailable');
      return r.json();
    }).then(function (data) {
      if (!data || !Array.isArray(data[field])) throw new Error('Invalid results');
      return data;
    }).catch(function () { var data = { error: true }; data[field] = []; return data; });
  }

  // SVG icons (no emojis — consistent with the rest of the Kazma UI).
  var ARCHIVE_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><rect x="2" y="4" width="20" height="4" rx="1"/><path d="M4 8v10a2 2 0 002 2h12a2 2 0 002-2V8"/><line x1="10" y1="12" x2="14" y2="12"/></svg>';
  var RESTORE_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M3 12a9 9 0 109-9"/><polyline points="3 4 3 10 9 10"/></svg>';
  var CHECK_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
  // Text for *key* in the page's language: a flat string research.html
  // merged into KAZMA_I18N (already translated), or a catalog entry
  // ({en, ar} -- never shown as it is), else *fallback*, with {name}
  // placeholders filled from *vars*.
  function i18n(key, fallback, vars) {
    var v = window.KAZMA_I18N ? window.KAZMA_I18N[key] : null;
    var text;
    if (typeof v === 'string' && v) text = v;
    else if (v && typeof v === 'object') text = v[window.KAZMA_LANG || 'en'] || v.en;
    if (!text) text = fallback || key;
    if (vars) for (var k in vars) text = text.split('{' + k + '}').join(String(vars[k]));
    return text;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toast(msg, type) {
    if (window.KazmaStream && KazmaStream.toast) KazmaStream.toast(msg, type || 'info', 3000);
  }
  // "Delete this result?" in the page's language, through the Kazma dialog
  // (it asked in English, through the browser's own confirm()).
  function confirmDelete() {
    var question = i18n('research.ui.delete_confirm', 'Delete this research result?');
    if (typeof window.kazmaConfirm === 'function') {
      return window.kazmaConfirm({
        message: question,
        confirmText: i18n('research.ui.delete', 'Delete'),
        danger: true,
      });
    }
    return Promise.resolve(window.confirm(question));
  }
  function timeAgo(iso) {
    if (!iso) return '—';
    try {
      // An ISO string, or epoch seconds (the sessions API).
      var d = typeof iso === 'number' ? new Date(iso < 1e12 ? iso * 1000 : iso) : new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      if (window.KazmaFormat && (window.KAZMA_LANG || 'en') !== 'en') {
        return (Date.now() - d.getTime()) < 86400000
          ? window.KazmaFormat.relative(d)
          : window.KazmaFormat.date(d);
      }
      var s = Math.floor((Date.now() - d.getTime()) / 1000);
      if (s < 60) return s + 's ago';
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      return d.toLocaleDateString();
    } catch (e) { return iso; }
  }

  function _registerSoftNavTeardown() {
    if (typeof window === 'undefined') return;
    var teardown = function () {
      if (window.KazmaResearch && typeof window.KazmaResearch.destroy === 'function') {
        window.KazmaResearch.destroy();
      }
    };
    window.kazmaAddLeaveHook(teardown);
  }

  // The delegated click listener is bound to this controller and removed on
  // leaving the page (destroy). The guard below lives in this run's closure,
  // and soft navigation runs the file again on every visit: each visit added
  // a listener, so Archive and Resume POSTed twice and Delete opened two
  // confirms (audit 2026-10-26 AUD-030).
  var _delegatedListeners = null;
  var _delegatedBound = false;
  function _bindDelegatedEvents() {
    if (_delegatedBound) return;
    _delegatedBound = true;
    _delegatedListeners = new AbortController();
    document.addEventListener('click', function (e) {
      var actEl = e.target.closest('[data-act]');
      if (!actEl) return;
      var container = actEl.closest('#research-list, #research-archived-list, #research-live-actions');
      if (!container) return;

      var act = actEl.getAttribute('data-act');
      var tid = actEl.getAttribute('data-task-id');
      if (!act) return;

      if (act === 'archive') {
        e.stopPropagation();
        if (tid) window.KazmaResearch.archive(tid);
      } else if (act === 'del') {
        e.stopPropagation();
        if (tid) window.KazmaResearch.del(tid);
      } else if (act === 'restore') {
        e.stopPropagation();
        if (tid) window.KazmaResearch.restore(tid);
      } else if (act === 'del-archived') {
        e.stopPropagation();
        if (tid) window.KazmaResearch.delArchived(tid);
      } else if (act === 'resume') {
        e.stopPropagation();
        if (tid) window.KazmaResearch.resumeSession(tid);
      } else if (act === 'view-detail') {
        if (e.target.closest('a, button[data-act]:not([data-act="view-detail"])')) return;
        if (tid) window.KazmaResearch.viewDetail(tid);
      }
    }, { signal: _delegatedListeners.signal });
  }

  // The session's own depth: "chat" (a chat tool's web search, recorded as
  // a session too), "brief" or "deep". Every row read "[Deep]" -- a one-line
  // search from the chat and a Brief run included (2026-09-28). An old row
  // with no depth is judged by its id (rs_chat_... is a chat search).
  var SESSION_TAGS = {
    chat: ['research.source_chat', 'Chat'],
    brief: ['research.depth_brief', 'Brief'],
    deep: ['research.depth_deep', 'Deep'],
  };
  function sessionTag(s) {
    var depth = String((s && s.depth) || '').toLowerCase();
    if (!SESSION_TAGS[depth]) {
      depth = String((s && s.id) || '').indexOf('rs_chat_') === 0 ? 'chat' : 'deep';
    }
    var key = SESSION_TAGS[depth][0];
    var label = (typeof window.t === 'function') ? window.t(key) : '';
    return '[' + (label && label !== key ? label : SESSION_TAGS[depth][1]) + '] ';
  }

  window.KazmaResearch = {
    init: function () {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      this.load();
      // Skip the poll while the tab is hidden (matches memory_console.js).
      pollTimer = setInterval(function() {
        if (!document.hidden) window.KazmaResearch.load();
      }, 15000);
      _bindDelegatedEvents();
      _registerSoftNavTeardown();
      var params = (typeof URLSearchParams !== 'undefined' && window.location) ? new URLSearchParams(window.location.search) : { get: function () { return null; } };
      // Soft navigation runs page scripts BEFORE pushing its new URL.
      // The incoming page carries its own query, including on same-page links.
      var root = $('research-root');
      var incoming = root && typeof root.getAttribute === 'function';
      var session = incoming ? root.getAttribute('data-session') : params.get('session');
      var job = incoming ? root.getAttribute('data-job') : params.get('job');
      if (session && session.length <= 200) this.viewDetail('session:' + session);
      else if (job && job.length <= 200) this.viewAcquisitionJob(job);
    },

    destroy: function () {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      closeLiveStream();
      if (_delegatedListeners) { _delegatedListeners.abort(); _delegatedListeners = null; }
      _delegatedBound = false;
    },

    switchTab: function (name) {
      ['list', 'archived', 'compare', 'about'].forEach(function (t) {
        var p = $('panel-' + t);
        if (p) p.style.display = 'none';
      });
      document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
      var panel = $('panel-' + name);
      var btn = document.querySelector('.tab[data-tab="' + name + '"]');
      if (panel) panel.style.display = 'block';
      if (btn) btn.classList.add('active');
      // Lazy-load archived list when switching to that tab.
      if (name === 'archived') this.loadArchived();
    },

    cancelDeep: function () {
      if (!liveSessionId) {
        toast(i18n('research.ui.no_running_session', 'No running session'), 'info');
        return;
      }
      var id = liveSessionId;
      fetch('/api/research/sessions/' + encodeURIComponent(id) + '/cancel', {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) {
            toast(data.error, 'error');
            return;
          }
          if (data.session) applyLiveSession(data.session);
          if (data.session && data.session.status === 'cancelling') {
            toast(i18n('research.ui.cancelling', 'Cancelling; waiting for cleanup…'), 'info');
          } else if (data.session && ['done', 'error', 'cancelled'].indexOf(data.session.status) !== -1) {
            closeLiveStream();
          }
          window.KazmaResearch.load();
        })
        .catch(function () { toast(i18n('research.ui.cancel_failed', 'Cancel failed'), 'error'); });
    },

    resumeSession: function (id) {
      id = String(id || '').replace(/^session:/, '');
      fetch('/api/research/sessions/' + encodeURIComponent(id) + '/resume', {
        method: 'POST', credentials: 'same-origin',
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error || !data.session) {
          toast(data.error || i18n('research.ui.resume_failed'), 'error');
          return;
        }
        liveSessionId = data.session.id;
        showLivePanel();
        applyLiveSession(data.session);
        openLiveStream(liveSessionId);
        window.KazmaResearch.load();
      }).catch(function () { toast(i18n('research.ui.resume_failed'), 'error'); });
    },

    startDeep: function () {
      var topicEl = $('research-topic');
      var topic = topicEl ? (topicEl.value || '').trim() : '';
      if (!topic) {
        toast(i18n('research.ui.enter_a_research_topic', 'Enter a research topic'), 'error');
        if (topicEl) topicEl.focus();
        return;
      }
      var depthEl = $('research-depth');
      var srcEl = $('research-max-sources');
      var depth = depthEl ? depthEl.value : 'deep';
      var maxSources = srcEl ? parseInt(srcEl.value, 10) || 8 : 8;
      var btn = $('research-start-btn');
      var startLabel = (window.KAZMA_I18N && window.KAZMA_I18N.research_start_btn) || 'Start';
      if (btn) {
        btn.disabled = true;
        btn.textContent = '…';
      }
      closeLiveStream();
      showLivePanel({
        status: 'pending',
        stage: 'queued',
        message: i18n('research_start_running'),
        sources: 0,
        log: [],
      });
      fetch('/api/research/sessions', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: topic,
          depth: depth,
          max_sources: maxSources,
        }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (btn) {
            btn.disabled = false;
            btn.textContent = startLabel;
          }
          if (!res.ok || !res.data || res.data.error) {
            toast((res.data && res.data.error) || i18n('research.ui.could_not_start_research', 'Could not start research'), 'error');
            hideLivePanel();
            return;
          }
          var sess = res.data.session || {};
          liveSessionId = sess.id;
          applyLiveSession(sess);
          openLiveStream(sess.id);
          toast(i18n('research_start_running'), 'info');
        })
        .catch(function () {
          if (btn) {
            btn.disabled = false;
            btn.textContent = startLabel;
          }
          toast(i18n('research.ui.could_not_start_research', 'Could not start research'), 'error');
          hideLivePanel();
        });
    },

    load: function () {
      Promise.all([
        readResults('/api/research/tasks?page=1&page_size=50&archived=false', 'tasks'),
        readResults('/api/research/papers?limit=50', 'papers'),
        readResults('/api/research/sessions?limit=30', 'sessions'),
      ]).then(function (triple) {
        listIncomplete = triple.some(function (p) { return p.error; });
        warning('research-load-error', listIncomplete, 'research.ui.results_unavailable');
        var data = triple[0] || {};
        var papersPayload = triple[1] || {};
        var sessionsPayload = triple[2] || {};
        var papers = papersPayload.papers || [];
        var paperTasks = papers.map(function (p) {
          var topic = p.topic || p.report_path || 'report';
          return {
            id: 'paper:' + (p.id || p.report_path),
            prompt: '[' + i18n('research.ui.paper_tag', 'Paper') + '] ' + topic,
            tag: '[' + i18n('research.ui.paper_tag', 'Paper') + '] ',
            topic: topic,
            status: 'paper',
            workers: ['research_pipeline'],
            cost: 0,
            duration: p.elapsed_seconds || 0,
            created_at: p.created_at,
            completed_at: p.created_at,
            report_path: p.report_path,
            docx_path: p.docx_path,
            sources: p.sources,
            rubric_score: p.rubric_score,
            metadata: { kind: 'research_paper' },
          };
        });
        var sessions = sessionsPayload.sessions || [];
        var sessionTasks = sessions.map(function (s) {
          return {
            id: 'session:' + s.id,
            prompt: sessionTag(s) + (s.topic || s.id),
            tag: sessionTag(s),
            topic: s.topic || s.id,
            status: s.status || 'pending',
            workers: ['research_pipeline'],
            cost: 0,
            duration: 0,
            created_at: s.created_at ? new Date(s.created_at * 1000).toISOString() : null,
            completed_at: s.updated_at ? new Date(s.updated_at * 1000).toISOString() : null,
            report_path: s.report_path,
            sources: s.sources,
            stage: s.stage,
            message: s.message,
            session_id: s.id,
            rubric_score: s.rubric_score,
            rubric_ok: s.rubric_ok,
            metadata: { kind: 'research_session' },
          };
        });
        // Prefer session cards over paper dups when both exist for same report
        allTasks = sessionTasks.concat(paperTasks).concat(data.tasks || []);
        renderList(allTasks);
        populateCompareDropdowns(data.tasks || []);

      });
    },

    loadArchived: function () {
      // Archived tab = archived swarm tasks + archived durable sessions.
      Promise.all([
        readResults('/api/research/tasks?page=1&page_size=50&archived=true', 'tasks'),
        readResults('/api/research/sessions?limit=50&archived=true', 'sessions'),
      ]).then(function (results) {
        archivedIncomplete = results.some(function (p) { return p.error; });
        warning('research-archived-load-error', archivedIncomplete, 'research.ui.results_unavailable');
        var tasks = results[0].tasks || [];
        var sessions = (results[1].sessions || []).map(function (s) {
          // Render archived sessions through the task-shaped card.
          return {
            id: 'session:' + s.id,
            prompt: sessionTag(s) + (s.topic || ''),
            tag: sessionTag(s),
            topic: s.topic || '',
            status: s.status || 'done',
            workers: [],
            cost: 0,
            created_at: s.created_at ? new Date(s.created_at * 1000).toISOString() : null,
            completed_at: s.updated_at ? new Date(s.updated_at * 1000).toISOString() : null,
          };
        });
        archivedTasks = tasks.concat(sessions);
        renderArchivedList(archivedTasks);
      }).catch(function () {
        archivedIncomplete = true;
        warning('research-archived-load-error', true, 'research.ui.results_unavailable');
      });
    },

    searchArchived: function (e) {
      var q = (e.target.value || '').toLowerCase();
      var filtered = q ? archivedTasks.filter(function (t) {
        return (t.prompt || '').toLowerCase().indexOf(q) !== -1;
      }) : archivedTasks;
      renderArchivedList(filtered);
    },

    search: function (e) {
      var q = (e.target.value || '').toLowerCase();
      var filtered = q ? allTasks.filter(function (t) {
        return (t.prompt || '').toLowerCase().indexOf(q) !== -1;
      }) : allTasks;
      renderList(filtered);
    },

    viewAcquisitionJob: function (id) {
      currentId = null;
      if ($('research-detail-actions')) $('research-detail-actions').style.display = 'none';
      $('research-list').style.display = 'none';
      $('research-detail').style.display = 'block';
      $('research-detail-title').textContent = i18n('research.ui.acquisition_title');
      $('research-detail-meta').textContent = id;
      var output = $('research-detail-output');
      output.textContent = i18n('research.ui.status_unavailable');
      fetch('/api/research/acquisition?job_id=' + encodeURIComponent(id) + '&language=' + (window.KAZMA_LANG === 'ar' ? 'ar' : 'en'), { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error('Unavailable'); return r.json(); })
        .then(function (data) {
          output.textContent = data.text || i18n('research.ui.status_unavailable');
          output.style.whiteSpace = 'pre-wrap';
          if (data.report_url && data.report_url.indexOf('/api/research/papers/file?path=') === 0) {
            var link = document.createElement('a');
            link.href = data.report_url;
            link.textContent = i18n('research.open_md', 'Open report');
            output.appendChild(document.createElement('br')); output.appendChild(link);
          }
        })
        .catch(function () { output.textContent = i18n('research.ui.status_unavailable'); });
      ['detail-archive-btn', 'detail-restore-btn', 'detail-delete-btn'].forEach(function (name) {
        var button = $(name); if (button) button.style.display = 'none';
      });
    },

    viewDetail: function (id) {
      currentId = id;
      if ($('research-detail-actions')) $('research-detail-actions').style.display = 'flex';
      $('research-detail-output').style.whiteSpace = '';
      $('research-detail-output').textContent = i18n('research.ui.loading', 'Loading…');
      $('research-list').style.display = 'none';
      $('research-detail').style.display = 'block';
      $('research-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });

      // Live / durable deep sessions
      if (String(id).indexOf('session:') === 0) {
        var sid = String(id).slice('session:'.length);
        fetch('/api/research/sessions/' + encodeURIComponent(sid), { credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) {
            if (!data || !data.session) {
              $('research-detail-output').textContent = i18n('research.ui.status_unavailable');
              toast(i18n('research.ui.session_not_found', 'Session not found'), 'error');
              return;
            }
            var s = data.session;
            $('research-detail-title').textContent = (s.topic || i18n('research.ui.deep_research', 'Deep research')).slice(0, 100);
            $('research-detail-meta').innerHTML =
              '<span>' + esc(i18n('research.ui.detail_session', 'Session')) + '</span> · <span>' + esc(stateLabel(s.status)) + '</span> · ' +
              (s.stage ? '<span>' + esc(i18n('research.ui.detail_stage', 'Stage: {stage}', { stage: stateLabel(s.stage) })) + '</span> · ' : '') +
              (s.sources != null ? '<span>' + esc(i18n('research.ui.detail_sources', 'Sources: {n}', { n: s.sources })) + '</span> · ' : '') +
              (s.report_path ? '<span dir="ltr" translate="no">' + esc(s.report_path) + '</span>' : '');
            var el = $('research-detail-output');
            el.className = 'markdown-body bidi-content';
            var body = s.summary || (s.log || []).join('\n') || s.message || i18n('research.ui.no_output_yet', '(no output yet)');
            if (s.report_path) {
              // Prefer loading the full report when available
              fetch('/api/research/papers/file?path=' + encodeURIComponent(s.report_path), {
                credentials: 'same-origin',
              })
                .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
                .then(function (md) {
                  if (window.KazmaStream && KazmaStream.markdown) {
                    el.innerHTML = KazmaStream.markdown(md);
                  } else {
                    el.textContent = md;
                  }
                  if (window.KazmaBidi) KazmaBidi.apply(el, md);
                })
                .catch(function () {
                  if (window.KazmaStream && KazmaStream.markdown) {
                    el.innerHTML = KazmaStream.markdown(body);
                  } else {
                    el.textContent = body;
                  }
                });
            } else {
              // Chat-research rows have no report.md — render the stored
              // summary (now persisted in full) as markdown like papers.
              if (window.KazmaStream && KazmaStream.markdown) {
                el.innerHTML = KazmaStream.markdown(body);
              } else {
                el.textContent = body;
              }
              if (window.KazmaBidi) KazmaBidi.apply(el, body);
            }
            var archBtn = $('detail-archive-btn');
            var restBtn = $('detail-restore-btn');
            // Sessions support archive/restore natively now (their own
            // endpoints); show the right button for the archived state.
            if (archBtn) archBtn.style.display = s.archived ? 'none' : '';
            if (restBtn) restBtn.style.display = s.archived ? '' : 'none';
            if (s.status === 'interrupted') {
              liveSessionId = s.id;
              closeLiveStream();
              showLivePanel(s);
            }
            // Re-attach live stream if still running
            if (s.status === 'running' || s.status === 'pending' || s.status === 'cancelling') {
              liveSessionId = s.id;
              showLivePanel(s);
              openLiveStream(s.id);
            }
          })
          .catch(function () { $('research-detail-output').textContent = i18n('research.ui.status_unavailable'); toast(i18n('research.ui.could_not_load_session', 'Could not load session'), 'error'); });
        return;
      }

      // Pipeline papers (not swarm tasks)
      if (String(id).indexOf('paper:') === 0) {
        var paper = null;
        for (var i = 0; i < allTasks.length; i++) {
          if (allTasks[i].id === id) { paper = allTasks[i]; break; }
        }
        if (!paper || !paper.report_path) {
          toast(i18n('research.ui.paper_not_found', 'Paper not found'), 'error');
          return;
        }
        $('research-detail-title').textContent = (paper.prompt || i18n('research.ui.paper', 'Paper')).slice(0, 100);
        $('research-detail-meta').innerHTML =
          '<span>' + esc(i18n('research.ui.detail_paper', 'Pipeline paper')) + '</span> · ' +
          (paper.sources != null ? '<span>' + esc(i18n('research.ui.detail_sources', 'Sources: {n}', { n: paper.sources })) + '</span> · ' : '') +
          '<span dir="ltr" translate="no">' + esc(paper.report_path) + '</span>';
        var el = $('research-detail-output');
        el.className = 'markdown-body bidi-content';
        el.textContent = i18n('research.ui.loading', 'Loading…');
        fetch('/api/research/papers/file?path=' + encodeURIComponent(paper.report_path), {
          credentials: 'same-origin',
        })
          .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
          .then(function (md) {
            if (window.KazmaStream && KazmaStream.markdown) {
              el.innerHTML = KazmaStream.markdown(md);
            } else {
              el.textContent = md;
            }
            if (window.KazmaBidi) KazmaBidi.apply(el, md);
            var archBtn = $('detail-archive-btn');
            var restBtn = $('detail-restore-btn');
            if (archBtn) archBtn.style.display = 'none';
            if (restBtn) restBtn.style.display = 'none';
          })
          .catch(function () {
            el.textContent = i18n('research.ui.could_not_load_report_file', 'Could not load report file.');
            toast(i18n('research.ui.could_not_load_paper', 'Could not load paper'), 'error');
          });
        return;
      }

      fetch(researchDeleteUrl(id), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || data.error) { $('research-detail-output').textContent = i18n('research.ui.status_unavailable'); toast(i18n('research.ui.could_not_load', 'Could not load'), 'error'); return; }
          var t = data.task;
          $('research-detail').style.display = 'block';
          $('research-detail-title').textContent = (t.prompt || i18n('research.ui.research', 'Research')).slice(0, 80);
          if (window.KazmaBidi) KazmaBidi.apply($('research-detail-title'), t.prompt || '');
          // Labels from the catalog; the worker names are data (escaped).
          $('research-detail-meta').innerHTML =
            '<span>' + esc(i18n('research.ui.meta_cost', 'Cost: {cost}', { cost: '$' + (t.cost || 0).toFixed(4) })) + '</span> · ' +
            '<span>' + esc(i18n('research.ui.meta_tokens', 'Tokens: {n}', { n: t.tokens || 0 })) + '</span> · ' +
            '<span>' + esc(i18n('research.ui.meta_duration', 'Duration: {s}s', { s: (t.duration || 0).toFixed(1) })) + '</span> · ' +
            '<span>' + esc(i18n('research.ui.meta_workers', 'Workers: {names}', { names: (t.workers || []).join(', ') })) + '</span>';
          var output = t.aggregated_output || t.synthesized_output ||
            (t.worker_results && t.worker_results[0] ? t.worker_results[0].output : '') ||
            i18n('research.ui.no_output', '(no output)');
          var el = $('research-detail-output');
          el.className = 'markdown-body bidi-content';
          if (window.KazmaStream && KazmaStream.markdown) {
            el.innerHTML = KazmaStream.markdown(output);
          } else {
            el.textContent = output;
          }
          if (window.KazmaBidi) KazmaBidi.apply(el, output);
          var isArchived = t.metadata && t.metadata.archived;
          var archBtn = $('detail-archive-btn');
          var restBtn = $('detail-restore-btn');
          if (archBtn) archBtn.style.display = isArchived ? 'none' : '';
          if (restBtn) restBtn.style.display = isArchived ? '' : 'none';
        }).catch(function () { $('research-detail-output').textContent = i18n('research.ui.status_unavailable'); });
    },

    exportCurrent: function (fmt) {
      if (!currentId) { toast(i18n('research.ui.select_a_research_result_first', 'Select a research result first'), 'error'); return; }
      toast(i18n('research.exporting_to', 'Exporting to {format}…', { format: fmt }), 'info');
      // The panel stores session selections as 'session:<id>' and pipeline
      // papers as 'paper:<id>'. Sessions export via their own endpoint
      // (research_sessions.db, not the swarm TaskStore); papers use the
      // report.md export; bare ids are swarm research tasks.
      var isSession = String(currentId).indexOf('session:') === 0;
      var isPaper = String(currentId).indexOf('paper:') === 0;
      var rawId = isSession
        ? String(currentId).slice('session:'.length)
        : currentId;
      var exportUrl = isPaper
        ? '/api/research/papers/export'
        : isSession
          ? '/api/research/sessions/' + encodeURIComponent(rawId) + '/export'
          : '/api/research/' + encodeURIComponent(rawId) + '/export';
      var body = isPaper
        ? (function () {
            var paper = null;
            for (var i = 0; i < allTasks.length; i++) {
              if (allTasks[i].id === currentId) { paper = allTasks[i]; break; }
            }
            return {
              format: fmt,
              report_path: paper && paper.report_path,
              topic: paper && paper.prompt,
            };
          })()
        : { format: fmt };
      fetch(exportUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.export_failed_error', 'Export failed: {error}', { error: data.error }), 'error'); return; }
          toast(i18n('research.exported', 'Exported: {name}', { name: data.filename || fmt }), 'success');
          if (data.download_url) {
            window.open(data.download_url, '_blank', 'noopener');
          } else if (data.filename) {
            window.open('/api/research/download?path=' + encodeURIComponent(data.filename), '_blank', 'noopener');
          } else if (data.path) {
            window.open('/api/research/download?path=' + encodeURIComponent(data.path), '_blank', 'noopener');
          }
        })
        .catch(function () { toast(i18n('research.ui.export_request_failed', 'Export request failed'), 'error'); });
    },

    backToList: function () {
      $('research-detail').style.display = 'none';
      $('research-list').style.display = 'flex';
      currentId = null;
    },

    delAndBack: async function () {
      if (!currentId) return;
      if (!await confirmDelete()) return;
      var id = currentId;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.delete_failed', 'Delete failed: ') + data.error, 'error'); return; }
          toast(i18n('research.ui.deleted', 'Deleted'), 'success');
          currentId = null;
          $('research-detail').style.display = 'none';
          $('research-list').style.display = 'flex';
          window.KazmaResearch.load();
        })
        .catch(function () { toast(i18n('research.ui.delete_failed_2', 'Delete failed'), 'error'); });
    },

    del: async function (id) {
      if (!await confirmDelete()) return;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.delete_failed', 'Delete failed: ') + data.error, 'error'); return; }
          toast(i18n('research.ui.deleted', 'Deleted'), 'success');
          window.KazmaResearch.load();
        })
        .catch(function () { toast(i18n('research.ui.delete_failed_2', 'Delete failed'), 'error'); });
    },

    delArchived: async function (id) {
      if (!await confirmDelete()) return;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.delete_failed', 'Delete failed: ') + data.error, 'error'); return; }
          toast(i18n('research.ui.deleted', 'Deleted'), 'success');
          window.KazmaResearch.loadArchived();
        })
        .catch(function () { toast(i18n('research.ui.delete_failed_2', 'Delete failed'), 'error'); });
    },

    archive: function (id) {
      fetch(researchArchiveUrl(id, 'archive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.archive_failed', 'Archive failed: ') + data.error, 'error'); return; }
          toast(i18n('research_archived_msg'), 'success');
          window.KazmaResearch.load();
          // If the archived panel is visible, refresh it too.
          if ($('panel-archived') && $('panel-archived').style.display !== 'none') {
            window.KazmaResearch.loadArchived();
          }
        })
        .catch(function () { toast(i18n('research.ui.archive_failed_2', 'Archive failed'), 'error'); });
    },

    restore: function (id) {
      fetch(researchArchiveUrl(id, 'unarchive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.restore_failed', 'Restore failed: ') + data.error, 'error'); return; }
          toast(i18n('research_restored_msg'), 'success');
          window.KazmaResearch.loadArchived();
        })
        .catch(function () { toast(i18n('research.ui.restore_failed_2', 'Restore failed'), 'error'); });
    },

    archiveCurrent: function () {
      if (!currentId) return;
      var id = currentId;
      fetch(researchArchiveUrl(id, 'archive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.archive_failed', 'Archive failed: ') + data.error, 'error'); return; }
          toast(i18n('research_archived_msg'), 'success');
          window.KazmaResearch.backToList();
          window.KazmaResearch.load();
        })
        .catch(function () { toast(i18n('research.ui.archive_failed_2', 'Archive failed'), 'error'); });
    },

    restoreCurrent: function () {
      if (!currentId) return;
      var id = currentId;
      fetch(researchArchiveUrl(id, 'unarchive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast(i18n('research.ui.restore_failed', 'Restore failed: ') + data.error, 'error'); return; }
          toast(i18n('research_restored_msg'), 'success');
          window.KazmaResearch.backToList();
          window.KazmaResearch.load();
        })
        .catch(function () { toast(i18n('research.ui.restore_failed_2', 'Restore failed'), 'error'); });
    },

    compare: function () {
      var a = $('research-cmp-a').value;
      var b = $('research-cmp-b').value;
      if (!a || !b) { toast(i18n('research.ui.pick_two_runs', 'Pick two runs'), 'error'); return; }
      $('research-cmp-result').innerHTML = '<div style="padding:1rem;color:var(--text-muted);">' + esc(i18n('research.comparing')) + '</div>';
      fetch('/api/research/compare', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ a: a, b: b }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { $('research-cmp-result').innerHTML = '<div translate="no" style="color:var(--error);">' + esc(data.error) + '</div>'; return; }
          var d = data.diff;
          function arrow(v) { return v > 0 ? '+' + v : String(v); }
          function row(label, a, b, delta) {
            return '<tr><td style="padding:8px;border-bottom:1px solid var(--border);">' + esc(label) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(a) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);">' + esc(b) + '</td>' +
              '<td style="text-align:right;padding:8px;border-bottom:1px solid var(--border);font-weight:600;">' + esc(delta) + '</td></tr>';
          }
          var html = '<table class="data-table" style="width:100%;border-collapse:collapse;font-size:0.9rem;">' +
            '<thead><tr><th style="text-align:start;padding:8px;">' + esc(i18n('research.metric')) + '</th>' +
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
          if (d.identical) html += '<p style="color:var(--success);display:flex;align-items:center;gap:4px;">' + CHECK_SVG + ' ' + esc(i18n('research.identical')) + '</p>';
          $('research-cmp-result').innerHTML = html;
        })
        .catch(function () { toast(i18n('research.ui.compare_failed', 'Compare failed'), 'error'); });
    },
  };

  function showLivePanel(s) {
    var panel = $('research-live');
    if (!panel) return;
    panel.style.display = 'block';
    applyLiveSession(s || {});
  }

  function hideLivePanel() {
    var panel = $('research-live');
    if (panel) panel.style.display = 'none';
    var actions = $('research-live-actions');
    if (actions) {
      actions.style.display = 'none';
      actions.innerHTML = '';
    }
  }

  function applyLiveSession(s) {
    if (!s) return;
    var versionId = s.id || liveSessionId;
    if (versionId !== liveVersionId) {
      liveVersionId = versionId;
      liveUpdatedAt = 0;
    }
    if (typeof s.updated_at === 'number' && Number.isFinite(s.updated_at)) {
      if (s.updated_at < liveUpdatedAt) return;
      liveUpdatedAt = s.updated_at;
    }
    var statusEl = $('research-live-status');
    var stageEl = $('research-live-stage');
    var msgEl = $('research-live-message');
    var errEl = $('research-live-error');
    var srcEl = $('research-live-sources');
    var logEl = $('research-live-log');
    var cancelBtn = $('research-cancel-btn');
    if (statusEl) statusEl.textContent = s.status ? stateLabel(s.status) : '—';
    if (stageEl) stageEl.textContent = s.stage ? ('· ' + stateLabel(s.stage)) : '';
    if (msgEl) msgEl.textContent = s.message || '';
    if (errEl) {
      if (s.status === 'error' && (s.error || s.message)) {
        errEl.style.display = 'block';
        errEl.textContent = s.error || s.message;
      } else {
        errEl.style.display = 'none';
        errEl.textContent = '';
      }
    }
    if (srcEl) {
      srcEl.textContent = (s.sources != null && s.sources > 0)
        ? sourcesText(s.sources)
        : '';
    }
    if (logEl && Array.isArray(s.log)) {
      logEl.textContent = s.log.slice(-40).join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (cancelBtn) {
      cancelBtn.style.display =
        (s.status === 'running' || s.status === 'pending') ? '' : 'none';
    }
    if (['done', 'partial', 'error', 'cancelled', 'interrupted'].indexOf(s.status) !== -1) {
      var actions = $('research-live-actions');
      if (actions) {
        actions.style.display = 'flex';
        var html = s.status === 'interrupted' ? '<button class="btn btn-primary btn-sm" data-act="resume" data-task-id="' + esc(s.id) + '">' + esc(i18n('research.ui.resume')) + '</button>' : '';
        if (s.report_path) {
          html += '<a class="btn btn-primary btn-sm" href="/api/research/papers/file?path=' +
            encodeURIComponent(s.report_path) + '" target="_blank">' +
            esc(i18n('research_view_report', 'View report')) + '</a>';
          html += '<button class="btn btn-secondary btn-sm" data-act="view-detail" data-task-id="' +
            esc('session:' + (s.id || liveSessionId || '')) + '">' + esc(i18n('research_open_md', 'Open')) + '</button>';
        }
        actions.innerHTML = html;
      }
      if (s.status === 'partial' || s.status === 'interrupted') toast(stateLabel(s.status), 'info');
      if (s.status === 'done') toast(i18n('research_start_done'), 'success');
      if (s.status === 'error') {
        toast(
          i18n('research_start_error', 'Research failed') +
            (s.error ? ': ' + String(s.error).slice(0, 120) : ''),
          'error',
        );
      }
      if (s.status === 'cancelled') toast(i18n('research_cancelled', 'Cancelled'), 'info');
    }
  }

  function closeLiveStream() {
    if (liveSource) {
      try { liveSource.close(); } catch (e) { /* ignore */ }
      if (typeof window !== 'undefined' && Array.isArray(window.__kazmaEventSources)) {
        var idx = window.__kazmaEventSources.indexOf(liveSource);
        if (idx !== -1) window.__kazmaEventSources.splice(idx, 1);
      }
      liveSource = null;
    }
  }

  function openLiveStream(sessionId) {
    closeLiveStream();
    if (!sessionId || typeof EventSource === 'undefined') return;
    var url = '/api/research/sessions/' + encodeURIComponent(sessionId) + '/stream';
    liveSource = new EventSource(url);
    if (typeof window !== 'undefined') {
      window.__kazmaEventSources = window.__kazmaEventSources || [];
      window.__kazmaEventSources.push(liveSource);
    }
    function onPayload(raw) {
      var data;
      try { data = JSON.parse(raw.data); } catch (e) { return; }
      if (!data) return;
      warning('research-live-connection', false);
      if (data.type === 'snapshot' && data.session) {
        applyLiveSession(data.session);
        return;
      }
      if (data.type === 'done' || data.type === 'error') {
        if (data.session) applyLiveSession(data.session);
        else {
          // refresh from API
          fetch('/api/research/sessions/' + encodeURIComponent(sessionId), { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
              if (res && res.session) applyLiveSession(res.session);
            });
        }
        closeLiveStream();
        window.KazmaResearch.load();
        return;
      }
      if (data.type === 'heartbeat') return;
      // progress events carry status/stage/message
      applyLiveSession({
        id: data.session_id || sessionId,
        status: data.status || 'running',
        updated_at: data.updated_at,
        stage: data.stage,
        message: data.message,
        sources: data.sources,
        report_path: data.report_path,
        log: (function () {
          var logEl = $('research-live-log');
          var prev = logEl ? logEl.textContent.split('\n').filter(Boolean) : [];
          if (data.stage || data.message) {
            prev.push('[' + (data.stage || '?') + '] ' + (data.message || ''));
          }
          return prev.slice(-40);
        })(),
      });
      if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
        closeLiveStream();
        window.KazmaResearch.load();
      }
    }
    liveSource.addEventListener('snapshot', onPayload);
    liveSource.addEventListener('progress', onPayload);
    liveSource.addEventListener('done', onPayload);
    liveSource.addEventListener('error', function (ev) {
      // Named error event from server, or connection error
      if (ev && ev.data) onPayload(ev);
      // Network drop: EventSource auto-reconnects; leave open unless terminal
    });
    liveSource.addEventListener('heartbeat', function () { warning('research-live-connection', false); });
    liveSource.onerror = function (ev) {
      if (ev && ev.data) return; // A named domain error is handled above.
      if (!liveSessionId) closeLiveStream();
      else warning('research-live-connection', true, 'research.ui.connection_interrupted');
    };
  }

  /** A run's state in the reader's language; an unknown one as it is. */
  function stateLabel(s) {
    var v = String(s || '');
    return v ? i18n('research.ui.state_' + v.toLowerCase(), v) : '';
  }
  /** "3 sources · rubric 100 passed" */
  function sourcesText(n) {
    return window.kazmaCount('research.ui.sources_n', n);
  }
  function rubricText(score, ok) {
    var n = Math.round(Number(score));
    if (ok === true) return i18n('research.ui.rubric_passed', 'rubric {n} passed', { n: n });
    if (ok === false) return i18n('research.ui.rubric_failed', 'rubric {n} failed', { n: n });
    return i18n('research.ui.rubric', 'rubric {n}', { n: n });
  }
  /** The title line: our tag, then the run's own words (content). */
  // A title runs in its question's language; the tag in front ("[مختصر]",
  // the page's word) is isolated. Under dir="auto" the tag's first letter
  // decided, so in the Arabic UI an English question ran right-to-left.
  function titleDir(t) {
    var text = t.topic != null ? t.topic : (t.prompt || '');
    return (window.KazmaBidi && KazmaBidi.blockDir && KazmaBidi.blockDir(text)) || 'auto';
  }

  function titleHtml(t) {
    if (t.topic != null) {
      var tag = String(t.tag || '').trim();
      return (tag ? '<bdi>' + esc(tag) + '</bdi> ' : '') +
        '<span translate="no">' + esc(t.topic) + '</span>';
    }
    return t.prompt
      ? '<span translate="no">' + esc(t.prompt) + '</span>'
      : esc(i18n('research.ui.no_prompt', '(no prompt)'));
  }

  // "No research yet" answers reads that all came back. With one of them
  // unavailable the list is incomplete: an empty one says nothing, and the
  // warning above it is the page's answer (tests/js/test_research_unavailable.js).
  var listIncomplete = false;
  var archivedIncomplete = false;

  function renderList(tasks) {
    var el = $('research-list');
    if (!tasks.length) {
      el.innerHTML = listIncomplete ? '' : '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(i18n('research_no_results')) + '</div>';
      return;
    }
    el.innerHTML = tasks.map(function (t) {
      var isPaper = t.status === 'paper' || (t.id && String(t.id).indexOf('paper:') === 0);
      var isSession = t.id && String(t.id).indexOf('session:') === 0;
      var meta;
      var rubricBit = (t.rubric_score != null && t.rubric_score !== '')
        ? (' · ' + esc(rubricText(t.rubric_score, t.rubric_ok)))
        : '';
      if (isSession) {
        meta = esc(i18n('research.ui.kind_session', 'session')) + ' · ' + esc(stateLabel(t.status)) +
          (t.stage ? ' · ' + esc(stateLabel(t.stage)) : '') +
          (t.sources != null && t.sources > 0 ? ' · ' + esc(sourcesText(t.sources)) : '') +
          rubricBit +
          ' · ' + esc(timeAgo(t.completed_at || t.created_at));
      } else if (isPaper) {
        meta = esc(i18n('research.ui.kind_pipeline', 'pipeline')) + ' · ' +
          (t.sources != null ? esc(sourcesText(t.sources)) + ' · ' : '') +
          (t.report_path ? '<span translate="no">' + esc(t.report_path) + '</span> · ' : '') +
          (rubricBit ? rubricBit.replace(/^ · /, '') + ' · ' : '') +
          esc(timeAgo(t.created_at));
      } else {
        meta = '<span translate="no">' + esc((t.workers || []).join(', ')) + '</span> · ' +
          '<span>$' + (t.cost || 0).toFixed(4) + '</span> · ' +
          '<span>' + (t.duration || 0).toFixed(1) + 's</span> · ' +
          '<span>' + timeAgo(t.completed_at || t.created_at) + '</span>';
      }
      var actions;
      if (isPaper || (isSession && t.report_path)) {
        actions = (t.report_path
          ? '<a class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" href="/api/research/papers/file?path=' + encodeURIComponent(t.report_path) + '" onclick="event.stopPropagation();" target="_blank" title="' + esc(i18n('research.open_md', 'Open report')) + '">MD</a>'
          : '') +
          (t.docx_path
            ? '<a class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" href="/api/research/papers/file?path=' + encodeURIComponent(t.docx_path) + '" onclick="event.stopPropagation();" target="_blank" title="DOCX">DOCX</a>'
            : '');
      } else if (isSession) {
        actions = '';
      } else {
        actions = '<button class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;display:flex;align-items:center;" data-act="archive" data-task-id="' + esc(t.id) + '" title="' + esc(i18n('research.ui.archive', 'Archive')) + '">' + ARCHIVE_SVG + '</button>' +
          '<button class="btn btn-danger btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" data-act="del" data-task-id="' + esc(t.id) + '" title="' + esc(i18n('research.ui.delete', 'Delete')) + '">×</button>';
      }
      if (isSession && t.status === 'interrupted') actions += '<button class="btn btn-primary btn-sm" data-act="resume" data-task-id="' + esc(t.id) + '">' + esc(i18n('research.ui.resume')) + '</button>';
      return '<div class="card" style="padding:12px 16px;cursor:pointer;max-width:100%;overflow:hidden;box-sizing:border-box;" data-act="view-detail" data-task-id="' + esc(t.id) + '">' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">' +
          '<div style="flex:1;min-width:0;overflow:hidden;">' +
            '<div dir="' + titleDir(t) + '" style="font-weight:600;color:var(--text-primary);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">' + titleHtml(t) + '</div>' +
            '<div style="font-size:0.85rem;color:var(--text-muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' +
              meta +
            '</div>' +
          '</div>' +
          '<span style="font-size:0.75rem;color:var(--text-muted);background:var(--surface-2);padding:2px 8px;border-radius:4px;flex-shrink:0;">' + esc(stateLabel(t.status)) + '</span>' +
          actions +
        '</div>' +
      '</div>';
    }).join('');
  }

  function renderArchivedList(tasks) {
    var el = $('research-archived-list');
    if (!el) return;
    if (!tasks.length) {
      el.innerHTML = archivedIncomplete ? '' : '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(i18n('research_no_archived')) + '</div>';
      return;
    }
    el.innerHTML = tasks.map(function (t) {
      return '<div class="card" style="padding:12px 16px;cursor:pointer;max-width:100%;overflow:hidden;box-sizing:border-box;opacity:0.7;" data-act="view-detail" data-task-id="' + esc(t.id) + '">' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">' +
          '<div style="flex:1;min-width:0;overflow:hidden;">' +
            '<div dir="' + titleDir(t) + '" style="font-weight:600;color:var(--text-primary);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">' + titleHtml(t) + '</div>' +
            '<div style="font-size:0.85rem;color:var(--text-muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' +
              '<span translate="no">' + esc((t.workers || []).join(', ')) + '</span> · ' +
              '<span>$' + (t.cost || 0).toFixed(4) + '</span> · ' +
              '<span>' + timeAgo(t.completed_at || t.created_at) + '</span>' +
            '</div>' +
          '</div>' +
          '<span style="font-size:0.75rem;color:var(--text-muted);background:var(--surface-2);padding:2px 8px;border-radius:4px;flex-shrink:0;">' + esc(stateLabel(t.status)) + '</span>' +
          '<button class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;display:flex;align-items:center;" data-act="restore" data-task-id="' + esc(t.id) + '" title="' + esc(i18n('research.ui.restore', 'Restore')) + '">' + RESTORE_SVG + '</button>' +
          '<button class="btn btn-danger btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" data-act="del-archived" data-task-id="' + esc(t.id) + '" title="' + esc(i18n('research.ui.delete', 'Delete')) + '">×</button>' +
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
