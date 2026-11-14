/**
 * kazmaRandomId — ids from the browser's cryptographic random source, also on
 * a page over plain http, where crypto.randomUUID does not exist.
 *
 * The chat's new-chat id, the IDE's request key and X Studio's booking key fell
 * back to Math.random on such a page (CodeQL js/insecure-randomness,
 * 2026-11-14). A script that makes an id from Math.random is declared below
 * with why it may.
 *
 * Run: node tests/js/test_random_ids.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const nodeCrypto = require("crypto");

const JS = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const GUARD = fs.readFileSync(path.join(JS, "auth-guard.js"), "utf8");
const UUID4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function pageWith(crypto) {
  const page = { crypto, location: { pathname: "/chat", search: "", href: "" }, fetch: async () => null };
  page.window = page;
  vm.runInNewContext(GUARD, page);
  return page;
}

// A page over plain http: getRandomValues only.
let drawn = 0;
const insecure = pageWith({
  getRandomValues: (a) => { drawn += 1; return nodeCrypto.getRandomValues(a); },
});
const ids = new Set();
for (let i = 0; i < 200; i += 1) ids.add(insecure.kazmaRandomId());
assert.strictEqual(ids.size, 200);
for (const id of ids) assert.match(id, UUID4);
assert.strictEqual(drawn, 200);

// A secure page uses the browser's own UUID.
const secure = pageWith({ randomUUID: () => "11111111-2222-4333-8444-555555555555", getRandomValues: () => { throw new Error("unused"); } });
assert.strictEqual(secure.kazmaRandomId(), "11111111-2222-4333-8444-555555555555");

// No script makes an id from Math.random, but these, which name nothing anyone
// could use: a history entry's key kept in this tab, and the chat's own-send
// marks (only this tab's socket drops its own echo by them).
const DECLARED = new Set(["modules/nav.js", "chat.js:_newClientMsgId"]);
const MATH_ID = /Math\.random\(\)\.toString\(36\)/;
const found = [];
(function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) { walk(full); continue; }
    if (!entry.name.endsWith(".js") || entry.name.endsWith(".min.js")) continue;
    const rel = path.relative(JS, full).split(path.sep).join("/");
    const lines = fs.readFileSync(full, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (!MATH_ID.test(line)) return;
      const fn = lines.slice(0, i + 1).reverse().find((l) => /function\s+\w+\s*\(/.test(l)) || "";
      const name = (fn.match(/function\s+(\w+)/) || [])[1];
      if (DECLARED.has(rel) || DECLARED.has(`${rel}:${name}`)) return;
      found.push(`${rel}:${i + 1}`);
    });
  }
})(JS);
assert.deepStrictEqual(found, [], `ids made from Math.random: ${found.join(", ")}`);

// Control: the chat's old fallback is found.
assert.ok(MATH_ID.test("return 's-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);"));

console.log("ok - random ids come from crypto, on secure and plain-http pages");
