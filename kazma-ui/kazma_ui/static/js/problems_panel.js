/* The Dashboard's Open problems card (resilience plan G2).
 *
 * Reads GET /api/problems every 30 s while the page is visible: every open
 * problem from where it lives (the bell's alerts, run incidents, research and
 * crawl alerts, unmet install requirements, open advisories, the outside
 * observer). A resolved problem leaves by itself. Acknowledge hides one until
 * it changes; Snooze hides it for a day.
 *
 * Built with DOM methods: titles and details are the problems' own data
 * (shown as text, translate="no"); interface words come from the catalog.
 */
(function () {
  'use strict';

  var POLL_MS = 30000;
  if (window.__kazmaProblemsPanel && window.__kazmaProblemsPanel.timer) {
    clearInterval(window.__kazmaProblemsPanel.timer);
  }
  var state = { timer: null, hidden: false };
  window.__kazmaProblemsPanel = state;

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function content(tag, cls, text) {
    var node = el(tag, cls, text);
    node.setAttribute('translate', 'no');
    return node;
  }

  function safeLink(link) {
    return typeof link === 'string' && link.charAt(0) === '/' && link.charAt(1) !== '/' ? link : '';
  }

  function when(epoch) {
    if (!epoch) return '';
    return window.KazmaFormat ? window.KazmaFormat.relative(Number(epoch)) : '';
  }

  var SOURCES = {
    alert: ['problems.source.alert', 'Alert'],
    incident: ['problems.source.incident', 'Running work'],
    acquisition: ['problems.source.acquisition', 'Research and crawls'],
    requirements: ['problems.source.requirements', 'Install requirements not met'],
    advisory: ['problems.source.advisory', 'Security advisory'],
    observer: ['problems.source.observer', 'Outside watch'],
  };

  function sourceLabel(source) {
    var pair = SOURCES[source] || [null, source];
    return pair[0] ? tr(pair[0], pair[1]) : String(source);
  }

  function resolvedLine(source) {
    var key = 'problems.resolved.' + (SOURCES[source] ? source : 'alert');
    return tr(key, 'It closes by itself when it is resolved.');
  }

  async function decide(id, how) {
    try {
      await window.kazmaSave('/api/problems/' + encodeURIComponent(id) + '/' + how, { method: 'POST', body: {} });
    } catch (_) {
      if (typeof window.showToast === 'function') {
        window.showToast(tr('problems.decide_failed', 'That could not be saved.'), 'error');
      }
      return;
    }
    load();
  }

  // The problem's tone on an icon tile (the Dashboard's shared components):
  // a critical one in the danger tone, the rest in the warning tone.
  function tile(critical) {
    var box = el('span', 'icon-tile ' + (critical ? 'tone-danger' : 'tone-warning'));
    box.setAttribute('aria-hidden', 'true');
    var name = critical ? 'alert-circle' : 'alert';
    if (window.KazmaIcons && typeof window.KazmaIcons.span === 'function') {
      box.innerHTML = window.KazmaIcons.span(name);
    }
    return box;
  }

  function row(problem) {
    var critical = problem.severity === 'critical';
    var item = el('li', 'problems-item problems-' + (critical ? 'critical' : 'warn'));
    item.id = 'problem-' + problem.id;
    item.appendChild(tile(critical));
    var main = el('div', 'problems-main');
    item.appendChild(main);
    var head = el('div', 'problems-head');
    head.appendChild(el('span', 'problems-source', sourceLabel(problem.source)));
    var title = content('strong', 'problems-title', problem.title || '');
    head.appendChild(title);
    if (problem.since) head.appendChild(el('span', 'problems-since', when(problem.since)));
    main.appendChild(head);
    if (problem.detail) main.appendChild(content('p', 'problems-detail', problem.detail));
    if (problem.fix) {
      var fix = el('p', 'problems-fix', tr('problems.fix', 'What fixes it:') + ' ');
      var code = content('code', '', problem.fix);
      code.setAttribute('dir', 'ltr');
      fix.appendChild(code);
      main.appendChild(fix);
    }
    main.appendChild(el('p', 'problems-resolved', resolvedLine(problem.source)));
    var tools = el('div', 'problems-tools');
    var link = safeLink(problem.link);
    if (link) {
      var open = el('a', 'btn btn-sm btn-secondary', tr('problems.open', 'Open'));
      open.href = link;
      tools.appendChild(open);
    }
    if (problem.acknowledged || problem.snoozed_until) {
      tools.appendChild(el('span', 'problems-note', problem.acknowledged
        ? tr('problems.acknowledged', 'Acknowledged')
        : tr('problems.snoozed', 'Snoozed')));
    } else {
      var ack = el('button', 'btn btn-sm btn-secondary', tr('problems.ack', 'Acknowledge'));
      ack.type = 'button';
      ack.addEventListener('click', function () { decide(problem.id, 'ack'); });
      var snooze = el('button', 'btn btn-sm btn-secondary', tr('problems.snooze', 'Snooze for a day'));
      snooze.type = 'button';
      snooze.addEventListener('click', function () { decide(problem.id, 'snooze'); });
      tools.appendChild(ack);
      tools.appendChild(snooze);
    }
    // A problem report about this one (REMAINING 3.1), shown whole before it is sent.
    if (typeof window.kazmaReportProblem === 'function') {
      var report = el('button', 'btn btn-sm btn-ghost', tr('support.report_this', 'Report this'));
      report.type = 'button';
      report.addEventListener('click', function () {
        window.kazmaReportProblem({ note: String(problem.title || ''), where: 'problem:' + problem.id });
      });
      tools.appendChild(report);
    }
    main.appendChild(tools);
    return item;
  }

  function render(data) {
    var list = document.getElementById('problems-list');
    if (!list) return;
    var loading = document.getElementById('problems-loading');
    var empty = document.getElementById('problems-empty');
    var error = document.getElementById('problems-error');
    var unreadable = document.getElementById('problems-unreadable');
    if (loading) loading.hidden = true;
    if (error) error.hidden = true;
    list.textContent = '';
    var problems = (data && data.problems) || [];
    problems.forEach(function (p) { list.appendChild(row(p)); });
    if (empty) empty.hidden = problems.length > 0;
    if (unreadable) {
      var names = (data && data.unreadable) || [];
      unreadable.hidden = names.length === 0;
      if (names.length) {
        unreadable.textContent = tr('problems.unreadable', 'Could not be read:') + ' '
          + names.map(sourceLabel).join(', ');
      }
    }
    var toggle = document.getElementById('problems-show-hidden');
    if (toggle) toggle.hidden = !(data && (data.hidden || state.hidden));
  }

  async function load() {
    if (!document.getElementById('problems-list')) return;
    var data = window.kazmaGetJson
      ? await window.kazmaGetJson('/api/problems' + (state.hidden ? '?hidden=1' : ''))
      : null;
    if (!data) {
      var loading = document.getElementById('problems-loading');
      var error = document.getElementById('problems-error');
      if (loading) loading.hidden = true;
      if (error) {
        error.hidden = false;
        error.textContent = tr('problems.error', 'Open problems could not be read right now.');
      }
      return;
    }
    render(data);
  }

  function init() {
    var toggle = document.getElementById('problems-show-hidden');
    if (toggle) {
      toggle.addEventListener('click', function () {
        state.hidden = !state.hidden;
        toggle.textContent = state.hidden
          ? tr('problems.hide_hidden', 'Hide acknowledged and snoozed')
          : tr('problems.show_hidden', 'Show acknowledged and snoozed');
        load();
      });
    }
    load();
    state.timer = setInterval(function () {
      if (document.visibilityState === 'visible') load();
    }, POLL_MS);
    if (typeof window.kazmaAddLeaveHook === 'function') {
      window.kazmaAddLeaveHook(function () {
        if (state.timer) clearInterval(state.timer);
        state.timer = null;
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
