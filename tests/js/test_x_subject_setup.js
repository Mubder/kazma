/* Basic authoring keeps stored advanced policy data and stable identities. */
"use strict";
const assert = require("assert");
const path = require("path");
global.window = globalThis;
require(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/settings_integrations.js"));
const page = Object.assign({ xReply: { subjects: [], reply_style: {
    language: "source", dialect: "", slang: "none", length: "standard",
    allow_uncensored_language: false, profanity: "none",
} } }, global.KazmaSettingsMixins.integrations());
page.xReplyAddSubject();
const subject = page.xReply.subjects[0];
const identity = subject.id;
page.xReplySetTarget(subject, "Coffee suppliers");
assert.deepStrictEqual(subject.match, ["Coffee suppliers"]);
page.xReplySetTarget(subject, "Tea suppliers");
assert.strictEqual(subject.id, identity);
assert.deepStrictEqual(subject.match, ["Tea suppliers"]);
subject._routingEdited = true;
subject._matchText = "tea, شاي";
page.xReplySetTarget(subject, "Commercial tea suppliers");
assert.strictEqual(subject._matchText, "tea, شاي");
Object.assign(subject, { aliases: ["espresso"], exclusions: ["decaf"], scope: "Sourcing",
    exceptions: ["Private life"], allow_auto: false });
const saved = page.xReplySubjectPayload()[0];
assert.deepStrictEqual(saved.aliases, ["espresso"]);
assert.deepStrictEqual(saved.exceptions, ["Private life"]);
assert.strictEqual(saved.scope, "Sourcing");
assert.strictEqual(saved.allow_auto, false);
assert(!Object.keys(saved).some(key => key.startsWith("_")));
page.xReplyOverrideStyle(subject, true);
subject.reply_style.allow_uncensored_language = true;
assert.strictEqual(page.xReply.reply_style.allow_uncensored_language, false);
page.xReplyOverrideStyle(subject, false);
assert.deepStrictEqual(subject.reply_style, {});
page.xReplyPreview = { text: '', result: { ok: true } };
page.xReplyExample('quoted');
assert(page.xReplyPreview.text.includes(subject.target));
assert.strictEqual(page.xReplyPreview.result, null);
const example = page.xReplyPreview.text;
page.xReplyExample('unknown');
assert.strictEqual(page.xReplyPreview.text, example);
console.log("Basic X subject setup preserves identity, routing and advanced policy.");
