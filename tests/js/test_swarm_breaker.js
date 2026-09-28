/**
 * The Swarm page's circuit-breaker badge and its Reset button.
 * Run: node tests/js/test_swarm_breaker.js
 *
 * Found 2026-09-28: the reset route existed and nothing called it, so an
 * open breaker could only be waited out; and the badge's refresh wrote the
 * raw English state ("closed") over the translated label and its icon.
 * updateBreakerBadge and resetBreaker are taken from the real swarm.js and
 * run against a DOM shaped like the worker card. Negative control: the old
 * updateBreakerBadge fails the label and icon checks.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { makeDocument } = require("./_dom.js");

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "swarm.js"), "utf8",
).replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) { fail++; console.error("FAIL", name, detail === undefined ? "" : detail); }
  else console.log("ok  ", name);
}

/** The text of `function <name>(...) {...}` in *code*, braces matched. */
function fn(code, name) {
  const start = code.indexOf("  function " + name + "(");
  if (start < 0) throw new Error("missing function " + name);
  let i = code.indexOf("{", start) + 1;
  let depth = 1;
  while (depth) { depth += { "{": 1, "}": -1 }[code[i]] || 0; i++; }
  return code.slice(start, i);
}

const AR = {
  "swarm.cb_closed": "مغلق", "swarm.cb_open": "مفتوح", "swarm.cb_half_open": "نصف مفتوح",
};

function page(updateSrc) {
  const { document, ROOT } = makeDocument();
  const badge = document.createElement("span");
  badge.setAttribute("data-cb-worker", "coder");
  const icon = document.createElement("span");
  icon.className = "ki";
  badge.appendChild(icon);
  const label = document.createElement("span");
  label.className = "cb-label";
  label.textContent = AR["swarm.cb_closed"];
  badge.appendChild(label);
  const reset = document.createElement("button");
  reset.setAttribute("data-cb-reset", "coder");
  reset.hidden = true;
  ROOT.appendChild(badge);
  ROOT.appendChild(reset);
  const calls = [];
  const toasts = [];
  const sandbox = {
    document,
    window: { tOr: (k, fb) => (AR[k] !== undefined ? AR[k] : fb) },
    t: (k, vars) => k + (vars && vars.name ? ":" + vars.name : ""),
    showToast: (m) => toasts.push(m),
    showError: (m) => toasts.push("ERR " + m),
    fetch: (url, opts) => {
      calls.push([url, opts && opts.method]);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ status: "ok", circuit_breaker: { state: "closed", consecutive_failures: 0 } }),
      });
    },
  };
  vm.runInNewContext(updateSrc + "\n" + fn(src, "resetBreaker") + "\nthis.updateBreakerBadge = updateBreakerBadge; this.resetBreaker = resetBreaker;", sandbox);
  return { sandbox, badge, icon, label, reset, calls, toasts };
}

async function main() {
  const now = page(fn(src, "updateBreakerBadge"));
  now.sandbox.updateBreakerBadge({ name: "coder", circuit_breaker: { state: "open", consecutive_failures: 5 } });
  ok("an open breaker reads in the page's language", now.label.textContent === "مفتوح", now.label.textContent);
  ok("its icon stays", now.icon.parentNode === now.badge);
  ok("and Reset shows", now.reset.hidden === false);
  now.sandbox.updateBreakerBadge({ name: "coder", circuit_breaker: { state: "half-open" } });
  ok("half-open reads through its key", now.label.textContent === "نصف مفتوح", now.label.textContent);

  now.sandbox.resetBreaker("coder");
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  ok("Reset posts to the worker's reset route",
    now.calls.length === 1 && now.calls[0][0] === "/api/swarm/workers/coder/circuit-breaker/reset"
      && now.calls[0][1] === "POST", JSON.stringify(now.calls));
  ok("and the badge closes", now.label.textContent === "مغلق", now.label.textContent);
  ok("and Reset hides again", now.reset.hidden === true);
  ok("and says so", now.toasts.length === 1 && now.toasts[0].indexOf("swarm.toast_breaker_reset") === 0, now.toasts);

  // Negative control: the old refresh wrote the raw state over the badge.
  const OLD = `  function updateBreakerBadge(worker) {
    var cb = worker.circuit_breaker;
    var badge = document.querySelector('[data-cb-worker="' + (worker.name || '') + '"]');
    if (!badge) return;
    var closed = !cb || cb.state === 'closed';
    badge.className = 'badge cb-badge ' + (closed ? 'badge-success cb-badge-closed' : (cb.state === 'open' ? 'badge-danger' : 'badge-warning'));
    badge.textContent = ' ' + (closed ? 'closed' : cb.state);
  }`;
  const old = page(OLD);
  old.sandbox.updateBreakerBadge({ name: "coder", circuit_breaker: { state: "open" } });
  ok("control: the old refresh put English on the badge", old.badge.textContent.trim() === "open");
  ok("control: and dropped its icon", old.icon.parentNode === null);

  if (fail) { console.error(fail + " failed"); process.exit(1); }
  console.log("all ok");
}

main().catch((e) => { console.error(e); process.exit(1); });
