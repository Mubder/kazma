"use strict";
// A research run's finished paper is what the Research page shows for it.
//
// Live 2026-10-31: the page listed a Kuwait history run by its working
// report; the 29-page paper the agent wrote from it was on no page. A run's
// entry now names the paper (``final``: title, its PDF, its Markdown, kept in
// the run's folder), and the page lists the run by that paper: its title, a
// PDF button, the paper's text in the detail view with the working report a
// link. Run through the real loader and detail view with fetch stubbed; a run
// without a finished paper is the control.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(
  path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8',
).replace(/\r\n/g, '\n');

const RUN = 'research/reports/pipeline-17f8';
const FINISHED = {
  id: 'pipeline-17f8', topic: 'History of Kuwait from its earliest documented written mention',
  report_path: RUN + '/report.md', sources: 4, created_at: '2026-10-31T20:20:24Z',
  final: { title: 'Kuwait: From the First Written Mention to the Present Day',
    pdf_path: RUN + '/final/paper.pdf', md_path: RUN + '/final/paper.md' },
};
const PLAIN = { id: 'pipeline-2c2d', topic: 'Kuwait pearling', report_path: 'research/reports/pipeline-2c2d/report.md',
  sources: 9, created_at: '2026-10-31T19:13:00Z' };

async function page(papers) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', style: {}, scrollIntoView() {} });
    return elements.get(id);
  };
  const requested = [];
  const window = {
    KAZMA_LANG: 'en', t: key => key, KAZMA_I18N: {},
    KazmaFormat: { relative: () => 'now', date: () => 'today' },
    kazmaCount: (key, n) => n + ' sources',
  };
  vm.runInNewContext(source, {
    window,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element, hidden: false },
    fetch: async url => {
      requested.push(url);
      if (url.includes('/papers/file')) return { ok: true, text: async () => '# The paper text' };
      const body = url.includes('/sessions') ? { sessions: [] } : url.includes('/papers') ? { papers } : { tasks: [] };
      return { ok: true, json: async () => body };
    },
  });
  const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };
  window.KazmaResearch.load();
  await settle();
  return { window, element, requested, settle };
}

test('a run with a finished paper is listed by the paper, with its PDF', async () => {
  const { element } = await page([FINISHED]);
  const html = element('research-list').innerHTML;
  assert.match(html, /Kuwait: From the First Written Mention to the Present Day/);
  assert.doesNotMatch(html, /earliest documented written mention/);
  assert.match(html, /finished paper ·/);
  assert.ok(html.includes('href="/api/research/papers/file?path=' + encodeURIComponent(RUN + '/final/paper.pdf') + '"'), html);
});

test('its detail shows the paper, with the working report a link', async () => {
  const { window, element, requested, settle } = await page([FINISHED]);
  window.KazmaResearch.viewDetail('paper:pipeline-17f8');
  await settle();
  assert.equal(requested.at(-1), '/api/research/papers/file?path=' + encodeURIComponent(RUN + '/final/paper.md'));
  assert.equal(element('research-detail-output').textContent, '# The paper text');
  const meta = element('research-detail-meta').innerHTML;
  assert.match(meta, /Finished paper, written from this research run/);
  assert.ok(meta.includes(encodeURIComponent(RUN + '/report.md')) && /Working report/.test(meta), meta);
  assert.ok(meta.includes(encodeURIComponent(RUN + '/final/paper.pdf')), meta);
});

test('control: a run without a finished paper shows its report as before', async () => {
  const { window, element, requested, settle } = await page([PLAIN]);
  const html = element('research-list').innerHTML;
  assert.match(html, /pipeline ·/);
  assert.doesNotMatch(html, />PDF</);
  window.KazmaResearch.viewDetail('paper:pipeline-2c2d');
  await settle();
  assert.equal(requested.at(-1), '/api/research/papers/file?path=' + encodeURIComponent(PLAIN.report_path));
  assert.match(element('research-detail-meta').innerHTML, /Pipeline paper/);
});
