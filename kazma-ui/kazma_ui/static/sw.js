/* Kazma's service worker: Web Push (Turn Delivery V2 plan P5) and the
 * installed app's offline page (REMAINING 4.1, kazma_ui/web_app.py).
 * Served from /sw.js (root scope required for notification clicks).
 *
 * It caches ONE page, /static/offline.html (and its icon), and answers with
 * it only a page navigation the server did not answer: Kazma restarting, the
 * computer asleep. Every other request goes to the server untouched. */

const OFFLINE_CACHE = 'kazma-offline-v1';
const OFFLINE_URL = '/static/offline.html';

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(OFFLINE_CACHE).then(function (cache) {
    return cache.addAll([OFFLINE_URL, '/static/img/app-icon-192.png']);
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) {
      return n.indexOf('kazma-offline-') === 0 && n !== OFFLINE_CACHE;
    }).map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(function () {
    return caches.match(OFFLINE_URL).then(function (page) {
      return page || new Response('Kazma is not answering right now.', {
        status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      });
    });
  }));
});

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
