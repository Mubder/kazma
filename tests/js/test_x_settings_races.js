/* A delayed save/test must not overwrite newer edits or show stale compatibility. */
'use strict';
const assert = require('assert');
const path = require('path');
global.window = globalThis;
global.showToast = () => {};
require(path.join(__dirname, '../../kazma-ui/kazma_ui/static/js/settings_integrations.js'));
(async () => {
  const page = Object.assign({ xReply: { subjects: [], trigger: 'first', reply_style: { slang: 'none' },
    ai: { selection: 'global' } }, xReplySummonersText: '', xReplyOpen: null },
    global.KazmaSettingsMixins.integrations());
  page.xReplySavedSignature = page.xReplySettingsSnapshot();
  let resolve;
  global.fetch = () => new Promise(done => { resolve = done; });
  const saving = page.saveXReply();
  page.xReply.trigger = 'newer edit';
  resolve({ ok: true, json: async () => ({ ok: true, settings_revision: 1, trigger: 'first', subjects: [], summoners: [] }) });
  await saving;
  assert.strictEqual(page.xReply.trigger, 'newer edit');
  assert.strictEqual(page.xReply.settings_revision, 1);
  assert(page.xReplyHasChanges());
  const testing = page.testXReplyModel();
  page.xReply.reply_style.slang = 'natural';
  resolve({ ok: true, json: async () => ({ ok: true, draft: 'stale draft' }) });
  await testing;
  assert.strictEqual(page.xReplyModelResult.ok, false);
  assert(!page.xReplyModelResult.draft);
  console.log('Delayed X settings saves preserve edits and model tests discard stale results.');
})().catch(error => { console.error(error); process.exitCode = 1; });
