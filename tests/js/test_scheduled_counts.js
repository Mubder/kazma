/**
 * The Scheduled page's counts come from one place (resilience plan I).
 * Run: node tests/js/test_scheduled_counts.js
 *
 * The live page said "3 Completed" next to "History (4)": a cancelled job
 * was in the History tab and in no count. The stats now show failed and
 * ended (completed and cancelled), which add up to the History tab's count,
 * and each tab's count is the length of the rows it lists. Negative control:
 * the old stat (completed only) does not add up.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const JS = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const ctx = {
  document: { documentElement: { lang: "en", getAttribute: () => "ltr" } },
  Intl, Date, Math, String, Number, JSON, isNaN, console, Object, Array,
};
ctx.window = ctx;
ctx.t = (key) => key;
ctx.kazmaT = (key, en) => en;
ctx.showToast = () => {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(JS, "locale_format.js"), "utf8"), ctx);
vm.runInContext(fs.readFileSync(path.join(JS, "scheduled.js"), "utf8"), ctx);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail));
}

const page = ctx.scheduledPage();
page.isOverdue = () => false;
page.tasks = [
  { id: "1", status: "done" }, { id: "2", status: "done" }, { id: "3", status: "done" },
  { id: "4", status: "cancelled" },
  { id: "5", status: "pending" }, { id: "6", status: "pending" },
];
const c = page.counts;
ok("History counts every finished row", c.history === 4, JSON.stringify(c));
ok("ended plus failed is the History count", c.ended + c.failed === c.history, JSON.stringify(c));
page.filter = "history";
ok("History's count is the rows it lists", page.filtered.length === c.history);
page.filter = "upcoming";
ok("Upcoming's count is the rows it lists", page.filtered.length === c.upcomingTab);
ok("control: the old stat (completed only) did not add up", c.done + c.failed !== c.history);

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
