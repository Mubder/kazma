"use strict";
// "No research yet" answers reads that all came back (2026-10-15).
//
// The Research list merges three reads (tasks, papers, sessions). When they
// all failed, the page showed its "results unavailable" warning AND "No
// research yet" under it: the empty state claimed an answer nobody gave.
// With any read unavailable the list is incomplete, so an empty list now
// shows the warning alone; the Archived tab follows the same rule. Run
// through the real loader with fetch stubbed; the old renderer is the
// negative control (tests/test_empty_states_after_load.py states the rule).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(
  path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8',
).replace(/\r\n/g, '\n');

const EMPTY = { 'research_no_results': 'No research yet.', 'research_no_archived': 'No archived research.' };
const WARNING = 'research.ui.results_unavailable';

async function run(code, method, answer) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {} });
    return elements.get(id);
  };
  const window = {
    KAZMA_LANG: 'en',
    t: key => key,
    KAZMA_I18N: Object.assign({ [WARNING]: { en: 'Results unavailable', ar: 'النتائج غير متاحة' } }, EMPTY),
    KazmaFormat: { relative: () => 'now', date: () => 'today' },
  };
  vm.runInNewContext(code, {
    window,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element, hidden: false },
    fetch: async url => answer(url),
  });
  window.KazmaResearch[method]();
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  return element;
}

const refused = async () => ({ ok: false, status: 503, json: async () => ({ error: 'unavailable' }) });
const empty = async url => ({
  ok: true,
  json: async () => (url.includes('/sessions') ? { sessions: [] } : url.includes('/papers') ? { papers: [] } : { tasks: [] }),
});
// Sessions unavailable, the rest answered with nothing.
const partly = async url => (url.includes('/sessions') ? refused() : empty(url));

const CASES = [
  ['load', 'research-list', 'research-load-error', EMPTY.research_no_results],
  ['loadArchived', 'research-archived-list', 'research-archived-load-error', EMPTY.research_no_archived],
];

for (const [method, list, warn, emptyText] of CASES) {
  test(`${method}: every read failed -> the warning, never the empty state`, async () => {
    const el = await run(source, method, refused);
    assert.ok(!el(list).innerHTML.includes(emptyText), el(list).innerHTML);
    assert.equal(el(warn).style.display, 'block');
    assert.equal(el(warn).textContent, 'Results unavailable');
  });

  test(`${method}: one read failed and nothing came back -> no empty state`, async () => {
    const el = await run(source, method, partly);
    assert.ok(!el(list).innerHTML.includes(emptyText), el(list).innerHTML);
    assert.equal(el(warn).style.display, 'block');
  });

  test(`${method}: every read answered with nothing -> the empty state`, async () => {
    const el = await run(source, method, empty);
    assert.ok(el(list).innerHTML.includes(emptyText), el(list).innerHTML);
    assert.equal(el(warn).style.display, 'none');
  });
}

test('negative control: the renderer that ignored failures shows "No research yet" over them', async () => {
  const old = source
    .replace("el.innerHTML = listIncomplete ? '' : '<div", "el.innerHTML = '<div")
    .replace("el.innerHTML = archivedIncomplete ? '' : '<div", "el.innerHTML = '<div");
  assert.notEqual(old, source, 'the control must change the renderer');
  for (const [method, list, , emptyText] of CASES) {
    const el = await run(old, method, refused);
    assert.ok(el(list).innerHTML.includes(emptyText), `${method}: the control did not reproduce the bug`);
  }
});
