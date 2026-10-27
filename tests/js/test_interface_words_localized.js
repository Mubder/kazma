// AUD-120: interface words and dates follow the page's language.
//
// - The approval panel's patch preview said "3 file(s):" in English on every
//   page; it is the catalog's plural form now (window.kazmaCount).
// - The Dashboard's answer-quality list dated each turn with the browser's
//   own toLocaleString(); it goes through window.KazmaFormat (the page's
//   language, Latin digits in Arabic).
// - The Research compare table's labels are checked by the script-English
//   gate (tests/test_scripts_have_no_english.py, HTML builders).
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS = path.resolve(__dirname, '..', '..', 'kazma-ui', 'kazma_ui', 'static', 'js');

function slice(src, from, to) {
  const start = src.indexOf(from);
  const end = src.indexOf(to, start);
  assert.ok(start >= 0 && end > start, 'markers not found: ' + from);
  return src.slice(start, end);
}

function patchPreview(windowStub) {
  const src = fs.readFileSync(path.join(JS, 'hitl_approval.js'), 'utf8');
  const body = slice(src, 'function _hitlPatchArgsHtml(', 'function safeIcon(');
  const ctx = vm.createContext({
    window: windowStub,
    JSON,
    String,
    Array,
    escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  });
  vm.runInContext(body, ctx);
  return ctx._hitlPatchArgsHtml;
}

const PATCHES = {
  patches: [
    { path: 'a.py', old_string: 'x', new_string: 'y' },
    { path: 'b.py', old_string: 'p', new_string: 'q' },
    { path: 'c.py', old_string: 'm', new_string: 'n' },
  ],
};

test('the patch count is the catalog plural form, in the page language', () => {
  const asked = [];
  const render = patchPreview({
    kazmaCount: (key, n) => { asked.push([key, n]); return n + ' ملفات:'; },
    t: (key) => (key === 'dashboard.hitl_patch_preview' ? 'معاينة التعديل' : key),
  });
  const html = render('file_apply_patch_set', PATCHES);
  assert.deepStrictEqual(asked, [['dashboard.hitl_patch_files', 3]]);
  assert.ok(html.includes('<div class="hitl-diff-file">3 ملفات:</div>'), html);
  assert.ok(html.includes('aria-label="معاينة التعديل"'), html);
  assert.ok(!html.includes('file(s)'), html);
});

test("each file's line keeps its own row class", () => {
  const render = patchPreview({ kazmaCount: (k, n) => String(n), t: (k) => k });
  const html = render('file_apply_patch_set', PATCHES);
  assert.ok(html.includes('<div class="hitl-diff-file">1. a.py</div>'), html);
  assert.ok(html.includes('<div class="hitl-diff-del">- x</div>'), html);
  assert.ok(html.includes('<div class="hitl-diff-add">+ y</div>'), html);
});

function element(tag) {
  return {
    tag, children: [], textContent: '', style: {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
  };
}

function renderQuality(windowStub) {
  const src = fs.readFileSync(path.join(JS, 'dashboard.js'), 'utf8');
  const body = slice(src, 'function renderAnswerQuality(', 'function fetchStatusFallback()');
  const ctx = vm.createContext({
    window: windowStub,
    document: { createElement: element, createTextNode: (text) => ({ textContent: text }) },
    String, Date, encodeURIComponent,
  });
  vm.runInContext(body, ctx);
  const status = element('div');
  const counts = element('div');
  const recent = element('ul');
  ctx.renderAnswerQuality(
    { totals: { turns: 1 }, recent: [{ ts: '2026-10-09T12:00:00Z', turn_id: 't1', signals: ['turn_failed'] }] },
    status, counts, recent,
  );
  return recent.children[0].children[0].textContent;
}

test('the answer-quality date goes through KazmaFormat', () => {
  const seen = [];
  const text = renderQuality({
    tOr: (_k, fallback) => fallback,
    KazmaFormat: { dateTime: (v) => { seen.push(v); return '٩ أكتوبر 2026'; } },
  });
  assert.deepStrictEqual(seen, ['2026-10-09T12:00:00Z']);
  assert.ok(text.startsWith('٩ أكتوبر 2026 — '), text);
});
