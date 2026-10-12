"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8').replace(/\r\n/g, '\n');

async function archived(code, lang) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', style: {} });
    return elements.get(id);
  };
  const requests = [];
  const window = { KAZMA_LANG: lang,
    t: key => key === 'research.depth_deep' ? (lang === 'ar' ? 'عميق' : 'Deep') : key,
    KAZMA_I18N: { 'research.ui.restore': { en: 'Restore', ar: 'استعادة' } },
    KazmaFormat: { relative: () => 'قبل لحظات', date: () => 'اليوم' } };
  vm.runInNewContext(code, { window,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element },
    fetch: async url => {
      requests.push(url);
      return { ok: true, json: async () => url.includes('/sessions') ? { sessions: [
        { id: 'saved', topic: '<saved report>', depth: 'deep', status: 'done', created_at: Date.now() / 1000 },
        { id: 'interrupted', topic: 'Unfinished evidence', depth: 'deep', status: 'interrupted', created_at: Date.now() / 1000 },
      ] } : { tasks: [] } };
    },
  });
  window.KazmaResearch.loadArchived();
  for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
  return { html: element('research-archived-list').innerHTML, requests };
}

for (const lang of ['en', 'ar']) {
  test(`${lang}: saved and interrupted archive cards render from the real loader`, async () => {
    const result = await archived(source, lang);
    assert.match(result.html, /data-task-id="session:saved"/);
    assert.match(result.html, /data-task-id="session:interrupted"/);
    assert.match(result.html, /&lt;saved report&gt;/);
    assert.ok(result.html.includes(lang === 'ar' ? 'استعادة' : 'Restore'));
    assert.ok(result.requests.some(url => url.includes('/sessions?limit=50&archived=true')));
    assert.ok(!result.html.includes('data-act="resume"'), 'Restore the archived card before explicit resume');
  });
  test(`${lang}: former undefined resume variables leave a populated archive blank`, async () => {
    const start = source.indexOf('  function renderArchivedList(tasks) {');
    const marker = '    el.innerHTML = tasks.map(function (t) {';
    const insertion = source.indexOf(marker, start) + marker.length;
    assert.ok(start > 0 && insertion > start);
    const old = source.slice(0, insertion) + "\n      if (isSession && t.status === 'interrupted') actions += 'resume';" + source.slice(insertion);
    assert.equal((await archived(old, lang)).html, '');
  });
}
