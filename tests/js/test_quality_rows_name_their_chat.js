"use strict";
/**
 * Every answer-quality row links to its chat or says the chat was deleted
 * (resilience plan I). Run: node tests/js/test_quality_rows_name_their_chat.js
 *
 * Live, 5 of 13 rows had no link and no word why. Negative control: a row
 * with a chat still links to it.
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/dashboard.js"), "utf8");
const start = source.indexOf("  var _qualityShown = null;");
const end = source.indexOf("  function fetchStatusFallback()", start);
assert(start >= 0 && end > start);
function element(tag) {
  return { tag, children: [], textContent: "", style: {}, className: "",
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; } };
}
const nodes = Object.fromEntries(["answer-quality-status", "answer-quality-counts", "answer-quality-recent"]
  .map(id => [id, element("div")]));
const response = { ok: true, json: async () => ({ available: true, totals: { turns: 2 }, recent: [
  { ts: "2026-11-01T12:00:00Z", session_id: "s-1", chat_deleted: false, signals: ["turn_failed"] },
  { ts: "2026-11-01T13:00:00Z", session_id: "", chat_deleted: true, signals: ["empty_answer"] },
] }) };
const context = vm.createContext({
  $: id => nodes[id], window: { tOr: (_, fallback) => fallback },
  document: { createElement: element, createTextNode: text => ({ textContent: text }) },
  fetch: async () => response, Date, String, Error, encodeURIComponent,
});
vm.runInContext(source.slice(start, end), context);
(async () => {
  vm.runInContext("fetchAnswerQuality()", context);
  await new Promise(resolve => setImmediate(resolve));
  const rows = nodes["answer-quality-recent"].children;
  const last = row => row.children[row.children.length - 1];
  assert.strictEqual(last(rows[0]).href, "/chat?s=s-1", "control: a row with its chat links to it");
  assert.strictEqual(last(rows[1]).textContent, "Chat deleted", "a row without one says so");
  console.log("Answer-quality rows: each links to its chat or says it was deleted.");
})().catch(error => { console.error(error); process.exitCode = 1; });
