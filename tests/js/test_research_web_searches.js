"use strict";
// The Research list shows research first and the chat's web searches apart.
//
// Live 2026-10-31: the page read the newest 30 sessions, 26 of them web
// searches the chat had recorded, and listed them first as "[Chat] <query>
// · session · done · 1 source", above every report; the owner read them as
// Kazma's research. The loader now asks for research runs and searches apart
// (kind=research / kind=search), lists the research newest first, and puts
// the searches in their own group after it, said for what they are. Run
// through the real loader with fetch stubbed; the old mixed list is the
// negative control.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(
  path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8',
).replace(/\r\n/g, '\n');

const NOW = Date.parse('2026-10-31T20:00:00Z') / 1000;
const DEEP = { id: 'rs_deep1', topic: 'History of Kuwait', depth: 'deep', status: 'done', stage: 'complete', sources: 9, created_at: NOW - 7200, updated_at: NOW - 7000 };
const SEARCH = { id: 'rs_chat_aaaaaaaaaaaa', topic: 'Kuwait Failaka Dilmun archaeology ancient', depth: 'chat', status: 'done', stage: 'complete', sources: 1, created_at: NOW - 60, updated_at: NOW - 60, meta: { source: 'chat', tool: 'web_search', results: 8 } };
const PAPER = { id: 'pipeline-2c2d', topic: 'Kuwait: from the first mention to today', report_path: 'research/reports/pipeline-2c2d/report.md', sources: 12, created_at: '2026-10-31T19:13:00Z' };

function answer(url) {
  let body;
  if (url.includes('/sessions') && url.includes('kind=search')) body = { sessions: [SEARCH] };
  else if (url.includes('/sessions') && url.includes('kind=research')) body = { sessions: [DEEP] };
  else if (url.includes('/sessions')) body = { sessions: [SEARCH, DEEP] };
  else if (url.includes('/papers')) body = { papers: [PAPER] };
  else body = { tasks: [] };
  return { ok: true, json: async () => body };
}

async function run(code, { reopen = false } = {}) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {} });
    return elements.get(id);
  };
  const requested = [];
  const window = {
    KAZMA_LANG: 'en',
    t: key => key,
    KAZMA_I18N: {},
    KazmaFormat: { relative: () => 'now', date: () => 'today' },
    kazmaCount: (key, n) => (key === 'research.ui.results_n' ? n + ' results' : n + ' sources'),
  };
  vm.runInNewContext(code, {
    window,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element, hidden: false },
    fetch: async url => { requested.push(url); return answer(url); },
  });
  window.KazmaResearch.load();
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  if (reopen) {
    // The reader opens the group; the page's 15-second refresh draws the list again.
    element('research-list').querySelector = selector => (selector === 'details.research-searches' ? { open: true } : null);
    window.KazmaResearch.load();
    for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
  }
  return { html: element('research-list').innerHTML, requested };
}

const cards = html => [...html.matchAll(/data-task-id="([^"]+)"/g)].map(m => m[1]);

test('research is listed first, newest first; the searches follow in their own group', async () => {
  const { html, requested } = await run(source);
  assert.ok(requested.some(u => u.includes('kind=research')), requested.join(' '));
  assert.ok(requested.some(u => u.includes('kind=search')), requested.join(' '));
  const group = html.indexOf('<details class="research-searches"');
  assert.ok(group > 0, 'the searches have their own group');
  assert.deepEqual(cards(html.slice(0, group)), ['paper:pipeline-2c2d', 'session:rs_deep1']);
  assert.deepEqual(cards(html.slice(group)), ['session:rs_chat_aaaaaaaaaaaa']);
});

test('a search says it is a web search and how many results it found, not "session · 1 source"', async () => {
  const { html } = await run(source);
  const searchCard = html.slice(html.indexOf('<details class="research-searches"'));
  assert.match(searchCard, /\[Web search\]/);
  assert.match(searchCard, /web search from chat · 8 results/);
  assert.doesNotMatch(searchCard, /session ·/);
  assert.doesNotMatch(searchCard, /1 sources?/);
  assert.match(searchCard, /Web searches from chat/);
});

test('an opened group stays open when the list is drawn again', async () => {
  assert.ok(!(await run(source)).html.includes('research-searches" style="margin-top:12px;" open>'));
  assert.ok((await run(source, { reopen: true })).html.includes('research-searches" style="margin-top:12px;" open>'));
  // Negative control: without reading the drawn group back, the refresh closed it (live 2026-10-31).
  const old = source.replace('    if (shown) searchesOpen = !!shown.open;\n', '');
  assert.notEqual(old, source, 'the control must change the renderer');
  assert.ok(!(await run(old, { reopen: true })).html.includes(' open>'));
});

test('negative control: the old mixed list put the search above every report', async () => {
  const old = source
    .replace("readResults('/api/research/sessions?limit=30&kind=research', 'sessions'),", "readResults('/api/research/sessions?limit=30', 'sessions'),")
    .replace("allTasks.sort(function (a, b) { return startedAt(b) - startedAt(a); });", '')
    .replace('renderList(allTasks, searchTasks);', 'renderList(allTasks);');
  assert.notEqual(old, source, 'the control must change the loader');
  const { html } = await run(old);
  assert.equal(cards(html)[0], 'session:rs_chat_aaaaaaaaaaaa');
  assert.ok(!html.includes('research-searches'));
});
