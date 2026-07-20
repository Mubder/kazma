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

  /**
   * Build the headers for an approval/deny request.
   * @returns {Object<string, string>}
   */
  function approvalHeaders() {
    return { 'Content-Type': 'application/json' };
  }

  /**
   * Escape HTML to prevent injection from tool arguments.
   * @param {string} text
   * @returns {string}
   */
  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

  /**
   * Render the list of pending approvals into the panel.
   * @param {Array<Object>} pending
   */
  function renderApprovals(pending) {
    var panel = document.getElementById(containerId);
    if (!panel) return;

    var list = panel.querySelector('.hitl-approval-list');
    var badge = panel.querySelector('.hitl-approval-count');
    var empty = panel.querySelector('.hitl-approval-empty');

    if (!list) return;

    if (badge) {
      badge.textContent = String(pending.length);
      badge.style.display = pending.length > 0 ? 'inline-block' : 'none';
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
      var toolName = escapeHtml(item.tool_name || 'unknown');
      var message = escapeHtml(item.message || '');
      var argsStr = escapeHtml(JSON.stringify(item.arguments || {}, null, 2));

      return (
        '<div class="hitl-approval-card" data-thread-id="' + threadId + '">' +
        '  <div class="hitl-approval-header">' +
        '    <span class="hitl-tool-name">🔧 ' + toolName + '</span>' +
        '    <span class="hitl-thread-id">' + threadId + '</span>' +
        '  </div>' +
        (message ? '<div class="hitl-approval-message">' + message + '</div>' : '') +
        '  <div class="hitl-approval-args"><pre>' + argsStr + '</pre></div>' +
        '  <div class="hitl-approval-actions">' +
        '    <button class="btn btn-sm btn-success hitl-approve-btn" data-thread-id="' + threadId + '" data-scope="once">' +
        '      ✓ Once' +
        '    </button>' +
        '    <button class="btn btn-sm btn-primary hitl-approve-tool-btn" data-thread-id="' + threadId + '" data-scope="tool" data-tool="' + toolName + '">' +
        '      Allow tool' +
        '    </button>' +
        '    <button class="btn btn-sm btn-warning hitl-approve-yolo-btn" data-thread-id="' + threadId + '" data-scope="yolo">' +
        '      YOLO' +
        '    </button>' +
        '    <button class="btn btn-sm btn-danger hitl-deny-btn" data-thread-id="' + threadId + '">' +
        '      ✕ Deny' +
        '    </button>' +
        '    <span class="hitl-approval-status" style="display:none;"></span>' +
        '  </div>' +
        '</div>'
      );
    }).join('');

    // Wire up buttons
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
  }

  /**
   * Submit an approval or denial.
   * @param {string} threadId
   * @param {boolean} approve
   * @param {HTMLElement} btn - the clicked button
   */
  async function submitApproval(threadId, approve, btn, scope, tool) {
    var card = btn.closest('.hitl-approval-card');
    var statusEl = card ? card.querySelector('.hitl-approval-status') : null;
    var buttons = card ? card.querySelectorAll('button') : [];
    scope = scope || 'once';
    tool = tool || '';

    // Disable buttons while request is in-flight
    buttons.forEach(function (b) { b.disabled = true; });
    if (statusEl) {
      statusEl.textContent = approve ? 'Approving…' : 'Denying…';
      statusEl.style.display = 'inline-block';
    }

    try {
      var resp = await fetch('/api/approve/' + encodeURIComponent(threadId), {
        method: 'POST',
        headers: approvalHeaders(),
        body: JSON.stringify({
          action: approve ? 'approve' : 'deny',
          scope: scope,
          tool: tool,
        }),
        credentials: 'same-origin',
      });

      if (resp.status === 202) {
        if (statusEl) {
          statusEl.textContent = approve ? '✓ Approved — agent resuming' : '✕ Denied';
          statusEl.className = 'hitl-approval-status hitl-status-' + (approve ? 'ok' : 'denied');
        }
        // Remove the card after a short delay
        setTimeout(function () {
          if (card) card.remove();
          refreshPending();
        }, 1500);
      } else if (resp.status === 401) {
        if (statusEl) {
          statusEl.textContent = '⚠ Unauthorized (invalid secret)';
          statusEl.className = 'hitl-approval-status hitl-status-error';
        }
        buttons.forEach(function (b) { b.disabled = false; });
      } else {
        var data = await resp.json().catch(function () { return {}; });
        if (statusEl) {
          statusEl.textContent = '⚠ Error: ' + (data.error || resp.statusText);
          statusEl.className = 'hitl-approval-status hitl-status-error';
        }
        buttons.forEach(function (b) { b.disabled = false; });
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = '⚠ Network error';
        statusEl.className = 'hitl-approval-status hitl-status-error';
      }
      buttons.forEach(function (b) { b.disabled = false; });
    }
  }

  /**
   * Fetch pending approvals from the API and re-render.
   */
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

  /**
   * Initialize the HITL approval panel: inject markup, poll, and
   * listen for manual refresh requests.
   */
  function initHitlApproval() {
    // Only init once
    if (document.getElementById(containerId)) {
      refreshPending();
      return;
    }

    // The panel markup is already in the dashboard template; just wire polling.
    refreshPending();
    setInterval(refreshPending, POLL_INTERVAL_MS);

    // Allow other scripts to trigger a manual refresh
    window.KazmaHITL = { refresh: refreshPending };
  }

  // Initialize on DOMContentLoaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHitlApproval);
  } else {
    initHitlApproval();
  }

  // Expose for testing
  window.__hitl_approval__ = {
    renderApprovals: renderApprovals,
    refreshPending: refreshPending,
  };
})();
