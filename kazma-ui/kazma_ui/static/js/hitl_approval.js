/**
 * HITL (Human-in-the-Loop) Approval Panel
 *
 * Polls GET /api/pending-approvals for threads paused on interrupt(),
 * renders approval cards with tool name + arguments, and wires up
 * Approve / Deny buttons that POST to /api/approve/{thread_id}.
 *
 * Authentication uses an HttpOnly cookie (set by the server when
 * KAZMA_SECRET is configured). No secret is exposed in page source.
 * All fetch calls use credentials: 'same-origin' to send the cookie.
 */
(function () {
  'use strict';

  const POLL_INTERVAL_MS = 5000;
  const containerId = 'hitl-approvals-panel';
  // Hoisted poll timer so re-init (soft-nav re-inject of this script, or a
  // second initHitlApproval call) doesn't stack a second setInterval on top
  // of the first — each extra poller doubled the /api/pending-approvals rate
  // forever (no clearInterval anywhere) (audit finding).
  let _pollTimer = null;
  function _startPolling() {
    if (_pollTimer) return;
    _pollTimer = setInterval(refreshPending, POLL_INTERVAL_MS);
  }

  function t(key, fallback) {
    try {
      if (typeof window.t === 'function') {
        var v = window.t(key);
        if (v && v !== key) return v;
      }
    } catch (e) { /* ignore */ }
    return fallback || key;
  }

  function safeIcon(name) {
    try {
      if (window.KazmaIcons && typeof KazmaIcons.span === 'function') {
        var html = KazmaIcons.span(name);
        return (html && html !== 'undefined') ? html : '';
      }
    } catch (e) { /* ignore */ }
    return '';
  }

  function approvalHeaders() {
    return { 'Content-Type': 'application/json' };
  }

  function escapeHtml(text) {
    // Escape quotes too — the output is used inside HTML attributes
    // (data-tool / data-opt / data-tcid), where the textContent→innerHTML
    // trick left " unescaped and allowed attribute injection.
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderApprovals(pending) {
    var panel = document.getElementById(containerId);
    if (!panel) return;

    var list = panel.querySelector('.hitl-approval-list');
    var badge = panel.querySelector('.hitl-approval-count');
    var empty = panel.querySelector('.hitl-approval-empty');
    var clearBtn = document.getElementById('hitl-clear-all-btn');

    if (!list) return;

    if (!Array.isArray(pending)) pending = [];
    // One card per thread: two registry rows for the same pause (native id
    // + hash id) used to render two identical cards; the leftover 409'd.
    var seenTid = {};
    pending = pending.filter(function (item) {
      var tid = String((item && item.thread_id) || '');
      if (!tid) return true;
      if (seenTid[tid]) return false;
      seenTid[tid] = true;
      return true;
    });

    if (badge) {
      badge.textContent = String(pending.length);
      badge.style.display = pending.length > 0 ? 'inline-block' : 'none';
    }

    if (clearBtn) {
      clearBtn.style.display = pending.length > 0 ? 'inline-block' : 'none';
      clearBtn.textContent = t('dashboard.clear_all', 'Clear All');
    }

    if (pending.length === 0) {
      list.innerHTML = '';
      if (empty) empty.style.display = 'block';
      panel.classList.remove('has-pending');
      return;
    }

    if (empty) empty.style.display = 'none';
    panel.classList.add('has-pending');

    list.innerHTML = pending.map(function (item) {
      var threadId = escapeHtml(item.thread_id || '');
      var toolName = escapeHtml(item.tool_name || item.tool || 'unknown');
      if (toolName === 'undefined' || toolName === 'null') toolName = 'unknown';
      var message = item.message != null ? String(item.message) : '';
      if (message === 'undefined' || message === 'null') message = '';
      message = escapeHtml(message);
      var argsStr = escapeHtml(JSON.stringify(item.arguments || item.args || {}, null, 2));
      var icon = safeIcon('wrench');
      // Phase 3: semantic clarify/confirm → render per-option buttons
      var _kind = item.kind || 'security';
      if (_kind.indexOf('semantic_') === 0) {
        var _si = (item.items && item.items[0]) || {};
        var _sq = escapeHtml(_si.question || message || 'Needs clarification');
        var _so = (_si.options || []);
        var _st = escapeHtml(_si.tool_call_id || '');
        var _ob = _so.map(function(o) {
          var c = o.id === 'cancel' ? 'btn-danger' : 'btn-primary';
          return '<button class="btn btn-sm ' + c + ' hitl-sem-opt-btn" data-thread-id="' + threadId +
                 '" data-tcid="' + _st + '" data-opt="' + escapeHtml(o.id) + '">' +
                 escapeHtml(o.label || o.id) + '</button>';
        }).join(' ');
        return (
          '<div class="hitl-approval-card" data-thread-id="' + threadId + '">' +
          '  <div class="hitl-approval-header"><span class="hitl-tool-name">❓ Clarification</span>' +
            (threadId ? '<span class="hitl-thread-id">' + threadId + '</span>' : '') + '</div>' +
          '  <div class="hitl-approval-message" dir="auto">' + _sq + '</div>' +
          '  <div class="hitl-approval-actions">' + _ob + '</div>' +
          '</div>'
        );
      }

      // Always-HITL tools (X ToU fail-safes) re-prompt even under YOLO —
      // hide the button so YOLO never reads as "approve once".
      var yoloOk = item.yolo_allowed !== false;
      return (
        '<div class="hitl-approval-card" data-thread-id="' + threadId + '">' +
        '  <div class="hitl-approval-header">' +
        '    <span class="hitl-tool-name">' +
        (icon ? icon + ' ' : '') + toolName + '</span>' +
        (threadId
          ? '    <span class="hitl-thread-id" title="thread">' + threadId + '</span>'
          : '') +
        '  </div>' +
        (message ? '<div class="hitl-approval-message" dir="auto">' + message + '</div>' : '') +
        '  <div class="hitl-approval-args"><pre>' + argsStr + '</pre></div>' +
        (yoloOk ? '' :
        '  <div class="hitl-approval-message" dir="auto" style="font-size:0.75rem;">' +
        escapeHtml(t('dashboard.hitl_always_note', 'This tool always requires approval — YOLO cannot skip it.')) +
        '</div>') +
        '  <div class="hitl-approval-actions">' +
        '    <button class="btn btn-sm btn-success hitl-approve-btn" data-thread-id="' + threadId + '" data-scope="once">' +
        '      ' + safeIcon('check') + ' ' + t('dashboard.hitl_once', 'Once') +
        '    </button>' +
        '    <button class="btn btn-sm btn-primary hitl-approve-tool-btn" data-thread-id="' + threadId + '" data-scope="tool" data-tool="' + toolName + '">' +
        '      ' + t('dashboard.hitl_allow_tool', 'Allow tool') +
        '    </button>' +
        (yoloOk
          ? '    <button class="btn btn-sm btn-warning hitl-approve-yolo-btn" data-thread-id="' + threadId + '" data-scope="yolo">' +
            '      ' + t('dashboard.hitl_yolo', 'YOLO') +
            '    </button>'
          : '') +
        '    <button class="btn btn-sm btn-danger hitl-deny-btn" data-thread-id="' + threadId + '">' +
        '      ' + safeIcon('x') + ' ' + t('dashboard.hitl_deny', 'Deny') +
        '    </button>' +
        '    <span class="hitl-approval-status" style="display:none;"></span>' +
        '  </div>' +
        '</div>'
      );
    }).join('');

    list.querySelectorAll('.hitl-approve-btn, .hitl-approve-tool-btn, .hitl-approve-yolo-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        submitApproval(
          btn.getAttribute('data-thread-id'),
          true,
          btn,
          btn.getAttribute('data-scope') || 'once',
          btn.getAttribute('data-tool') || ''
        );
      });
    });
    list.querySelectorAll('.hitl-deny-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        submitApproval(btn.getAttribute('data-thread-id'), false, btn, 'once', '');
      });
    });
    // Phase 3: wire semantic per-option buttons
    list.querySelectorAll('.hitl-sem-opt-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tid = btn.getAttribute('data-thread-id');
        var tcid = btn.getAttribute('data-tcid');
        var optId = btn.getAttribute('data-opt');
        var card = btn.closest('.hitl-approval-card');
        if (card) card.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
        var act = card ? card.querySelector('.hitl-approval-actions') : null;
        if (act) act.innerHTML = '<span>Resolving…</span>';
        var payload = { action: optId === 'cancel' ? 'deny' : 'approve', scope: 'once', choices: {} };
        payload.choices[tcid] = optId;
        fetch('/api/approve/' + encodeURIComponent(tid), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }).then(function() { refreshPending(); })
          .catch(function() { if (act) act.innerHTML = '<span class="text-danger">Failed</span>'; });
      });
    });
  }

  async function submitApproval(threadId, approve, btn, scope, tool) {
    var card = btn.closest('.hitl-approval-card');
    var statusEl = card ? card.querySelector('.hitl-approval-status') : null;
    var buttons = card ? card.querySelectorAll('button') : [];
    scope = scope || 'once';
    tool = tool || '';

    buttons.forEach(function (b) { b.disabled = true; });
    if (statusEl) {
      statusEl.textContent = approve
        ? t('dashboard.hitl_approving', 'Approving…')
        : t('dashboard.hitl_denying', 'Denying…');
      statusEl.style.display = 'inline-block';
    }

    var payload = {
      action: approve ? 'approve' : 'deny',
      scope: scope,
      tool: tool,
    };

    var url = '/api/approve/' + encodeURIComponent(threadId);

    if (window.KazmaApp && window.KazmaApp.setIsThinking) {
      window.KazmaApp.setIsThinking(
        true,
        approve
          ? t('dashboard.hitl_running', 'Running approved tool…')
          : t('dashboard.hitl_denying', 'Denying…')
      );
    }

    try {
      var resp = await fetch(url, {
        method: 'POST',
        headers: approvalHeaders(),
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });
      var body = {};
      try { body = await resp.json(); } catch (eParse) { body = {}; }
      if (window.KazmaApp && window.KazmaApp.setIsThinking) {
        window.KazmaApp.setIsThinking(false);
      }
      if (resp.status === 409 || body.reason === 'not_pending') {
        if (body.running) {
          if (statusEl) {
            statusEl.textContent = t('dashboard.hitl_running', 'Running approved tool…');
            statusEl.className = 'hitl-approval-status';
            statusEl.style.display = 'inline-block';
          }
          if (card) card.remove();
          refreshPending();
          return;
        }
        // Twin/ghost card of a pause already claimed on the other row.
        // Remove it — refreshPending used to redraw live buttons forever.
        if (card) card.remove();
        refreshPending();
        return;
      }
      if (!resp.ok || body.ok === false) {
        var errText = String(body.error || body.reason || ('HTTP ' + resp.status));
        if (statusEl) {
          statusEl.innerHTML =
            (window.KazmaIcons ? KazmaIcons.span('alert') : '') + ' ' + escapeHtml(errText) +
            ' <a href="#" class="hitl-dismiss-link" style="margin-left:8px;color:var(--text-danger);text-decoration:underline;">' +
            t('dashboard.hitl_dismiss', 'Dismiss') + '</a>';
          statusEl.className = 'hitl-approval-status hitl-status-error';
          statusEl.style.display = 'inline-block';
          var dismissLink = statusEl.querySelector('.hitl-dismiss-link');
          if (dismissLink) {
            dismissLink.addEventListener('click', function(e) {
              e.preventDefault();
              if (card) card.remove();
              refreshPending();
            });
          }
        }
        buttons.forEach(function (b) { b.disabled = false; });
        return;
      }
      if (statusEl) {
        statusEl.textContent = approve
          ? t('dashboard.hitl_approved', 'Approved — running')
          : t('dashboard.hitl_denied', 'Denied');
        statusEl.className = 'hitl-approval-status hitl-status-' + (approve ? 'ok' : 'denied');
        statusEl.style.display = 'inline-block';
      }
      setTimeout(function () {
        if (card) card.remove();
        refreshPending();
      }, 1500);
    } catch (err) {
      if (window.KazmaApp && window.KazmaApp.setIsThinking) {
        window.KazmaApp.setIsThinking(false);
      }
      if (statusEl) {
        statusEl.innerHTML =
          (window.KazmaIcons ? KazmaIcons.span('alert') : '') + ' ' +
          escapeHtml(String((err && err.message) || err || 'Approval failed')) +
          ' <a href="#" class="hitl-dismiss-link" style="margin-left:8px;color:var(--text-danger);text-decoration:underline;">' +
          t('dashboard.hitl_dismiss', 'Dismiss') + '</a>';
        statusEl.className = 'hitl-approval-status hitl-status-error';
        statusEl.style.display = 'inline-block';
        var dismissCatch = statusEl.querySelector('.hitl-dismiss-link');
        if (dismissCatch) {
          dismissCatch.addEventListener('click', function(e) {
            e.preventDefault();
            if (card) card.remove();
            refreshPending();
          });
        }
      }
      buttons.forEach(function (b) { b.disabled = false; });
    }
  }

  function _bindHitlResolvedRefresh() {
    if (window.__kazmaHitlResolvedBound) return;
    window.__kazmaHitlResolvedBound = true;
    try {
      window.addEventListener('kazma:hitl-resolved', function () {
        refreshPending();
      });
    } catch (eEv) { /* ignore */ }
    try {
      window.addEventListener('storage', function (ev) {
        if (ev && ev.key === 'kazma:hitl-resolved') refreshPending();
      });
    } catch (eSt) { /* ignore */ }
  }
  _bindHitlResolvedRefresh();

  async function refreshPending() {
    try {
      var resp = await fetch('/api/pending-approvals', { credentials: 'same-origin' });
      if (!resp.ok) return;
      var data = await resp.json();
      renderApprovals(data.pending || []);
    } catch (err) {
      // Silently ignore — will retry on next poll
    }
  }

  function initHitlApproval() {
    var clearBtn = document.getElementById('hitl-clear-all-btn');
    if (clearBtn && !clearBtn.dataset.wired) {
      clearBtn.dataset.wired = 'true';
      clearBtn.textContent = t('dashboard.clear_all', 'Clear All');
      clearBtn.addEventListener('click', async function () {
        var msg = t('dashboard.clear_all_confirm', 'Clear all pending approvals?');
        var ok = window.kazmaConfirm
          ? await window.kazmaConfirm({ title: t('dashboard.clear_all', 'Clear All'), message: msg, danger: true, confirmText: t('dashboard.clear_all', 'Clear All') })
          : await window.confirm(msg);
        if (!ok) return;
        clearBtn.disabled = true;
        try {
          await fetch('/api/pending-approvals/clear', { method: 'POST', credentials: 'same-origin' });
        } catch (e) {}
        clearBtn.disabled = false;
        refreshPending();
      });
    }

    if (document.getElementById(containerId)) {
      refreshPending();
      _startPolling();
      window.KazmaHITL = { refresh: refreshPending };
      return;
    }

    refreshPending();
    _startPolling();
    window.KazmaHITL = { refresh: refreshPending };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHitlApproval);
  } else {
    initHitlApproval();
  }

  window.__hitl_approval__ = {
    renderApprovals: renderApprovals,
    refreshPending: refreshPending,
  };
})();
