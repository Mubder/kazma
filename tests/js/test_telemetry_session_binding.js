"use strict";
// Exercise the real store: no subscription before HTTP ownership binds,
// and a late response cannot reopen a chat the user switched away from.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/stores/agentStore.js'), 'utf8');

function harness(code = source) {
  let store;
  const pending = [];
  const sockets = [];
  class Socket {
    static OPEN = 1;
    static CONNECTING = 0;
    constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
    close() { this.readyState = 3; }
  }
  const Alpine = { store(name, value) { if (value) store = value; return store; } };
  vm.runInNewContext(code, {
    Alpine, WebSocket: Socket,
    window: { Alpine, location: { protocol: 'http:', host: 'localhost' } },
    document: { addEventListener() {} },
    console: { log() {}, warn() {}, error() {}, debug() {} },
    setTimeout, clearTimeout,
    fetch(url, options) { return new Promise(resolve => pending.push({ url, options, resolve })); },
  });
  store._startLivenessTicker = () => {};
  store._resetTurnState = () => {};
  return { store, pending, sockets };
}

async function switchRace(code) {
  const h = harness(code);
  const first = h.store.connect('first');
  assert.equal(h.sockets.length, 0);
  const second = h.store.connect('second');
  assert.equal(h.pending[1].url, '/api/chat/sessions');
  assert.equal(h.pending[1].options.method, 'POST');
  assert.equal(JSON.parse(h.pending[1].options.body).session_id, 'second');
  h.pending[1].resolve({ ok: true });
  await second;
  h.pending[0].resolve({ ok: true });
  await first;
  assert.equal(h.sockets.length, 1);
  assert.match(h.sockets[0].url, /\/ws\/chat\/second/);
}

(async () => {
  await switchRace(source);
  const h = harness();
  let retries = 0;
  h.store._scheduleReconnect = () => { retries += 1; };
  const gone = h.store.connect('gone');
  h.store.disconnect();
  h.pending[0].resolve({ ok: true });
  await gone;
  assert.equal(h.sockets.length, 0);
  const denied = h.store.connect('foreign-gateway');
  h.pending[1].resolve({ ok: false, status: 404 });
  await denied;
  assert.equal(h.sockets.length, 0);
  assert.equal(h.store.connectionStatus, 'disconnected');
  assert.equal(retries, 0);
  const unavailable = h.store.connect('temporary-outage');
  h.pending[2].resolve({ ok: false, status: 503 });
  await unavailable;
  assert.equal(retries, 1);
  // Negative control: removing the stale-attempt guard resurrects the first chat.
  const old = source.replace(
    'if (attempt !== this._connectionAttempt || this.sessionId !== sessionId) return;', '');
  await assert.rejects(() => switchRace(old), assert.AssertionError);
  console.log('PASS: bind before subscribe, refused bind, disconnect and switch race');
})().catch(error => { console.error(error); process.exitCode = 1; });
