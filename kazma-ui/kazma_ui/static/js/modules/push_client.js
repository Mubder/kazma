/* ═══════════════════════════════════════════════════════
   Kazma Push Client — Web Push subscription (plan P5)
   Covers Memory-Saver-DISCARDED tabs: a Service Worker wakes
   with the completion notification even when no tab survives.
   Graceful by design: unsupported / denied / server-off all
   silently no-op. Requires pywebpush on the server.
   ═══════════════════════════════════════════════════════ */

window.KazmaPushClient = (function() {
  'use strict';

  var _tried = false;

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(base64);
    var arr = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }

  /**
   * Register SW + subscribe. Called after Notification permission is
   * granted (user-gesture path from the send button). Idempotent per page.
   */
  async function ensureSubscribed() {
    if (_tried) return;
    _tried = true;
    try {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
      if (!window.Notification || Notification.permission !== 'granted') return;

      // Operator gate — same key the in-page notifications honor.
      const gate = await fetch('/api/notifications/turn-complete')
        .then(function(r) { return r.ok ? r.json() : null; })
        .catch(function() { return null; });
      if (gate && gate.enabled === false) return;

      const info = await fetch('/api/push/vapid-public-key')
        .then(function(r) { return r.ok ? r.json() : null; })
        .catch(function() { return null; });
      if (!info || !info.available || !info.public_key) return;

      const reg = await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      const sub = existing || await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(info.public_key),
      });
      const payload = sub.toJSON();
      await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription: payload }),
      }).catch(function() {});
    } catch (e) {
      console.debug('[KazmaPush] subscription skipped:', e && e.message);
    }
  }

  return { ensureSubscribed: ensureSubscribed };
})();
