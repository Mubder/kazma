/**
 * The page's leave hooks for harnesses that run page scripts without the page.
 *
 * base.html defines window.kazmaAddLeaveHook (the one way a page script asks
 * to be told when soft navigation leaves the page) and modules/nav.js runs
 * the hooks on the way out (teardownLiveSockets). A harness that evaluates a
 * page script alone has neither; this installs base.html's own helper, read
 * from the template, so the tests exercise the code the browser runs.
 *
 * Usage:
 *   const { installLeaveHooks, leavePage } = require("./_leave_hooks.js");
 *   installLeaveHooks(window);   // before running the page script
 *   leavePage(window);           // what a sidebar link does
 */
"use strict";
const fs = require("fs");
const path = require("path");

const BASE = path.join(__dirname, "../../kazma-ui/kazma_ui/templates/base.html");

function helperSource() {
  const html = fs.readFileSync(BASE, "utf8").replace(/\r\n/g, "\n");
  const m = html.match(/window\.kazmaAddLeaveHook = function \(fn\) \{[\s\S]*?\n\s*\};/);
  if (!m) throw new Error("base.html defines no window.kazmaAddLeaveHook");
  return m[0];
}

function installLeaveHooks(win) {
  // eslint-disable-next-line no-new-func
  new Function("window", helperSource())(win);
  return win;
}

/** nav.js teardownLiveSockets: run every hook, in either shape, then clear. */
function leavePage(win) {
  const hooks = win.kazmaOnSoftNavLeave;
  if (typeof hooks === "function") hooks();
  else if (Array.isArray(hooks)) hooks.forEach((fn) => { if (typeof fn === "function") fn(); });
  win.kazmaOnSoftNavLeave = null;
}

module.exports = { helperSource, installLeaveHooks, leavePage };
