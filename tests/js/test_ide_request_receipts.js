"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/ide.js"), "utf8");
const storage = new Map();
const keys = [];
let fault = true;
function page() {
  const context = vm.createContext({
    window: { crypto: webcrypto }, crypto: webcrypto, TextEncoder, Uint8Array,
    sessionStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
    fetch: async (url, options) => {
      keys.push(options.headers["Idempotency-Key"]);
      if (fault) { fault = false; throw new Error("response lost after execution"); }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  });
  vm.runInContext(source, context);
  return vm.runInContext("ideApp()", context);
}

(async () => {
  const first = page();
  await assert.rejects(() => first._post("/api/ide/write", { path: "a.txt", content: "once" }));
  assert.equal(storage.size, 1);
  assert.ok(!JSON.stringify([...storage]).includes("once"), "storage holds only digest and operation ID");
  const reloaded = page();
  await reloaded._post("/api/ide/write", { path: "a.txt", content: "once" });
  assert.equal(keys[0], keys[1], "retry after reload uses the admitted operation");
  assert.equal(storage.size, 0);
  await reloaded._post("/api/ide/write", { path: "a.txt", content: "once" });
  assert.notEqual(keys[1], keys[2], "new deliberate action gets a new operation");
  console.log("IDE operation identity survives response loss and reload");
})().catch(error => { console.error(error); process.exitCode = 1; });
