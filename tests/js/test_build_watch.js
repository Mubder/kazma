/**
 * An open tab is told when Kazma was updated -- once, and only then.
 *
 * modules/build_watch.js compares the build that rendered the page
 * (<meta name="kazma-build">) with the one /health/live reports, and offers a
 * Refresh toast when they differ (the owner's request, 2026-10-16: a fix to
 * the chat page reached the server but not tabs opened before the deploy).
 * Runs the REAL module source with `export` stripped, against stubs.
 *
 * Run: node tests/js/test_build_watch.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "build_watch.js"),
  "utf8").replace(/\r\n/g, "\n").replace(/^export /gm, "");

let fail = 0;
function check(name, cond) {
  if (cond) { console.log("ok   " + name); } else { fail++; console.log("FAIL " + name); }
}

/** A fresh world: a page rendered by *pageBuild*, a server reporting *live*. */
function world(pageBuild, live, { visible = true } = {}) {
  const toasts = [];
  const document = {
    visibilityState: visible ? "visible" : "hidden",
    querySelector: (sel) => (sel === 'meta[name="kazma-build"]' && pageBuild !== null
      ? { getAttribute: () => pageBuild } : null),
    addEventListener: () => {},
  };
  const window = {
    kazmaGetJson: async (url) => (url === "/health/live" ? live : null),
    kazmaT: (key) => key,
    Alpine: { store: (n) => (n === "toast" ? { add: (...a) => { toasts.push(a); return 1; } } : null) },
    location: { reload: () => { window.reloaded = true; } },
  };
  // eslint-disable-next-line no-new-func
  const mod = new Function("window", "document", "setInterval",
    src + "\nreturn { buildNoticeDue, startBuildWatch };")(window, document, () => 0);
  return { mod, window, toasts };
}

(async () => {
  // The decision.
  const { mod } = world("aaa", null);
  check("another build is due", mod.buildNoticeDue("aaa", "bbb") === true);
  check("the same build is not", mod.buildNoticeDue("aaa", "aaa") === false);
  check("an unknown page build never notifies", mod.buildNoticeDue("unknown", "bbb") === false);
  check("an unknown server build never notifies", mod.buildNoticeDue("aaa", "unknown") === false);
  check("no server answer never notifies", mod.buildNoticeDue("aaa", "") === false);

  // A deploy: one toast, persistent, with a Refresh that reloads.
  {
    const w = world("aaa", { status: "alive", build: { commit: "bbb" } });
    const watch = w.mod.startBuildWatch();
    check("a deploy notifies", (await watch.check()) === true);
    check("one toast", w.toasts.length === 1);
    const [message, type, duration, action] = w.toasts[0];
    check("the message is the catalog's", message === "common.update_available");
    check("an info toast", type === "info");
    check("it stays until answered", duration === 0);
    check("its button is the catalog's Refresh", action && action.label === "common.refresh");
    action.run();
    check("Refresh reloads the page", w.window.reloaded === true);
    check("only once per tab", (await watch.check()) === false && w.toasts.length === 1);
    check("the watcher is on window", w.window.KazmaBuildWatch === watch);
  }

  // Nothing to say.
  {
    const w = world("aaa", { status: "alive", build: { commit: "aaa" } });
    check("a restart of the same build shows nothing",
      (await w.mod.startBuildWatch().check()) === false && w.toasts.length === 0);
  }
  {
    const w = world("aaa", null);  // kazmaGetJson: a 502 page mid-restart, or offline
    check("a server not answering shows nothing",
      (await w.mod.startBuildWatch().check()) === false && w.toasts.length === 0);
  }
  {
    const w = world("aaa", { build: { commit: "bbb" } }, { visible: false });
    check("a hidden tab waits until it is seen",
      (await w.mod.startBuildWatch().check()) === false && w.toasts.length === 0);
  }
  {
    const w = world(null, { build: { commit: "bbb" } });
    check("a page without the build tag shows nothing",
      (await w.mod.startBuildWatch().check()) === false && w.toasts.length === 0);
  }

  if (fail) { console.log(fail + " failed"); process.exit(1); }
  console.log("all passed");
})();
