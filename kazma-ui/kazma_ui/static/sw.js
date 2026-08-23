/* Kazma Web Push service worker (Turn Delivery V2 plan P5).
 * Minimal RFC 8030 handler: wake on push, show the notification.
 * Served from /sw.js (root scope required for notification clicks). */

self.addEventListener('push', function (event) {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (e) { /* ignore */ }
  const title = payload.title || 'Kazma';
  const body = payload.body || 'Your task completed.';
  event.waitUntil(self.registration.showNotification(title, {
    body: body,
    tag: 'kazma-turn-complete',
    renotify: true,
    badge: '/static/img/kazma-icon.png',
    icon: '/static/img/kazma-icon.png',
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if ('focus' in c) return c.focus();
    }
    return clients.openWindow('/chat');
  }));
});
