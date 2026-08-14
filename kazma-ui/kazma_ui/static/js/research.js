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

  // SVG icons (no emojis — consistent with the rest of the Kazma UI).
  var ARCHIVE_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><rect x="2" y="4" width="20" height="4" rx="1"/><path d="M4 8v10a2 2 0 002 2h12a2 2 0 002-2V8"/><line x1="10" y1="12" x2="14" y2="12"/></svg>';
  var RESTORE_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M3 12a9 9 0 109-9"/><polyline points="3 4 3 10 9 10"/></svg>';
  var CHECK_SVG = '<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
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
      // Skip the poll while the tab is hidden (matches memory_console.js).
      pollTimer = setInterval(function() {
        if (!document.hidden) window.KazmaResearch.load();
      }, 15000);
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
        toast('No running session', 'info');
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
          toast(i18n('research_cancelled') || 'Research cancelled', 'info');
          if (data.session) applyLiveSession(data.session);
          closeLiveStream();
          var cancelBtn = $('research-cancel-btn');
          if (cancelBtn) cancelBtn.style.display = 'none';
          window.KazmaResearch.load();
        })
        .catch(function () { toast('Cancel failed', 'error'); });
    },

    startDeep: function () {
      var topicEl = $('research-topic');
      var topic = topicEl ? (topicEl.value || '').trim() : '';
      if (!topic) {
        toast('Enter a research topic', 'error');
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
            toast((res.data && res.data.error) || 'Could not start research', 'error');
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
          toast('Could not start research', 'error');
          hideLivePanel();
        });
    },

    load: function () {
      Promise.all([
        fetch('/api/research/tasks?page=1&page_size=50&archived=false', { credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.json() : { tasks: [] }; })
          .catch(function () { return { tasks: [] }; }),
        fetch('/api/research/papers?limit=50', { credentials: 'same-origin' })
          .then(function (r) {
            if (!r.ok) {
              console.warn('[research] papers API HTTP', r.status);
              return { papers: [], error: 'HTTP ' + r.status };
            }
            return r.json();
          })
          .catch(function (err) {
            console.warn('[research] papers fetch failed', err);
            return { papers: [] };
          }),
        fetch('/api/research/sessions?limit=30', { credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.json() : { sessions: [] }; })
          .catch(function () { return { sessions: [] }; }),
      ]).then(function (triple) {
        var data = triple[0] || {};
        var papersPayload = triple[1] || {};
        var sessionsPayload = triple[2] || {};
        var papers = papersPayload.papers || [];
        var paperTasks = papers.map(function (p) {
          var topic = p.topic || p.report_path || 'report';
          return {
            id: 'paper:' + (p.id || p.report_path),
            prompt: '[Paper] ' + topic,
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
            prompt: '[Deep] ' + (s.topic || s.id),
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
        if (papersPayload.error) {
          toast('Papers list: ' + papersPayload.error, 'error');
        }
      });
    },

    loadArchived: function () {
      // Archived tab = archived swarm tasks + archived durable sessions.
      Promise.all([
        fetch('/api/research/tasks?page=1&page_size=50&archived=true', { credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.json() : { tasks: [], count: 0 }; }),
        fetch('/api/research/sessions?limit=50&archived=true', { credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.json() : { sessions: [], count: 0 }; })
          .catch(function () { return { sessions: [] }; }),
      ]).then(function (results) {
        var tasks = results[0].tasks || [];
        var sessions = (results[1].sessions || []).map(function (s) {
          // Render archived sessions through the task-shaped card.
          return {
            id: 'session:' + s.id,
            prompt: '[Deep] ' + (s.topic || ''),
            status: s.status || 'done',
            workers: [],
            cost: 0,
            created_at: s.created_at,
            completed_at: s.updated_at,
          };
        });
        archivedTasks = tasks.concat(sessions);
        renderArchivedList(archivedTasks);
      }).catch(function () { /* silent */ });
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

    viewDetail: function (id) {
      currentId = id;
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
              toast('Session not found', 'error');
              return;
            }
            var s = data.session;
            $('research-detail-title').textContent = (s.topic || 'Deep research').slice(0, 100);
            $('research-detail-meta').innerHTML =
              '<span>Session</span> · <span>' + esc(s.status) + '</span> · ' +
              (s.stage ? '<span>Stage: ' + esc(s.stage) + '</span> · ' : '') +
              (s.sources != null ? '<span>Sources: ' + s.sources + '</span> · ' : '') +
              (s.report_path ? '<span dir="ltr">' + esc(s.report_path) + '</span>' : '');
            var el = $('research-detail-output');
            el.className = 'markdown-body bidi-content';
            var body = s.summary || (s.log || []).join('\n') || s.message || '(no output yet)';
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
            // Re-attach live stream if still running
            if (s.status === 'running' || s.status === 'pending') {
              liveSessionId = s.id;
              showLivePanel(s);
              openLiveStream(s.id);
            }
          })
          .catch(function () { toast('Could not load session', 'error'); });
        return;
      }

      // Pipeline papers (not swarm tasks)
      if (String(id).indexOf('paper:') === 0) {
        var paper = null;
        for (var i = 0; i < allTasks.length; i++) {
          if (allTasks[i].id === id) { paper = allTasks[i]; break; }
        }
        if (!paper || !paper.report_path) {
          toast('Paper not found', 'error');
          return;
        }
        $('research-detail-title').textContent = (paper.prompt || 'Paper').slice(0, 100);
        $('research-detail-meta').innerHTML =
          '<span>Pipeline paper</span> · ' +
          (paper.sources != null ? '<span>Sources: ' + paper.sources + '</span> · ' : '') +
          '<span dir="ltr">' + esc(paper.report_path) + '</span>';
        var el = $('research-detail-output');
        el.className = 'markdown-body bidi-content';
        el.textContent = 'Loading…';
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
            el.textContent = 'Could not load report file.';
            toast('Could not load paper', 'error');
          });
        return;
      }

      fetch(researchDeleteUrl(id), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || data.error) { toast('Could not load', 'error'); return; }
          var t = data.task;
          $('research-detail').style.display = 'block';
          $('research-detail-title').textContent = (t.prompt || 'Research').slice(0, 80);
          if (window.KazmaBidi) KazmaBidi.apply($('research-detail-title'), t.prompt || '');
          $('research-detail-meta').innerHTML =
            '<span>Cost: <strong>$' + (t.cost || 0).toFixed(4) + '</strong></span> · ' +
            '<span>Tokens: ' + (t.tokens || 0) + '</span> · ' +
            '<span>Duration: ' + (t.duration || 0).toFixed(1) + 's</span> · ' +
            '<span>Workers: ' + (t.workers || []).join(', ') + '</span>';
          var output = t.aggregated_output || t.synthesized_output ||
            (t.worker_results && t.worker_results[0] ? t.worker_results[0].output : '') ||
            '(no output)';
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
        });
    },

    exportCurrent: function (fmt) {
      if (!currentId) { toast('Select a research result first', 'error'); return; }
      toast('Exporting to ' + fmt + '…', 'info');
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
          if (data.error) { toast('Export failed: ' + data.error, 'error'); return; }
          toast('Exported: ' + (data.filename || fmt), 'success');
          if (data.download_url) {
            window.open(data.download_url, '_blank', 'noopener');
          } else if (data.filename) {
            window.open('/api/research/download?path=' + encodeURIComponent(data.filename), '_blank', 'noopener');
          } else if (data.path) {
            window.open('/api/research/download?path=' + encodeURIComponent(data.path), '_blank', 'noopener');
          }
        })
        .catch(function () { toast('Export request failed', 'error'); });
    },

    backToList: function () {
      $('research-detail').style.display = 'none';
      $('research-list').style.display = 'flex';
      currentId = null;
    },

    delAndBack: async function () {
      if (!currentId) return;
      if (!await confirm('Delete this research result?')) return;
      var id = currentId;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Delete failed: ' + data.error, 'error'); return; }
          toast('Deleted', 'success');
          currentId = null;
          $('research-detail').style.display = 'none';
          $('research-list').style.display = 'flex';
          window.KazmaResearch.load();
        })
        .catch(function () { toast('Delete failed', 'error'); });
    },

    del: async function (id) {
      if (!await confirm('Delete this research result?')) return;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Delete failed: ' + data.error, 'error'); return; }
          toast('Deleted', 'success');
          window.KazmaResearch.load();
        })
        .catch(function () { toast('Delete failed', 'error'); });
    },

    delArchived: async function (id) {
      if (!await confirm('Delete this research result?')) return;
      fetch(researchDeleteUrl(id), {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Delete failed: ' + data.error, 'error'); return; }
          toast('Deleted', 'success');
          window.KazmaResearch.loadArchived();
        })
        .catch(function () { toast('Delete failed', 'error'); });
    },

    archive: function (id) {
      fetch(researchArchiveUrl(id, 'archive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Archive failed: ' + data.error, 'error'); return; }
          toast(i18n('research_archived_msg'), 'success');
          window.KazmaResearch.load();
          // If the archived panel is visible, refresh it too.
          if ($('panel-archived') && $('panel-archived').style.display !== 'none') {
            window.KazmaResearch.loadArchived();
          }
        })
        .catch(function () { toast('Archive failed', 'error'); });
    },

    restore: function (id) {
      fetch(researchArchiveUrl(id, 'unarchive'), {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { toast('Restore failed: ' + data.error, 'error'); return; }
          toast(i18n('research_restored_msg'), 'success');
          window.KazmaResearch.loadArchived();
        })
        .catch(function () { toast('Restore failed', 'error'); });
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
          if (data.error) { toast('Archive failed: ' + data.error, 'error'); return; }
          toast(i18n('research_archived_msg'), 'success');
          window.KazmaResearch.backToList();
          window.KazmaResearch.load();
        })
        .catch(function () { toast('Archive failed', 'error'); });
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
          if (data.error) { toast('Restore failed: ' + data.error, 'error'); return; }
          toast(i18n('research_restored_msg'), 'success');
          window.KazmaResearch.backToList();
          window.KazmaResearch.load();
        })
        .catch(function () { toast('Restore failed', 'error'); });
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
          if (d.identical) html += '<p style="color:var(--success);display:flex;align-items:center;gap:4px;">' + CHECK_SVG + ' ' + esc(i18n('research.identical')) + '</p>';
          $('research-cmp-result').innerHTML = html;
        })
        .catch(function () { toast('Compare failed', 'error'); });
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
    var statusEl = $('research-live-status');
    var stageEl = $('research-live-stage');
    var msgEl = $('research-live-message');
    var errEl = $('research-live-error');
    var srcEl = $('research-live-sources');
    var logEl = $('research-live-log');
    var cancelBtn = $('research-cancel-btn');
    if (statusEl) statusEl.textContent = s.status || '—';
    if (stageEl) stageEl.textContent = s.stage ? ('· ' + s.stage) : '';
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
        ? (s.sources + ' sources')
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
    if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled') {
      var actions = $('research-live-actions');
      if (actions) {
        actions.style.display = 'flex';
        var html = '';
        if (s.report_path) {
          html += '<a class="btn btn-primary btn-sm" href="/api/research/papers/file?path=' +
            encodeURIComponent(s.report_path) + '" target="_blank">' +
            esc(i18n('research_view_report') || 'View report') + '</a>';
          html += '<button class="btn btn-secondary btn-sm" onclick="KazmaResearch.viewDetail(\'session:' +
            esc(s.id || liveSessionId || '') + '\')">' + esc(i18n('research_open_md') || 'Open') + '</button>';
        }
        actions.innerHTML = html;
      }
      if (s.status === 'done') toast(i18n('research_start_done'), 'success');
      if (s.status === 'error') {
        toast(
          (i18n('research_start_error') || 'Research failed') +
            (s.error ? ': ' + String(s.error).slice(0, 120) : ''),
          'error',
        );
      }
      if (s.status === 'cancelled') toast(i18n('research_cancelled') || 'Cancelled', 'info');
    }
  }

  function closeLiveStream() {
    if (liveSource) {
      try { liveSource.close(); } catch (e) { /* ignore */ }
      liveSource = null;
    }
  }

  function openLiveStream(sessionId) {
    closeLiveStream();
    if (!sessionId || typeof EventSource === 'undefined') return;
    var url = '/api/research/sessions/' + encodeURIComponent(sessionId) + '/stream';
    liveSource = new EventSource(url);
    function onPayload(raw) {
      var data;
      try { data = JSON.parse(raw.data); } catch (e) { return; }
      if (!data) return;
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
      if (data.status === 'done' || data.status === 'error') {
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
    liveSource.addEventListener('heartbeat', function () { /* keep-alive */ });
    liveSource.onerror = function () {
      // If session already terminal, close; else let browser reconnect
      if (!liveSessionId) closeLiveStream();
    };
  }

  function renderList(tasks) {
    var el = $('research-list');
    if (!tasks.length) {
      el.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(i18n('research_no_results')) + '</div>';
      return;
    }
    el.innerHTML = tasks.map(function (t) {
      var isPaper = t.status === 'paper' || (t.id && String(t.id).indexOf('paper:') === 0);
      var isSession = t.id && String(t.id).indexOf('session:') === 0;
      var meta;
      var rubricBit = (t.rubric_score != null && t.rubric_score !== '')
        ? (' · rubric ' + Math.round(Number(t.rubric_score)) +
           (t.rubric_ok === true ? ' passed' : (t.rubric_ok === false ? ' failed' : '')))
        : '';
      if (isSession) {
        meta = 'session · ' + esc(t.status || '') +
          (t.stage ? ' · ' + esc(t.stage) : '') +
          (t.sources != null && t.sources > 0 ? ' · ' + t.sources + ' sources' : '') +
          rubricBit +
          ' · ' + timeAgo(t.completed_at || t.created_at);
      } else if (isPaper) {
        meta = 'pipeline · ' + (t.sources != null ? t.sources + ' sources · ' : '') +
          (t.report_path ? esc(t.report_path) + ' · ' : '') +
          (rubricBit ? rubricBit.replace(/^ · /, '') + ' · ' : '') +
          timeAgo(t.created_at);
      } else {
        meta = '<span>' + esc((t.workers || []).join(', ')) + '</span> · ' +
          '<span>$' + (t.cost || 0).toFixed(4) + '</span> · ' +
          '<span>' + (t.duration || 0).toFixed(1) + 's</span> · ' +
          '<span>' + timeAgo(t.completed_at || t.created_at) + '</span>';
      }
      var actions;
      if (isPaper || (isSession && t.report_path)) {
        actions = (t.report_path
          ? '<a class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" href="/api/research/papers/file?path=' + encodeURIComponent(t.report_path) + '" onclick="event.stopPropagation();" target="_blank" title="' + esc(i18n('research.open_md') || 'Open report') + '">MD</a>'
          : '') +
          (t.docx_path
            ? '<a class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" href="/api/research/papers/file?path=' + encodeURIComponent(t.docx_path) + '" onclick="event.stopPropagation();" target="_blank" title="DOCX">DOCX</a>'
            : '');
      } else if (isSession) {
        actions = '';
      } else {
        actions = '<button class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;display:flex;align-items:center;" onclick="event.stopPropagation();KazmaResearch.archive(\'' + t.id + '\')" title="Archive">' + ARCHIVE_SVG + '</button>' +
          '<button class="btn btn-danger btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" onclick="event.stopPropagation();KazmaResearch.del(\'' + t.id + '\')" title="Delete">×</button>';
      }
      var onclick = ' onclick="KazmaResearch.viewDetail(\'' + String(t.id).replace(/'/g, "\\'") + '\')"';
      return '<div class="card" style="padding:12px 16px;cursor:pointer;max-width:100%;overflow:hidden;box-sizing:border-box;"' + onclick + '>' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">' +
          '<div style="flex:1;min-width:0;overflow:hidden;">' +
            '<div style="font-weight:600;color:var(--text-primary);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">' + esc(t.prompt || '(no prompt)') + '</div>' +
            '<div style="font-size:0.85rem;color:var(--text-muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' +
              meta +
            '</div>' +
          '</div>' +
          '<span style="font-size:0.75rem;color:var(--text-muted);background:var(--surface-2);padding:2px 8px;border-radius:4px;flex-shrink:0;">' + esc(t.status) + '</span>' +
          actions +
        '</div>' +
      '</div>';
    }).join('');
  }

  function renderArchivedList(tasks) {
    var el = $('research-archived-list');
    if (!el) return;
    if (!tasks.length) {
      el.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-muted);">' + esc(i18n('research_no_archived')) + '</div>';
      return;
    }
    el.innerHTML = tasks.map(function (t) {
      return '<div class="card" style="padding:12px 16px;cursor:pointer;max-width:100%;overflow:hidden;box-sizing:border-box;opacity:0.7;" onclick="KazmaResearch.viewDetail(\'' + t.id + '\')">' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;">' +
          '<div style="flex:1;min-width:0;overflow:hidden;">' +
            '<div style="font-weight:600;color:var(--text-primary);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">' + esc(t.prompt || '(no prompt)') + '</div>' +
            '<div style="font-size:0.85rem;color:var(--text-muted);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' +
              '<span>' + esc((t.workers || []).join(', ')) + '</span> · ' +
              '<span>$' + (t.cost || 0).toFixed(4) + '</span> · ' +
              '<span>' + timeAgo(t.completed_at || t.created_at) + '</span>' +
            '</div>' +
          '</div>' +
          '<span style="font-size:0.75rem;color:var(--text-muted);background:var(--surface-2);padding:2px 8px;border-radius:4px;flex-shrink:0;">' + esc(t.status) + '</span>' +
          '<button class="btn btn-secondary btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;display:flex;align-items:center;" onclick="event.stopPropagation();KazmaResearch.restore(\'' + t.id + '\')" title="Restore">' + RESTORE_SVG + '</button>' +
          '<button class="btn btn-danger btn-sm" style="flex-shrink:0;margin-left:4px;padding:2px 8px;font-size:0.75rem;" onclick="event.stopPropagation();KazmaResearch.delArchived(\'' + t.id + '\')" title="Delete">×</button>' +
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
