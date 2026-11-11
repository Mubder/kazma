/**
 * kazmaStartSignIn — an OAuth sign-in starts with a POST and runs in this tab.
 *
 * Security audit 2026-11-10: the sign-in starts were GETs that wrote state
 * (any link could start one), and the GitHub one ran in a popup that answered
 * its opener. A start is now a POST from Kazma's own page (with the
 * X-Requested-With header the mail and calendar routes require); its answer
 * is the provider's URL, followed in the SAME tab -- only an http(s) address.
 * A start that did not go is handed back for the page to word.
 *
 * Run: node tests/js/test_kazma_start_sign_in.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "auth-guard.js");

let next = null;
const sent = [];
function respond(status, body) {
  next = { ok: status >= 200 && status < 300, status, json: async () => body };
}

const went = [];
const opened = [];
global.window = global;
global.location = {
  pathname: "/workspace", search: "", href: "https://kazma.example/workspace",
  assign: (url) => went.push(url),
};
global.open = (...args) => { opened.push(args); return null; };
global.kazmaT = (key, english, vars) => {
  let text = english;
  for (const [k, v] of Object.entries(vars || {})) text = text.replace("{" + k + "}", v);
  return "[" + key + "] " + text;
};
global.fetch = async (url, init) => { sent.push({ url, init }); return next; };
vm.runInThisContext(fs.readFileSync(SRC, "utf8"), { filename: SRC });

async function main() {
  assert.strictEqual(typeof window.kazmaStartSignIn, "function");

  // A start that goes: a POST, then the provider's page in this tab.
  respond(200, { ok: true, authorize_url: "https://github.com/login/oauth/authorize?state=s1" });
  const done = await window.kazmaStartSignIn("/api/github/oauth/start", { return_to: "/workspace" });
  assert.deepStrictEqual(done, { ok: true, navigating: true });
  const call = sent.pop();
  assert.strictEqual(call.url, "/api/github/oauth/start");
  assert.strictEqual(call.init.method, "POST");
  assert.strictEqual(call.init.credentials, "same-origin");
  assert.strictEqual(call.init.headers["X-Requested-With"], "XMLHttpRequest");
  assert.deepStrictEqual(JSON.parse(call.init.body), { return_to: "/workspace" });
  assert.deepStrictEqual(went, ["https://github.com/login/oauth/authorize?state=s1"]);
  assert.deepStrictEqual(opened, [], "no popup");

  // An address that is not http(s) is never followed.
  went.length = 0;
  respond(200, { ok: true, authorize_url: "javascript:alert(1)" });
  const bad = await window.kazmaStartSignIn("/api/email/oauth/gmail/start", {});
  assert.strictEqual(bad.ok, false);
  assert.strictEqual(bad.code, "bad_authorize_url");
  assert.deepStrictEqual(went, []);

  // A start that did not go comes back for the page to word.
  respond(409, { ok: false, code: "sign_in_from_callback_origin", origin: "https://kazma.example" });
  const elsewhere = await window.kazmaStartSignIn("/api/email/oauth/gmail/start", { account: "work" });
  assert.strictEqual(elsewhere.ok, false);
  assert.strictEqual(elsewhere.status, 409);
  assert.deepStrictEqual(went, []);
  assert.strictEqual(
    window.kazmaSignInProblem(elsewhere, "fallback"),
    "[oauth.sign_in_from] Open Kazma at https://kazma.example to sign in: the provider sends you back there.",
  );
  respond(400, { ok: false, error: "Google OAuth Client ID is not set." });
  const refused = await window.kazmaStartSignIn("/api/email/oauth/gmail/start", {});
  assert.strictEqual(window.kazmaSignInProblem(refused, "fallback"), "Google OAuth Client ID is not set.");
  respond(500, null);
  const broken = await window.kazmaStartSignIn("/api/email/oauth/gmail/start", {});
  assert.strictEqual(window.kazmaSignInProblem(broken, "fallback"), "fallback");

  // The callback's refusals, by code; anything else keeps the fallback.
  for (const code of ["other_browser", "unknown", "expired", "other_provider", "denied", "no_code",
                      "exchange_failed"]) {
    assert.ok(window.kazmaSignInFailed(code, "x").startsWith("[oauth.failed." + code + "] "), code);
  }
  assert.strictEqual(window.kazmaSignInFailed("toString", "fallback"), "fallback");
  assert.strictEqual(window.kazmaSignInFailed(null, "fallback"), "fallback");

  // Negative control: the start as it was (a GET link opened in a popup)
  // sends no POST and follows nothing this helper would.
  sent.length = 0;
  global.open("/api/github/oauth/start", "_blank");
  assert.strictEqual(sent.length, 0);
  assert.strictEqual(opened.length, 1, "the old start was a popup");

  console.log("test_kazma_start_sign_in: ok");
}

main().catch((err) => { console.error(err); process.exit(1); });
