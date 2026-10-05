/* Save failures retain annotations and surface actionable server/browser errors. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {
  window: { t: key => key === 'x_dataset.invalid_response' ? 'Request failed (HTTP {status})' : key },
  document: { querySelector: () => ({ textContent: 'Follower Count' }) },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('kazma-ui/kazma_ui/static/js/x_datasets.js', 'utf8'), context);

async function main() {
  const editor = context.xDatasetPage();
  let focused = 0;
  editor.$nextTick = fn => fn();
  editor.$refs = { saveError: { scrollIntoView() {}, focus() { focused++; } } };
  editor.dataset = { id: 'practice', revision: 3 };
  editor.form = { case: { id: 'real-case', language: 'en', rationale: '', categories: ['sarcasm'], summon: {} },
    targetLabeled: false, target: '', auto: '', evidence: '', safety: '', followers: '' };
  editor._saved = 'before editing';
  const original = JSON.stringify(editor.form);
  context.fetch = async () => ({ ok: false, status: 422,
    json: async () => ({ detail: [{ loc: ['body', 'case'], msg: 'Invalid annotation' }] }) });
  await editor.save();
  assert.match(editor.error, /body.case: Invalid annotation/);
  assert.equal(JSON.stringify(editor.form), original);
  assert.equal(editor.dirty, true);

  editor.reviewed = true;
  context.window.t = key => key === 'x_dataset.review_incomplete' ? 'Missing: {fields}; uncheck review'
    : key === 'x_dataset.invalid_response' ? 'Request failed (HTTP {status})' : key;
  await editor.save();
  assert.match(editor.error, /expected_auto/);
  assert.match(editor.error, /expected_evidence/);
  assert.match(editor.error, /expected_safety/);
  assert.match(editor.error, /rationale/);
  assert.equal(editor.reviewed, true);
  editor.reviewed = false;
  assert.equal(editor.busy, false);
  assert.equal(focused, 2);

  context.fetch = async () => ({ ok: false, status: 409,
    json: async () => ({ error: 'Collection changed; reload before saving' }) });
  await editor.save();
  assert.match(editor.error, /Collection changed/);
  assert.equal(JSON.stringify(editor.form), original);

  for (const json of [async () => { throw Error('HTML response'); }, async () => null]) {
    context.fetch = async () => ({ ok: false, status: 503, json });
    await editor.save();
    assert.match(editor.error, /HTTP 503/);
    assert.equal(JSON.stringify(editor.form), original);
  }
  editor.invalidField({ target: { id: 'xd-followers', validationMessage: 'Must be at least zero' } });
  assert.match(editor.error, /Must be at least zero/);
  assert.equal(editor.dirty, true);

  let toast;
  context.window.showToast = (message, type) => { toast = { message, type }; };
  context.fetch = async url => ({ ok: true, status: 200,
    json: async () => ({ ok: true, dataset: url.endsWith('/case') ? { id: 'practice', revision: 4 } : [] }) });
  await editor.save();
  assert.equal(editor.form, null);
  assert.equal(editor.dirty, false);
  assert.equal(editor.error, '');
  assert.equal(toast.type, 'success');
  assert.equal(editor.dataset.revision, 4);
  console.log('Dataset save feedback checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
