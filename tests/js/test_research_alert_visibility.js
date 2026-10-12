/* A disconnected stream or unavailable list must not look healthy or empty. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const code = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setImmediate(r)); };

function setup(lang, failure) {
  const elements = new Map(), streams = [], requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, textContent: '', innerHTML: '', value: '',
      scrollIntoView() {}, classList: { add() {}, remove() {} }, appendChild() {} });
    return elements.get(id);
  };
  class EventSource {
    constructor(url) { this.url = url; this.handlers = {}; this.closed = false; streams.push(this); }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    close() { this.closed = true; }
  }
  const window = { KAZMA_LANG: lang, KAZMA_I18N: {
    'research.ui.results_unavailable': { en: 'Results unavailable; retry', ar: 'تعذر تحميل النتائج؛ أعد المحاولة' },
    'research.ui.connection_interrupted': { en: 'Progress disconnected; reconnecting', ar: 'انقطع اتصال التقدم؛ جارٍ الاتصال' },
  }, kazmaCount: (_, count) => String(count) };
  let failed = failure;
  vm.runInNewContext(code, { window, EventSource,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element, querySelectorAll: () => [],
      createElement: () => ({ style: {}, appendChild() {} }) },
    fetch: async url => {
      requests.push(url);
      if (failed === 'network') throw new Error('offline');
      return { ok: failed !== 'http', json: async () => {
        if (failed === 'invalid') return {};
        if (url.includes('/sessions/')) return { session: { id: 'run', topic: 'Topic', status: 'running', updated_at: 1, log: [] } };
        if (url.includes('/acquisition?')) return { text: 'Saved result' };
        return { tasks: [], sessions: [], papers: [] };
      } };
    },
  });
  return { page: window.KazmaResearch, elements, streams, requests, recover() { failed = null; } };
}

for (const lang of ['en', 'ar']) {
  for (const failure of ['http', 'network', 'invalid']) {
    test(`${lang}: ${failure} leaves a persistent result warning until successful refresh`, async () => {
      const f = setup(lang, failure);
      f.page.load(); f.page.loadArchived(); await flush();
      for (const id of ['research-load-error', 'research-archived-load-error']) {
        assert.equal(f.elements.get(id).style.display, 'block');
        assert.match(f.elements.get(id).textContent, lang === 'ar' ? /تعذر/ : /unavailable/);
      }
      f.recover(); f.page.load(); f.page.loadArchived(); await flush();
      assert.equal(f.elements.get('research-load-error').style.display, 'none');
      assert.equal(f.elements.get('research-archived-load-error').style.display, 'none');
    });
  }
  test(`${lang}: network loss warns without closing or failing the research`, async () => {
    const f = setup(lang);
    f.page.viewDetail('session:run'); await flush();
    const stream = f.streams[0]; assert.ok(stream);
    stream.onerror({});
    assert.equal(stream.closed, false);
    assert.equal(f.elements.get('research-live-connection').style.display, 'block');
    assert.match(f.elements.get('research-live-connection').textContent, lang === 'ar' ? /انقطع/ : /disconnected/);
    stream.handlers.heartbeat({});
    assert.equal(f.elements.get('research-live-connection').style.display, 'none');
    stream.onerror({ data: '{}' });
    assert.equal(f.elements.get('research-live-connection').style.display, 'none');
  });
  test(`${lang}: retained crawler result cannot delete or export a previous session`, async () => {
    const f = setup(lang);
    f.page.viewDetail('session:run'); await flush();
    f.page.viewAcquisitionJob('saved'); await flush();
    assert.equal(f.elements.get('research-detail-actions').style.display, 'none');
    assert.equal(f.elements.get('research-detail-output').textContent, 'Saved result');
    const before = f.requests.length;
    f.page.exportCurrent('markdown');
    assert.equal(f.requests.length, before);
    f.page.viewDetail('session:run'); await flush();
    assert.equal(f.elements.get('research-detail-actions').style.display, 'flex');
  });
}
