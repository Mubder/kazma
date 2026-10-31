/* The Dashboard's Knowledge crawls card (2026-10-31).
 *
 * Reads GET /api/kb/outcomes when the page opens and every 60 s while it is
 * visible: the retained crawls' pages by outcome and reason, the share stored
 * or kept out by a rule against its target (crawl_jobs.PAGE_TARGET), and the
 * daily crawl check (web_acquire/canary.py). Built with DOM methods; the
 * interface words come from the catalog (kazmaT), the reasons from the
 * Knowledge page's own entries (knowledge.reason.*).
 */
(function () {
  'use strict';

  var POLL_MS = 60000;
  if (window.__kazmaCrawlPanel && window.__kazmaCrawlPanel.timer) {
    clearInterval(window.__kazmaCrawlPanel.timer);
  }
  var state = { timer: null };
  window.__kazmaCrawlPanel = state;

  function tr(key, fallback, vars) {
    return window.kazmaT ? window.kazmaT(key, fallback, vars) : fallback;
  }

  function el(tag, text, color) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (color) node.style.color = color;
    return node;
  }

  // Latin digits in either language, like every number the interface shows.
  function percent(value) {
    return (Math.round(value * 1000) / 10) + '%';
  }

  function render(data) {
    var body = document.getElementById('crawl-panel-body');
    if (!body) return;
    body.textContent = '';
    if (!data || !data.ok) {
      body.appendChild(el('p', tr('dashboard.crawls.unavailable', 'Crawl outcomes are unavailable right now.')));
      return;
    }
    var summary = data.summary || {};
    if (!summary.pages) {
      body.appendChild(el('p', tr('dashboard.crawls.none', 'No finished crawl yet.')));
    } else {
      var met = summary.rate >= summary.target;
      body.appendChild(el('p', tr('dashboard.crawls.rate', 'Pages stored or kept out by a rule: {rate} (target {target})',
        { rate: percent(summary.rate), target: percent(summary.target) }), met ? 'var(--success)' : 'var(--warning)'));
      if ((summary.reasons || []).length) {
        body.appendChild(el('div', tr('dashboard.crawls.reasons', 'Why pages were not stored')));
        var list = document.createElement('ul');
        list.style.margin = '4px 0 0 0';
        list.style.paddingInlineStart = '18px';
        summary.reasons.slice(0, 8).forEach(function (entry) {
          var item = el('li');
          item.appendChild(el('span', tr('knowledge.outcome.' + entry.outcome, entry.outcome),
            entry.outcome === 'failed' ? 'var(--danger)' : 'var(--text-secondary)'));
          item.appendChild(el('span', ' · ' + tr('knowledge.reason.' + entry.reason, entry.reason) + ' · '));
          var count = el('bdi', window.KazmaFormat ? window.KazmaFormat.number(entry.pages) : entry.pages);
          item.appendChild(count);
          list.appendChild(item);
        });
        body.appendChild(list);
      }
    }
    var canary = data.canary || {};
    var line;
    if (!canary.ran_at) {
      line = el('p', tr('dashboard.crawls.canary_never', 'Daily crawl check: not run yet'));
    } else {
      var when = window.KazmaFormat ? window.KazmaFormat.dateTime(canary.ran_at * 1000) : '';
      line = el('p', tr(canary.ok ? 'dashboard.crawls.canary_passed' : 'dashboard.crawls.canary_failed',
        canary.ok ? 'Daily crawl check passed: {when}' : 'Daily crawl check failed: {when}', { when: when }),
        canary.ok ? 'var(--success)' : 'var(--danger)');
      if (!canary.ok && canary.reason) {
        var reason = el('span', ' ' + canary.reason);
        reason.setAttribute('translate', 'no');
        line.appendChild(reason);
      }
    }
    body.appendChild(line);
  }

  // The first read happens whatever the tab's state (a Dashboard opened in a
  // background tab stayed empty until it had been shown for a minute); the
  // polls after it only while the page is seen, and once more when it is.
  function refresh(always) {
    if ((!always && document.visibilityState === 'hidden') || !document.getElementById('crawl-panel-body')) return;
    var get = window.kazmaGetJson;
    if (!get) return;
    get('/api/kb/outcomes').then(render);
  }

  function onVisible() {
    if (document.visibilityState === 'visible') refresh();
  }

  refresh(true);
  state.timer = setInterval(refresh, POLL_MS);
  document.addEventListener('visibilitychange', onVisible);
  if (window.kazmaAddLeaveHook) {
    window.kazmaAddLeaveHook(function () {
      clearInterval(state.timer);
      state.timer = null;
      document.removeEventListener('visibilitychange', onVisible);
    });
  }
})();
