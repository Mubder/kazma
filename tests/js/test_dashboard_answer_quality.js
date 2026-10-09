"use strict";
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/dashboard.js'), 'utf8');
const start = source.indexOf('  function fetchAnswerQuality()');
const end = source.indexOf('  function fetchStatusFallback()', start);
assert(start >= 0 && end > start);
function element(tag) {
  return {tag, children: [], textContent: '', style: {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    set innerHTML(_) { throw new Error('Untrusted telemetry must not enter innerHTML'); }
  };
}
const nodes = Object.fromEntries(['answer-quality-status', 'answer-quality-counts', 'answer-quality-recent'].map(id => [id, element('div')]));
let response = {ok: true, json: async () => ({available: true, totals: {turns: 2, paragraph_miss: 1}, recent: [
  {ts:'2026-10-09T12:00:00Z', session_id:'<script>alert(1)</script>&s=other', signals:['paragraph_miss']}
]})};
const context = vm.createContext({
  $: id => nodes[id], window: {tOr: (_, fallback) => fallback},
  document: {createElement: element, createTextNode: text => ({textContent:text})},
  fetch: async url => { assert.strictEqual(url, '/api/dashboard/answer-quality'); return response; },
  Date, String, Error, encodeURIComponent
});
vm.runInContext(source.slice(start, end), context);
async function refresh() {
  vm.runInContext('fetchAnswerQuality()', context);
  await new Promise(resolve => setImmediate(resolve));
}
(async () => {
  await refresh();
  assert(nodes['answer-quality-status'].textContent.includes('2'));
  const link = nodes['answer-quality-recent'].children[0].children[3];
  assert.strictEqual(link.href, '/chat?s=%3Cscript%3Ealert(1)%3C%2Fscript%3E%26s%3Dother');
  assert.strictEqual(nodes['answer-quality-counts'].children.length, 7);
  response = {ok: true, json: async () => ({available:false, totals:null, recent:[]})};
  await refresh();
  assert(nodes['answer-quality-status'].textContent.includes('unavailable'));
  assert.strictEqual(nodes['answer-quality-counts'].children.length, 0);
  assert.strictEqual(nodes['answer-quality-recent'].children.length, 0);
  response = {ok:false};
  await refresh();
  assert(nodes['answer-quality-status'].textContent.includes('administrator'));
  console.log('Dashboard quality: safe links, counters and unavailable state passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
