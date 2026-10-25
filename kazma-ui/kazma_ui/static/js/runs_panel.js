/* The Dashboard's Running work card (run supervision, kazma_core.runs).
 *
 * Reads GET /api/runs every 15 s while the page is visible: the incidents
 * (stalled, stuck or failed work, with the actions each offers) and the runs.
 * A button posts the owner's decision to /api/runs/incidents/{id}/act; the
 * first decision wins on every surface, and a refusal says why. The automatic
 * first steps (runbooks) are switched here, each off by default.
 *
 * Built with DOM methods: titles, reasons and diagnoses are the runs' own data
 * (shown as text, translate="no"); interface words come from the catalog
 * (kazmaT / kazmaCount) or, for states and actions, from the server in the
 * page's language.
 */
(function () {
  'use strict';

  var POLL_MS = 15000;
  if (window.__kazmaRunsPanel && window.__kazmaRunsPanel.timer) {
    clearInterval(window.__kazmaRunsPanel.timer);
  }
  var state = { timer: null, focused: false, kinds: {} };
  window.__kazmaRunsPanel = state;

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

  function when(epoch) {
    if (!epoch) return '';
    return window.KazmaFormat ? window.KazmaFormat.relative(epoch) : '';
  }

  function safeLink(link) {
    return typeof link === 'string' && link.charAt(0) === '/' && link.charAt(1) !== '/' ? link : '';
  }

  function toast(message, type) {
    if (typeof window.showToast === 'function') window.showToast(message, type || 'info');
  }

  function show(id, visible) {
    var node = document.getElementById(id);
    if (node) node.hidden = !visible;
  }

  function messageLine(row) {
    var notify = row.notify || '';
    if (notify === 'suppressed') {
      return row.quiet_reason === 'before_start'
        ? tr('runs.message_before_start', 'No message: it ended before Kazma started watching')
        : tr('runs.message_announced', 'No message: another Kazma notice already reports this');
    }
    var known = { delivered: 1, refused: 1, uncertain: 1, pending: 1, sending: 1 };
    return known[notify] ? tr('runs.message_' + notify, notify) : '';
  }

  function outcomeLine(row) {
    if (row.state === 'resolved') return tr('runs.outcome_resolved', 'Settled by itself: {reason}', { reason: row.outcome_message || '' });
    if (row.state === 'dismissed') return tr('runs.outcome_dismissed', 'Dismissed');
    if (row.state === 'expired') return tr('runs.outcome_expired', 'Expired unanswered');
    if (row.outcome_code === 'interrupted') return tr('runs.outcome_interrupted', 'Kazma stopped while the action ran; check the run before acting again.');
    if (row.decided_label) {
      return tr('runs.decided', '{action}: {outcome}', { action: row.decided_label, outcome: row.outcome_message || '' });
    }
    return '';
  }

  async function act(incidentId, action, button) {
    button.disabled = true;
    try {
      var body = await window.kazmaSave('/api/runs/incidents/' + encodeURIComponent(incidentId) + '/act',
        { method: 'POST', body: { action: action } });
      toast(tr('runs.action_done', 'Done: {message}', { message: (body && body.message) || '' }), 'success');
    } catch (err) {
      toast(tr('runs.action_refused', 'Not done: {message}', { message: (err && err.message) || '' }), 'error');
    }
    refresh();
  }

  function incidentCard(row) {
    var card = el('article', 'runs-incident runs-incident-' + (row.condition || ''));
    card.id = 'runs-incident-' + row.id;
    card.appendChild(el('div', 'runs-incident-head', (row.kind_label || '') + ' · ' + (row.condition_label || '')));
    if (row.title) card.appendChild(content('div', 'runs-incident-title', row.title));
    var meta = el('div', 'runs-incident-meta');
    meta.appendChild(el('span', '', tr('runs.noticed', 'Noticed {when}', { when: when(row.opened_at) })));
    var message = messageLine(row);
    if (message) meta.appendChild(el('span', '', message));
    card.appendChild(meta);
    if (row.detail) card.appendChild(content('p', 'runs-detail', tr('runs.reason', 'Reason: {detail}', { detail: row.detail })));
    if (row.effects === 'unknown') card.appendChild(el('p', 'runs-warning', tr('runs.effects_unknown', 'Its effects outside Kazma are unknown: check them before you retry.')));
    if (row.decided_platform === 'runbook') card.appendChild(el('p', 'runs-note', tr('runs.automatic', "Kazma's automatic first step")));
    var outcome = outcomeLine(row);
    if (outcome) card.appendChild(content('p', 'runs-note', outcome));
    if (row.diagnosis_state === 'running') {
      card.appendChild(el('p', 'runs-note', tr('runs.diagnosis_running', 'Diagnosing…')));
    } else if (row.diagnosis) {
      var box = el('div', 'runs-diagnosis');
      box.appendChild(el('strong', '', tr('runs.diagnosis_title', 'Diagnosis')));
      box.appendChild(content('p', '', row.diagnosis));
      card.appendChild(box);
    }
    var actions = el('div', 'runs-actions');
    (row.offered || []).forEach(function (item) {
      var danger = item.action === 'cancel' || item.action === 'close';
      var button = el('button', 'btn btn-sm ' + (danger ? 'btn-danger' : 'btn-secondary'), item.label);
      button.type = 'button';
      button.addEventListener('click', function () { act(row.id, item.action, button); });
      actions.appendChild(button);
    });
    var link = safeLink(row.link);
    if (link) {
      var a = el('a', 'btn btn-sm btn-ghost', tr('runs.open_page', 'Open its page'));
      a.href = link;
      actions.appendChild(a);
    }
    if (actions.childNodes.length) card.appendChild(actions);
    return card;
  }

  function runRow(run) {
    var li = el('li', 'runs-row' + (run.attention ? ' runs-row-attention' : ''));
    li.appendChild(el('span', 'runs-row-kind', run.kind_label || run.kind));
    li.appendChild(content('span', 'runs-row-title', run.title || run.run_id));
    li.appendChild(el('span', 'runs-row-state runs-state-' + run.state, run.state_label || run.state));
    var last = run.progress_at || run.started_at;
    if (last) li.appendChild(el('span', 'runs-row-when', tr('runs.progress', 'Last progress {when}', { when: when(last) })));
    if (run.progress_note) li.appendChild(content('span', 'runs-row-note', run.progress_note));
    var link = safeLink(run.link);
    if (link) {
      var a = el('a', 'runs-row-link', tr('runs.open_page', 'Open its page'));
      a.href = link;
      li.appendChild(a);
    }
    return li;
  }

  function render(data) {
    var live = (data.incidents || []).filter(function (r) { return r.state === 'open' || r.state === 'acting'; });
    var done = (data.incidents || []).filter(function (r) { return r.state !== 'open' && r.state !== 'acting'; });
    var active = (data.runs || []).filter(function (r) {
      return ['queued', 'running', 'waiting_human', 'stalled', 'slow'].indexOf(r.state) >= 0;
    });
    var attention = (data.runs || []).filter(function (r) { return r.attention; });
    state.kinds = data.kinds || {};

    var summary = document.getElementById('runs-summary');
    if (summary && window.kazmaCount) {
      summary.textContent = window.kazmaCount('runs.count_attention', attention.length) + ' · ' +
        window.kazmaCount('runs.count_active', active.length);
    }

    var incidents = document.getElementById('runs-incidents');
    incidents.replaceChildren.apply(incidents, live.map(incidentCard));

    var shown = active.concat(attention.filter(function (r) { return active.indexOf(r) < 0; }));
    var list = document.getElementById('runs-list');
    list.replaceChildren.apply(list, shown.map(runRow));

    var errors = Object.keys(data.errors || {});
    var errorBox = document.getElementById('runs-error');
    if (errors.length) {
      errorBox.textContent = errors.map(function (kind) {
        return tr('runs.kind_unreadable', '{kind}: could not be read ({reason})',
          { kind: state.kinds[kind] || kind, reason: data.errors[kind] });
      }).join(' · ');
    }
    show('runs-error', errors.length > 0);
    show('runs-supervisor-off', !(data.supervisor && data.supervisor.running));
    show('runs-empty', !live.length && !shown.length && !errors.length);

    var history = document.getElementById('runs-history-list');
    history.replaceChildren.apply(history, done.slice(0, 15).map(incidentCard));
    show('runs-history', done.length > 0);

    var panel = document.getElementById('runs-panel');
    var focus = panel ? panel.getAttribute('data-incident-id') : '';
    if (focus && !state.focused) {
      var target = document.getElementById('runs-incident-' + focus);
      if (target) {
        state.focused = true;
        target.classList.add('runs-incident-focus');
        target.scrollIntoView({ block: 'center' });
      }
    }
  }

  async function refresh() {
    if (!document.getElementById('runs-panel')) {
      clearInterval(state.timer);
      state.timer = null;
      return;
    }
    var data = await window.kazmaGetJson('/api/runs');
    show('runs-loading', false);
    if (!data || !data.ok) {
      var errorBox = document.getElementById('runs-error');
      if (errorBox) errorBox.textContent = (data && data.error) || tr('runs.load_failed', 'Could not read running work. Trying again shortly.');
      show('runs-error', true);
      return;
    }
    render(data);
  }

  async function loadRunbooks() {
    var box = document.getElementById('runs-runbook-list');
    if (!box) return;
    var data = await window.kazmaGetJson('/api/runs/runbooks');
    if (!data || !data.ok) return;
    box.replaceChildren.apply(box, data.runbooks.map(function (book) {
      var label = el('label', 'runs-runbook');
      var input = el('input');
      input.type = 'checkbox';
      input.checked = !!book.enabled;
      input.addEventListener('change', async function () {
        var wanted = {};
        wanted[book.name] = input.checked;
        try {
          await window.kazmaSave('/api/runs/runbooks', { method: 'PUT', body: wanted });
          toast(tr('runs.runbook_saved', 'Saved'), 'success');
        } catch (err) {
          input.checked = !input.checked;
          toast(tr('runs.runbook_failed', 'Not saved: {message}', { message: (err && err.message) || '' }), 'error');
        }
      });
      label.appendChild(input);
      label.appendChild(el('span', '', tr('runs.runbook.' + book.name, book.name)));
      return label;
    }));
  }

  function start() {
    if (!document.getElementById('runs-panel')) return;
    refresh();
    loadRunbooks();
    state.timer = setInterval(function () {
      if (document.visibilityState === 'visible') refresh();
    }, POLL_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
