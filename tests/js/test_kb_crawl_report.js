/**
 * The Knowledge page's crawl report (2026-10-31): green when the crawl did
 * what was asked, yellow when a page failed or nothing was stored, red when it
 * failed; a page cap or a sitemap read in part is a note, never "Finished with
 * failures"; the pages not stored are listed failures first, each with its
 * reason and what to do. Runs the REAL knowledgePage() from static/js/kb.js.
 *
 * Run: node tests/js/test_kb_crawl_report.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "kb.js"), "utf8");
const catalog = {
  "knowledge.reason.robots": "robots.txt asks crawlers to stay out",
  "knowledge.hint.network": "check the internet connection",
  "knowledge.verdict.empty": "Stored nothing",
};
const window = {
  __KB_STRINGS: {},
  kazmaT: (key, english) => (key in catalog ? catalog[key] : english),
};
const sandbox = { window, console, URLSearchParams, document: undefined };
vm.createContext(sandbox);
vm.runInContext(source + "\nthis.knowledgePage = knowledgePage;", sandbox);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

function page(job) {
  const p = sandbox.knowledgePage();
  p.activeJob = Object.assign({ phase: "done", failed: 0, notes: [], pages: [], acquisition: { status: "completed" } }, job);
  return p;
}

const done = page({ notes: ["page_cap", "sitemap_bounded"], acquisition: { status: "completed", discovery_limited: true },
                    pages: [{ url: "a", outcome: "ingested" }, { url: "b", outcome: "unchanged" }] });
ok("a page cap and a cut sitemap are notes: Done, green", done.jobTitle() !== "Finished with failures"
  && done.jobColor() === "var(--success)" && !done.jobPartial(), done.jobTitle());
ok("the notes are worded", done.noteText("page_cap").length > 0);

const partial = page({ failed: 1, acquisition: { status: "partial" }, pages: [
  { url: "x", outcome: "excluded", reason: "robots" }, { url: "y", outcome: "failed", reason: "network" },
  { url: "z", outcome: "ingested" }] });
ok("a failed page is yellow", partial.jobPartial() && partial.jobColor() === "var(--warning)");
const listed = partial.reportPages();
ok("failures first, stored pages left out", listed.map((p) => p.url).join() === "y,x", listed.map((p) => p.url));
ok("each reason worded, with what to do", partial.reasonText("robots") === catalog["knowledge.reason.robots"]
  && partial.reasonHint("network") === catalog["knowledge.hint.network"]);
ok("counts by outcome", JSON.stringify(partial.reportCounts()) === JSON.stringify([
  { outcome: "ingested", count: 1 }, { outcome: "excluded", count: 1 }, { outcome: "failed", count: 1 }]));

const empty = page({ notes: ["excluded", "nothing_stored"], pages: [{ url: "g", outcome: "excluded", reason: "robots" }] });
ok("nothing stored: yellow, said so", empty.jobEmpty() && empty.jobColor() === "var(--warning)"
  && empty.jobTitle() === "Stored nothing");

const failed = page({ phase: "error" });
ok("a failed crawl is red", failed.jobColor() === "var(--danger)");

process.exit(fail ? 1 : 0);
