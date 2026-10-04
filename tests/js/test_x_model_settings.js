/* Unsaved X model choices reach preview/save without global-model changes. */
"use strict";
const assert = require("assert");
const path = require("path");
global.window = globalThis;
global.showToast = () => {};
require(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/settings_integrations.js"));

(async () => {
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body), method: options.method });
    return { ok: true, json: async () => ({ ok: true, ai: model }) };
  };
  const model = { selection: "specific", provider: "local", model: "installed-model" };
  const page = Object.assign({
    xReply: { ai: model, subjects: [], ai_options: [{ provider: "local", models: ["installed-model"] }] },
    xReplySummonersText: "", xReplyProblems: [], xReplyOpen: null,
    xReplyPreview: { text: "A post to reply to", handle: "", subject_id: "", mood: "" },
  }, global.KazmaSettingsMixins.integrations());
  assert.deepStrictEqual(page.xReplyAIModels(), ["installed-model"]);
  await page.runXReplyPreview();
  assert.strictEqual(requests[0].url, "/api/x/reply/preview");
  assert.deepStrictEqual(requests[0].body.ai, model);
  await page.saveXReply();
  assert.deepStrictEqual(requests[1].body.ai, model);
  assert.strictEqual(requests[1].url, "/api/x/reply");
  assert.strictEqual(requests[1].method, "PUT");
  assert.strictEqual(requests.length, 2, "No global model activation request");
  console.log("X model preview/save use the explicit binding without global activation.");
})().catch(error => { console.error(error); process.exitCode = 1; });
