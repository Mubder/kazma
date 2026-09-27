/**
 * kazmaGetJson — a poller's read never throws and never parses an error page.
 *
 * While the server restarts, Cloudflare answers every request with a 502 HTML
 * page. The Memory and Swarm pages' pollers parsed it as JSON and logged
 * "Unexpected token '<'" into every console (2026-09-27). kazmaGetJson answers
 * the parsed body of a 2xx JSON response and null for everything else.
 *
 * Run: node tests/js/test_kazma_get_json.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "auth-guard.js");

let next = null;
function respond(status, contentType, body) {
  next = {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name.toLowerCase() === "content-type" ? contentType : null) },
    json: async () => {
      if (typeof body === "string") throw new SyntaxError("Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON");
      return body;
    },
  };
}

const logged = [];
global.window = global;
global.location = { pathname: "/memory", search: "", href: "" };
global.console = Object.assign({}, console, { error: (...a) => logged.push(a.join(" ")), warn: (...a) => logged.push(a.join(" ")) });
global.fetch = async () => {
  if (next === "offline") throw new TypeError("Failed to fetch");
  return next;
};
vm.runInThisContext(fs.readFileSync(SRC, "utf8"), { filename: SRC });

async function main() {
  assert.strictEqual(typeof window.kazmaGetJson, "function", "auth-guard.js must define kazmaGetJson");

  respond(200, "application/json", { status: "ACTIVE" });
  assert.deepStrictEqual(await window.kazmaGetJson("/api/system/status"), { status: "ACTIVE" });

  // What Cloudflare serves during a restart.
  respond(502, "text/html", "<!DOCTYPE html><html>Bad gateway</html>");
  assert.strictEqual(await window.kazmaGetJson("/api/system/status"), null);

  // A 200 that is an HTML page (a login redirect, a proxy page) is not data.
  respond(200, "text/html; charset=utf-8", "<!DOCTYPE html>");
  assert.strictEqual(await window.kazmaGetJson("/api/swarm/status"), null);

  // No network at all.
  next = "offline";
  assert.strictEqual(await window.kazmaGetJson("/api/swarm/status"), null);

  // Negative control: the pattern it replaces throws on the same page.
  respond(502, "text/html", "<!DOCTYPE html><html>Bad gateway</html>");
  let threw = null;
  try { await (await fetch("/api/system/status")).json(); } catch (e) { threw = e; }
  assert.ok(threw && /not valid JSON/.test(threw.message), "the old read did not throw on an error page");

  assert.deepStrictEqual(logged, [], "kazmaGetJson must not log");
  console.log("ok — kazmaGetJson answers null for error pages, HTML and no network, and logs nothing");
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + "\n");
  process.exit(1);
});
