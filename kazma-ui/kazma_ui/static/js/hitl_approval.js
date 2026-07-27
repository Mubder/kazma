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
    var div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
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
        '  <div class="hitl-approval-actions">' +
        '    <button class="btn btn-sm btn-success hitl-approve-btn" data-thread-id="' + threadId + '" data-scope="once">' +
        '      ' + safeIcon('check') + ' ' + t('dashboard.hitl_once', 'Once') +
        '    </button>' +
        '    <button class="btn btn-sm btn-primary hitl-approve-tool-btn" data-thread-id="' + threadId + '" data-scope="tool" data-tool="' + toolName + '">' +
        '      ' + t('dashboard.hitl_allow_tool', 'Allow tool') +
        '    </button>' +
        '    <button class="btn btn-sm btn-warning hitl-approve-yolo-btn" data-thread-id="' + threadId + '" data-scope="yolo">' +
        '      ' + t('dashboard.hitl_yolo', 'YOLO') +
        '    </button>' +
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

    var sseFn = (window.KazmaStream && (KazmaStream.ssePost || KazmaStream.sse));
    if (!sseFn) {
      if (statusEl) {
        statusEl.style.display = 'inline';
        statusEl.textContent = t('dashboard.hitl_stream_unavailable', 'Streaming unavailable');
        statusEl.className = 'hitl-approval-status hitl-status-error';
      }
      if (window.KazmaApp && window.KazmaApp.setIsThinking) {
        window.KazmaApp.setIsThinking(false);
      }
      return;
    }
    sseFn(url, payload, {
      onEvent: function(type, data) {
        if (type === 'status' && data && data.content && statusEl) {
          statusEl.textContent = data.content;
          if (window.KazmaApp && window.KazmaApp.setIsThinking) {
            window.KazmaApp.setIsThinking(true, data.content);
          }
        }
      },
      onToken: function(d) {
        if (window.KazmaApp) {
          if (window.KazmaApp.setIsThinking) window.KazmaApp.setIsThinking(false);
          if (window.KazmaApp.appendToken && d && d.content) {
            window.KazmaApp.appendToken(d.content);
          }
        }
      },
      onToolCall: function(d) {
        if (window.KazmaApp) {
          if (window.KazmaApp.setIsThinking) {
            window.KazmaApp.setIsThinking(
              true,
              t('dashboard.hitl_executing', 'Executing tool: {name}…').replace(
                '{name}',
                (d && d.name) || 'tool'
              )
            );
          }
          if (window.KazmaApp.addToolCall) window.KazmaApp.addToolCall(d);
        }
      },
      onDone: function(d) {
        if (window.KazmaApp && window.KazmaApp.setIsThinking) {
          window.KazmaApp.setIsThinking(false);
        }
        if (statusEl) {
          statusEl.textContent = approve
            ? t('dashboard.hitl_approved', 'Approved — complete')
            : t('dashboard.hitl_denied', 'Denied');
          statusEl.className = 'hitl-approval-status hitl-status-' + (approve ? 'ok' : 'denied');
        }
        setTimeout(function () {
          if (card) card.remove();
          refreshPending();
        }, 1500);
      },
      onError: function(err) {
        if (window.KazmaApp && window.KazmaApp.setIsThinking) {
          window.KazmaApp.setIsThinking(false);
        }
        if (statusEl) {
          statusEl.innerHTML =
            '⚠ ' + escapeHtml(err) +
            ' <a href="#" class="hitl-dismiss-link" style="margin-left:8px;color:var(--text-danger);text-decoration:underline;">' +
            t('dashboard.hitl_dismiss', 'Dismiss') + '</a>';
          statusEl.className = 'hitl-approval-status hitl-status-error';
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
      }
    });
  }

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
        if (!confirm(t('dashboard.clear_all_confirm', 'Clear all pending approvals?'))) return;
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
      setInterval(refreshPending, POLL_INTERVAL_MS);
      window.KazmaHITL = { refresh: refreshPending };
      return;
    }

    refreshPending();
    setInterval(refreshPending, POLL_INTERVAL_MS);
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
