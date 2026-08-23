/* ═══════════════════════════════════════════════════════
   Kazma Turn Visibility — hidden-tab awareness (plan P4)
   While the user is on another tab during a running turn:
   - document.title carries an activity badge + tool count
   - on terminal, a desktop Notification fires (if permitted)
   Zero network. Pure UI. Restores title on visibility.
   ═══════════════════════════════════════════════════════ */

window.KazmaTurnVisibility = (function() {
  'use strict';

  var _baseTitle = null;
  var _active = false;
  var _events = 0;
  var _lastTool = '';
  var _flashTimer = null;
  var _flashOn = false;

  function baseTitle() {
    if (_baseTitle == null) {
      _baseTitle = document.title.replace(/^[\u25CF\u2022]\s*/, '');
    }
    return _baseTitle;
  }

  function render() {
    if (!_active || !document.hidden) {
      document.title = baseTitle();
      return;
    }
    var badge = '\u25CF ' + (_events > 0 ? '(' + _events + ') ' : '');
    document.title = badge + baseTitle();
  }

  function ensurePermissionRequested() {
    // Called from a user-gesture path (send). Silently no-ops when denied
    // or unsupported; the localStorage toggle is the opt-out.
    try {
      if (!('Notification' in window)) return;
      if (Notification.permission === 'default') Notification.requestPermission();
    } catch (e) { /* ignore */ }
  }

  function enabled() {
    // Operator knob (Settings → notifications.turn_complete, served live at
    // /api/notifications/turn-complete) AND the per-browser instant override.
    if (_serverEnabled === false) return false;
    try {
      return window.localStorage.getItem('kazma.notifyOnComplete') !== '0';
    } catch (e) { return true; }
  }

  var _serverEnabled = true;
  // Consult the operator gate once at boot; a failed fetch fails open.
  try {
    fetch('/api/notifications/turn-complete')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (d && d.enabled === false) _serverEnabled = false;
      })
      .catch(function() { /* fail open */ });
  } catch (e) { /* ignore */ }

  function notifyTerminal(summary) {
    _active = false;
    if (_flashTimer) { clearInterval(_flashTimer); _flashTimer = null; }
    if (!document.hidden) { render(); return; }
    var text = String(summary || '').replace(/\s+/g, ' ').trim();
    if (text.length > 120) text = text.slice(0, 117) + '\u2026';
    // Title keeps a subtle done marker until the tab is shown again.
    document.title = '\u2713 ' + (text ? text + ' \u2014 ' : '') + baseTitle();
    _flashOn = false;
    _flashTimer = setInterval(function() {
      _flashOn = !_flashOn;
      document.title = _flashOn
        ? document.title
        : '\u2713 ' + baseTitle();
      if (!document.hidden) {
        clearInterval(_flashTimer);
        _flashTimer = null;
        render();
      }
    }, 1200);
    try {
      if (!enabled() || !('Notification' in window)) return;
      if (Notification.permission !== 'granted') return;
      var n = new Notification('Kazma \u2014 task finished', {
        body: text || 'Your task completed.',
        tag: 'kazma-turn-complete',
        silent: false,
      });
      n.onclick = function() {
        try { window.focus(); } catch (e) {}
        n.close();
      };
      setTimeout(function() { try { n.close(); } catch (e) {} }, 10000);
    } catch (e) { /* notifications are best-effort */ }
  }

  return {
    /** Call from a user gesture (send button) so permission may prompt once. */
    armPermission: ensurePermissionRequested,
    /** Live heartbeat while a turn runs (token/tool/status frames). */
    noteActivity: function(kind, name) {
      if (!_active) {
        _active = true;
        _events = 0;
      }
      _events++;
      if (kind === 'tool' && name) _lastTool = String(name);
      render();
    },
    beginTurn: function() {
      _active = true;
      _events = 0;
      _lastTool = '';
      render();
    },
    endTurn: function(summary) {
      notifyTerminal(summary);
    },
    /** Tab shown again — restore title immediately. */
    restore: function() {
      if (_flashTimer) { clearInterval(_flashTimer); _flashTimer = null; }
      _active = false;
      render();
      document.title = baseTitle();
    },
  };
})();

document.addEventListener('visibilitychange', function() {
  if (!document.hidden && window.KazmaTurnVisibility) {
    KazmaTurnVisibility.restore();
  }
});
