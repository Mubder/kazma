// AUD-106: the Workspace page hears the GitHub OAuth "done" message only from
// its own origin, and the callback tab posts it only to that origin.
//
// The page's window 'message' listener acted on any window's
// {type: 'github-oauth-done'} -- a page on another site that opened or
// framed Kazma could drive its GitHub refresh and its "connected" toast --
// and the callback posted the outcome with targetOrigin '*'.
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = path.join(ROOT, 'kazma-ui', 'kazma_ui', 'templates', 'workspace.html');
const CALLBACK = path.join(ROOT, 'kazma-gateway', 'kazma_gateway', 'routers', 'github.py');

function listenerSource(html) {
  const start = html.indexOf('const onMessage = (e) => {');
  const end = html.indexOf("window.addEventListener('message', onMessage);");
  assert.ok(start > 0 && end > start, 'the OAuth message listener was not found');
  return html.slice(start, end);
}

// Build the listener with a fake component as `this`, and count what it does.
function build(source) {
  const seen = { status: 0 };
  const component = {
    ghOAuthConnected: true,
    loadOAuthStatus() { seen.status += 1; return Promise.resolve(); },
    loadGitHubStatus() {},
  };
  const sandbox = {
    window: { location: { origin: 'https://kazma.example' } },
    KazmaStream: { toast() {} },
    t: (k) => k,
    component,
  };
  vm.createContext(sandbox);
  const fn = vm.runInContext(
    '(function () {\n' + source + '\nreturn onMessage;\n}).call(component)',
    sandbox,
  );
  return { onMessage: fn, seen };
}

const DONE = { type: 'github-oauth-done', success: true };

test('a message from another origin is ignored', () => {
  const { onMessage, seen } = build(listenerSource(fs.readFileSync(TEMPLATE, 'utf8')));
  onMessage({ origin: 'https://evil.example', data: DONE });
  assert.strictEqual(seen.status, 0);
});

test("the page's own callback tab is heard", () => {
  const { onMessage, seen } = build(listenerSource(fs.readFileSync(TEMPLATE, 'utf8')));
  onMessage({ origin: 'https://kazma.example', data: DONE });
  assert.strictEqual(seen.status, 1);
});

test('negative control: without the origin check a foreign message drives the page', () => {
  const source = listenerSource(fs.readFileSync(TEMPLATE, 'utf8'))
    .replace(/if \(e\.origin !== window\.location\.origin\) return;/, '');
  const { onMessage, seen } = build(source);
  onMessage({ origin: 'https://evil.example', data: DONE });
  assert.strictEqual(seen.status, 1);
});

test('the callback tab posts to its own origin, never to every window', () => {
  const py = fs.readFileSync(CALLBACK, 'utf8');
  const line = py.split('\n').find((l) => l.includes("type: 'github-oauth-done'"));
  assert.ok(line, 'the callback postMessage was not found');
  assert.ok(line.includes('window.location.origin'), line);
  assert.ok(!line.includes("'*')"), line);
});
