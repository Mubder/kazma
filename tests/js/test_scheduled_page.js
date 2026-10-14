/**
 * The Scheduled page tells the truth about every row (Scheduled audit
 * 2026-10-13, SCH-S04, S07, S02, S12).
 *
 * Runs the REAL scheduled.js (and locale_format.js) in a vm:
 *  - every status maps to a class; an unknown one is "needs attention", never
 *    counted as Completed (the page counted everything not pending/running as
 *    done -- a held X post, an unknown outcome, a missed reminder);
 *  - the controls shown are exactly the server's task.actions, never guessed
 *    from the status (Edit and Delete were offered on running jobs that the
 *    server then refused with 404);
 *  - a timing-only edit sends no prompt, and sends the version it was read
 *    at (the page sent the 200-character summary back as the prompt);
 *  - dates go through KazmaFormat and name the zone.
 * Each check runs the old rule too, as its negative control.
 *
 * Run: node tests/js/test_scheduled_page.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const JS = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const src = fs.readFileSync(path.join(JS, "scheduled.js"), "utf8");
const fmtSrc = fs.readFileSync(path.join(JS, "locale_format.js"), "utf8");

function load(lang, catalog) {
  const ctx = {
    document: { documentElement: { lang: lang, getAttribute: () => (lang === "ar" ? "rtl" : "ltr") } },
    Intl, Date, Math, String, Number, JSON, isNaN, console, Object, Array,
  };
  ctx.window = ctx;
  ctx.t = (key) => (catalog && catalog[key]) || key;
  ctx.kazmaT = (key, en) => en;
  ctx.showToast = () => {};
  vm.createContext(ctx);
  vm.runInContext(fmtSrc, ctx);
  vm.runInContext(src, ctx);
  return ctx;
}

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail));
}

const ctx = load("en");
const page = ctx.scheduledPage();
const MAP = ctx.SCHEDULED_STATUS_CLASS;
const CLASSES = ["upcoming", "attention", "done", "failed", "cancelled"];

// ── every status has a class, and is counted in it ────────────────────────
for (const status of Object.keys(MAP)) {
  page.tasks = [{ source: "cron", id: 1, status: status }];
  const c = page.counts;
  const cls = MAP[status];
  ok("status " + status + " counts as " + cls, CLASSES.includes(cls) && c[cls] === 1, JSON.stringify(c));
}
page.tasks = [{ source: "x", id: 9, status: "something_new" }];
ok("an unknown status needs attention, never Completed",
   page.counts.attention === 1 && page.counts.done === 0, JSON.stringify(page.counts));
for (const st of ["awaiting_approval", "outcome_unknown", "missed", "delivery_unknown"]) {
  page.tasks = [{ source: "x", id: 1, status: st }];
  ok(st + " is not Completed", page.counts.done === 0);
}

// Negative control: the old counter put everything else under done.
function oldCounts(tasks) {
  const c = { upcoming: 0, failed: 0, done: 0 };
  for (const t of tasks) {
    const st = t.status || "";
    if (st === "pending" || st === "running") c.upcoming++;
    else if (st === "failed") c.failed++;
    else c.done++;
  }
  return c;
}
ok("negative control: the old counter calls a held post Completed",
   oldCounts([{ status: "awaiting_approval" }, { status: "outcome_unknown" }]).done === 2);

// ── the controls are the server's ────────────────────────────────────────
const running = { source: "cron", status: "running", actions: ["edit", "cancel"] };
const finished = { source: "cron", status: "done", actions: ["restore"] };
const bare = { source: "cron", status: "running" };
ok("running: the controls the server allows", page.canEdit(running) && page.canDelete(running) && !page.canRestore(running));
ok("finished: only Run again", !page.canEdit(finished) && !page.canDelete(finished) && page.canRestore(finished));
ok("no actions from the server: no controls", !page.canEdit(bare) && !page.canDelete(bare) && !page.canRestore(bare));
ok("negative control: the old canDelete offered a refused delete",
   ((t) => t.status === "pending" || t.status === "running")(bare) === true);
const xRow = { source: "x", status: "scheduled", actions: ["edit", "cancel"], operation_id: "op1", version: 3 };
ok("an X row offers what its queue allows", page.canEdit(xRow) && page.canDelete(xRow));

// ── a timing-only edit sends no prompt, with its version ─────────────────
const long = "Check the deploy dashboard. " + "x".repeat(600) + " END";
const form = { job_id: "cron-1", timing: "2h", originalTiming: "1h", prompt: long, original: long, version: 4 };
const body = page.editBody(form);
ok("timing-only edit: no prompt sent", body.prompt === undefined && body.timing === "2h", JSON.stringify(body));
ok("the version it was read at", body.expected_version === 4);
const edited = page.editBody(Object.assign({}, form, { prompt: long + " more" }));
ok("an edited prompt is sent", edited.prompt === long + " more");
ok("negative control: the old form sent the summary back",
   ((t) => ({ prompt: t.summary }))({ summary: long.slice(0, 200) }).prompt.length === 200);
ok("openEdit reads the prompt from the server",
   /fetch\('\/api\/scheduled\/cron\/' \+ encodeURIComponent\(task\.id\)/.test(src));

// ── dates through KazmaFormat, with the zone ─────────────────────────────
const when = page.fmtAbs({ when: "2026-10-14T06:00:00+00:00" });
ok("absolute time names a zone", /(UTC|GMT)/.test(when), when);
const ar = load("ar").scheduledPage();
const whenAr = ar.fmtAbs({ when: "2026-10-14T06:00:00+00:00" });
ok("Arabic dates keep Latin digits", /\d/.test(whenAr) && !/[٠-٩]/.test(whenAr), whenAr);
ok("no direct Intl formatting in the page script",
   !/new Intl\.DateTimeFormat\(|new Intl\.RelativeTimeFormat\(|toLocale(String|DateString|TimeString)\(/.test(src));

// ── status labels: never a raw id ────────────────────────────────────────
const catalog = { "scheduled.st_attention": "Needs attention", "scheduled.st_done": "Done" };
const labelled = load("en", catalog).scheduledPage();
ok("a known status reads its catalog label", labelled.statusLabel({ status: "done" }) === "Done");
ok("an unlabelled status reads as needs attention, not its id",
   labelled.statusLabel({ status: "brand_new" }) === "Needs attention");

if (fail) {
  console.log(fail + " failure(s)");
  process.exit(1);
}
console.log("all scheduled page checks passed");
