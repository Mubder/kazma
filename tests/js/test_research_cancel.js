"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/research.js'), 'utf8').replace(/\r\n/g, '\n');
const fixedCancel = `          if (data.session) applyLiveSession(data.session);
          if (data.session && data.session.status === 'cancelling') {
            toast(i18n('research.ui.cancelling', 'Cancelling; waiting for cleanup…'), 'info');
          } else if (data.session && ['done', 'error', 'cancelled'].indexOf(data.session.status) !== -1) {
            closeLiveStream();
          }`;
const originalCancel = `          toast(i18n('research_cancelled', 'Research cancelled'), 'info');
          if (data.session) applyLiveSession(data.session);
          closeLiveStream();`;

function setup(lang, code = source, status = 'running') {
  const elements = new Map(), streams = [], toasts = [], requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, { style: {}, textContent: '', innerHTML: '',
      scrollIntoView() {}, classList: { add() {}, remove() {} } });
    return elements.get(id);
  };
  class EventSource {
    constructor(url) { this.url = url; this.closed = false; this.handlers = {}; streams.push(this); }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    close() { this.closed = true; }
    emit(name, payload) { this.handlers[name]({ data: JSON.stringify(payload) }); }
  }
  const window = { KAZMA_LANG: lang, KAZMA_I18N: {
    'research.ui.state_cancelling': { en: 'cancelling', ar: 'جارٍ الإلغاء' },
    'research.ui.cancelling': { en: 'Cancelling; waiting for cleanup…', ar: 'جارٍ الإلغاء؛ بانتظار اكتمال التنظيف…' },
    research_cancelled: lang === 'ar' ? 'أُلغي البحث' : 'Research cancelled',
  }, kazmaCount: (key, count) => String(count), KazmaStream: { toast: msg => toasts.push(msg) } };
  vm.runInNewContext(code, { window, KazmaStream: window.KazmaStream, EventSource,
    document: { readyState: 'loading', addEventListener() {}, getElementById: element },
    fetch: async url => (requests.push(url), { ok: true, json: async () => ({ ok: true, session: {
      id: 'held', topic: 'topic', status: url.endsWith('/cancel') ? 'cancelling' : url.endsWith('/resume') ? 'pending' : status, log: [],
      updated_at: url.endsWith('/cancel') ? 20 : 10,
    } }) }),
  });
  window.KazmaResearch.load = () => {};
  return { page: window.KazmaResearch, elements, streams, toasts, requests };
}
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };

for (const lang of ['en', 'ar']) {
  for (const fixed of [true, false]) {
    test(`${lang}: ${fixed ? 'cancel waits for terminal receipt' : 'original closed stream early'}`, async () => {
      assert.ok(source.includes(fixedCancel));
      const fixture = setup(lang, fixed ? source : source.replace(fixedCancel, originalCancel));
      fixture.page.viewDetail('session:held');
      await flush();
      const stream = fixture.streams[0];
      assert.ok(stream);
      fixture.page.cancelDeep();
      await flush();
      assert.equal(stream.closed, !fixed);
      const cancelled = lang === 'ar' ? 'أُلغي البحث' : 'Research cancelled';
      assert.equal(fixture.toasts.includes(cancelled), !fixed);
      if (fixed) {
        assert.equal(fixture.elements.get('research-live-status').textContent, lang === 'ar' ? 'جارٍ الإلغاء' : 'cancelling');
        assert.ok(fixture.toasts.some(msg => msg.includes(lang === 'ar' ? 'بانتظار' : 'waiting')));
        // Delivery can race across writer threads even though SQLite commits are serialized.
        stream.emit('progress', { type: 'progress', session_id: 'held', status: 'running', updated_at: 15 });
        assert.equal(fixture.elements.get('research-live-status').textContent, lang === 'ar' ? 'جارٍ الإلغاء' : 'cancelling');
        stream.emit('done', { type: 'done', session: { id: 'held', status: 'cancelled', updated_at: 30 } });
        assert.equal(stream.closed, true);
        assert.ok(fixture.toasts.includes(cancelled));
      }
    });
  }
  test(`${lang}: reopened cancelling session attaches to progress`, async () => {
    const fixture = setup(lang, source, 'cancelling');
    fixture.page.viewDetail('session:held');
    await flush();
    assert.equal(fixture.streams.length, 1);
    assert.equal(fixture.streams[0].closed, false);
    assert.equal(fixture.toasts.length, 0);
  });
}

for (const lang of ['en', 'ar']) {
  test(`${lang}: interrupted inspection requires explicit resume`, async () => {
    const fixture = setup(lang, source, 'interrupted');
    fixture.page.viewDetail('session:held');
    await flush();
    assert.equal(fixture.streams.length, 0);
    assert.equal(fixture.requests.some(url => url.endsWith('/resume')), false);
    assert.ok(fixture.elements.get('research-live-actions').innerHTML.includes('data-act="resume"'));
    fixture.page.resumeSession('session:held');
    await flush();
    assert.equal(fixture.requests.filter(url => url.endsWith('/resume')).length, 1);
    assert.equal(fixture.streams.length, 1);
    assert.equal(fixture.streams[0].closed, false);
    fixture.streams[0].emit('done', {type: 'done', session: {id: 'held', status: 'partial', updated_at: 40}});
    assert.equal(fixture.streams[0].closed, true);
    assert.equal(fixture.toasts.includes('Research complete'), false);
  });
}
