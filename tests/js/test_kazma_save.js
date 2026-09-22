/**
 * kazmaSave — a write the server refused is never reported as saved.
 *
 * Regression for the 2026-09-22 audit: 24 save/delete handlers across the
 * settings, skills, memory and dashboard pages did `await fetch(...)`, threw
 * the Response away, and showed a success toast. A 403, 422 or 500 still said
 * "saved" — Settings → Safety (the HITL policy) among them. kazmaSave resolves
 * only when the server accepted the write and rejects with the server's own
 * reason otherwise; tests/test_static_gates.py forbids discarding an awaited
 * fetch() result anywhere in the UI.
 *
 * Run: node tests/js/test_kazma_save.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "auth-guard.js",
);

let nextResponse = null;
const sent = [];

function respond(status, body) {
  nextResponse = {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (typeof body === "string") throw new SyntaxError("not JSON");
      return body;
    },
  };
}

global.window = global;
global.location = { pathname: "/settings", search: "", href: "" };
global.fetch = async (url, init) => {
  sent.push({ url, init });
  return nextResponse;
};
vm.runInThisContext(fs.readFileSync(SRC, "utf8"), { filename: SRC });

async function rejects(promise) {
  try {
    await promise;
  } catch (err) {
    return err;
  }
  throw new assert.AssertionError({ message: "expected kazmaSave to reject" });
}

async function main() {
  assert.strictEqual(typeof window.kazmaSave, "function", "auth-guard.js must define kazmaSave");

  // Accepted write: resolves with the body.
  respond(200, { status: "ok", saved: 1 });
  assert.deepStrictEqual(await window.kazmaSave("/api/x", { method: "PUT" }), { status: "ok", saved: 1 });

  // Server error: rejects with the server's reason and status.
  respond(500, { detail: "database is locked" });
  let err = await rejects(window.kazmaSave("/api/settings/agent/safety", { method: "PUT" }));
  assert.strictEqual(err.message, "database is locked");
  assert.strictEqual(err.status, 500);

  // 200 carrying the codebase's error envelope is a failure too.
  respond(200, { status: "error", error: "protected key" });
  err = await rejects(window.kazmaSave("/api/settings/single", { method: "PUT" }));
  assert.strictEqual(err.message, "protected key");

  // FastAPI validation errors carry a list in `detail`.
  respond(422, { detail: [{ loc: ["body", "value"], msg: "field required" }] });
  err = await rejects(window.kazmaSave("/api/settings", { method: "PUT" }));
  assert.match(err.message, /field required/);

  // A forbidden response with no JSON body still rejects, with the status.
  respond(403, "<html>Forbidden</html>");
  err = await rejects(window.kazmaSave("/api/skills/toggle", { method: "POST" }));
  assert.strictEqual(err.message, "HTTP 403");

  // An object body is JSON-encoded; a string body is sent as given. The
  // X-Requested-With header rides along (some same-origin guards need it).
  sent.length = 0;
  respond(200, {});
  await window.kazmaSave("/api/a", { method: "POST", body: { enabled: true } });
  await window.kazmaSave("/api/b", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: false }),
  });
  assert.strictEqual(sent[0].init.body, '{"enabled":true}');
  assert.strictEqual(sent[0].init.headers["Content-Type"], "application/json");
  assert.strictEqual(sent[0].init.headers["X-Requested-With"], "XMLHttpRequest");
  assert.strictEqual(sent[0].init.credentials, "same-origin");
  assert.strictEqual(sent[1].init.body, '{"enabled":false}');

  // A 204 with no body is still a success.
  respond(204, "");
  assert.strictEqual(await window.kazmaSave("/api/c", { method: "DELETE" }), null);

  // Negative control: the pattern this replaced does NOT notice a 500.
  respond(500, { detail: "database is locked" });
  await fetch("/api/settings/agent/safety", { method: "PUT" }); // no throw, no signal

  console.log("test_kazma_save: all checks passed");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
