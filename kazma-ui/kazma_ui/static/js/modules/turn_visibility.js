/* ═══════════════════════════════════════════════════════
   Kazma Turn Visibility — hidden-tab awareness (plan P4)
   While the user is on another tab during a running turn:
   - document.title carries an activity badge + tool count
   - on terminal, a desktop Notification fires (if permitted)
   Restores title on visibility. Network: the operator gate, read once,
   and Web Push, armed from the send gesture once permission is granted.
   ═══════════════════════════════════════════════════════ */

// A newer version can load into a page that ran this one (soft-nav loads
// shared scripts once per version): stop the old copy's title flashing
// before replacing it.
try {
  if (window.KazmaTurnVisibility && typeof window.KazmaTurnVisibility.restore === 'function') {
    window.KazmaTurnVisibility.restore();
  }
} catch (e) { /* the old copy is best-effort */ }

window.KazmaTurnVisibility = (function() {
  'use strict';

  var _baseTitle = null;
  var _active = false;
  var _events = 0;
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

  function armPush() {
    // Web Push reaches a tab the browser discarded (plan P5), and it needs the
    // permission asked for below. Until 2026-09-30 only the chat store's WS
    // send path armed it, and nothing sent over WS, so no browser subscribed.
    // ensureSubscribed checks the grant itself and runs once per page.
    try {
      if (window.KazmaPushClient && typeof KazmaPushClient.ensureSubscribed === 'function') {
        Promise.resolve(KazmaPushClient.ensureSubscribed()).catch(function() {});
      }
    } catch (e) { /* best-effort */ }
  }

  function ensurePermissionRequested() {
    // Called from a user-gesture path (send). Silently no-ops when denied
    // or unsupported; the localStorage toggle is the opt-out.
    try {
      if (!('Notification' in window)) return;
      if (Notification.permission === 'default') {
        var asked = Notification.requestPermission();
        if (asked && typeof asked.then === 'function') {
          asked.then(function(p) { if (p === 'granted') armPush(); }, function() {});
        }
        return;
      }
      if (Notification.permission === 'granted') armPush();
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

  /* A catalog string (window.t) or its English fallback. */
  function tr(key, fallback) {
    var text = typeof window.t === 'function' ? window.t(key) : key;
    return text && text !== key ? text : fallback;
  }

  /* The answer as plain text for the tab title and the desktop
     notification: they showed its markdown ("[Open this chat in Time
     Travel](/replay?...)", 2026-09-28). A link keeps its words; code
     fences, emphasis and heading/list marks go. */
  function plainText(summary) {
    return String(summary || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/`([^`]*)`/g, '$1')
      .replace(/(\*\*|__|~~|\*)(\S(?:[\s\S]*?\S)?)\1/g, '$2')
      .replace(/^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+[.)][ \t]+)/gm, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function notifyTerminal(summary) {
    _active = false;
    if (_flashTimer) { clearInterval(_flashTimer); _flashTimer = null; }
    if (!document.hidden) { render(); return; }
    var text = plainText(summary);
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
      var n = new Notification(tr('chat.notify_done_title', 'Kazma \u2014 task finished'), {
        body: text || tr('chat.notify_done_body', 'Your task completed.'),
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
      render();
    },
    endTurn: function(summary) {
      notifyTerminal(summary);
    },
    plainText: plainText,
    /** Tab shown again — restore title immediately. */
    restore: function() {
      if (_flashTimer) { clearInterval(_flashTimer); _flashTimer = null; }
      _active = false;
      render();
      document.title = baseTitle();
    },
  };
})();

// One listener per page, whichever version is running: it reads the current
// window.KazmaTurnVisibility.
if (!window.__kazmaTurnVisibilityBound) {
  window.__kazmaTurnVisibilityBound = true;
  document.addEventListener('visibilitychange', function() {
    if (!document.hidden && window.KazmaTurnVisibility) {
      window.KazmaTurnVisibility.restore();
    }
  });
}
