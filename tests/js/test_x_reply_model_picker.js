/* X's model is chosen from a list the page shows (2026-11-19).
 *
 * The provider's models were a <datalist>: nothing on screen until the box
 * was clicked, and then only the names matching what the box held. They are
 * a <select> now, with "Another model" for a name the provider does not
 * list. This drives the picker's own methods: a listed model, "Another
 * model", a saved model the list lacks (typed), a model named "other" (the
 * "m:" prefix keeps it apart), and a provider change.
 */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
global.window = globalThis;
global.showToast = () => {};
require(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/settings_integrations.js"));

function page(model) {
  return Object.assign({
    xReply: {
      ai: { selection: "specific", provider: "local", model: model || "" },
      subjects: [],
      ai_options: [{ provider: "local", models: ["first", "second", "other"] }],
    },
    xReplyAIModelTyped: false,
  }, global.KazmaSettingsMixins.integrations());
}

// A listed model is picked from the list, never typed.
const p = page("");
assert.strictEqual(p.xReplyAIModelTyping(), false);
p.xReplyPickModel("m:second");
assert.strictEqual(p.xReply.ai.model, "second");
assert.strictEqual(p.xReplyAIModelTyping(), false);

// A model named "other" is a model, not "Another model".
p.xReplyPickModel("m:other");
assert.strictEqual(p.xReply.ai.model, "other");
assert.strictEqual(p.xReplyAIModelTyping(), false);

// "Another model": the box empties and the typed box shows.
p.xReplyPickModel("other");
assert.strictEqual(p.xReply.ai.model, "");
assert.strictEqual(p.xReplyAIModelTyping(), true);
p.xReply.ai.model = "my-own-model";
assert.strictEqual(p.xReplyAIModelTyping(), true);
assert.deepStrictEqual(p.xReplyAIConfigPayload(), { selection: "specific", provider: "local", model: "my-own-model" });

// Back to a listed model.
p.xReplyPickModel("m:first");
assert.strictEqual(p.xReplyAIModelTyping(), false);
assert.strictEqual(p.xReply.ai.model, "first");

// A saved model the provider does not list opens in the typed box, kept.
const saved = page("custom-finetune");
assert.strictEqual(saved.xReplyAIModelTyping(), true);
assert.strictEqual(saved.xReply.ai.model, "custom-finetune");

// The page: a select of the models with "Another model", and no datalist.
const html = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/templates/settings.html"), "utf8");
const start = html.indexOf('for="xr-ai-model"');
const part = html.slice(start, html.indexOf("</div>", start));
assert.ok(/<select id="xr-ai-model"/.test(part), "the models are a select");
assert.ok(part.includes('value="other"') && part.includes("xReplyPickModel("), "with Another model");
assert.ok(!part.includes("<datalist"), "never a datalist");
// The provider change starts the choice again.
assert.ok(html.includes('@change="xReply.ai.model = \'\'; xReplyAIModelTyped = false"'));

// Negative control: the old markup is a datalist this test refuses.
const old = '<input id="xr-ai-model" list="xr-ai-models"><datalist id="xr-ai-models"></datalist>';
assert.ok(!/<select id="xr-ai-model"/.test(old) && old.includes("<datalist"));

console.log("X's model is picked from a visible list, with Another model for a typed name.");
