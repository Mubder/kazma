/**
 * The tab title follows the page the tab is on.
 *
 * Live 2026-11-05: after a soft navigation from Chat to the Knowledge
 * Library, the tab kept reading "Kazma - Chat". nav.js gives the tab the next
 * page's title, but modules/turn_visibility.js kept the title it read first
 * and wrote it back at every visibility change and turn frame.
 *
 * Runs the REAL module in a sandbox. Negative control: the old base-title
 * rule (read once, kept) puts the Chat page's title back.
 *
 * Run: node tests/js/test_turn_title_follows_page.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_visibility.js"),
  "utf8").split(String.fromCharCode(13, 10)).join(String.fromCharCode(10));

const DASH = String.fromCharCode(0x2014);
const DOT = String.fromCharCode(0x25CF);
const CHECK = String.fromCharCode(0x2713);
const CHAT = "Kazma " + DASH + " Chat";
const KNOWLEDGE = "Kazma " + DASH + " Knowledge Library";
const MEMORY = "Kazma " + DASH + " Memory";

let fail = 0;
function eq(name, got, want) {
  if (got === want) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "\n  got:  " + JSON.stringify(got) + "\n  want: " + JSON.stringify(want));
}

function load(source) {
  const timers = [];
  const ctx = {
    window: {},
    document: { title: CHAT, hidden: false, addEventListener() {} },
    fetch: () => Promise.resolve({ ok: false }),
    setInterval: (fn, ms) => { timers.push(fn); return timers.length; },
    clearInterval: () => {},
    setTimeout: () => 0,
  };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return { V: ctx.window.KazmaTurnVisibility, doc: ctx.document, timers };
}

// The module, run on the Chat page, then a soft navigation to Knowledge.
{
  const { V, doc } = load(src);
  V.restore();
  eq("chat page keeps its title", doc.title, CHAT);
  doc.title = KNOWLEDGE;              // nav.js syncChrome
  V.restore();                        // the tab is shown again
  eq("after a soft nav the tab shows the new page", doc.title, KNOWLEDGE);

  // A turn running while the tab is hidden: badge over the NEW page's title.
  doc.hidden = true;
  V.noteActivity("token");
  V.noteActivity("tool", "file_read");
  eq("the badge sits on the current page's title", doc.title, DOT + " (2) " + KNOWLEDGE);
  V.noteActivity("token");
  eq("our own badge is not taken for a page title", doc.title, DOT + " (3) " + KNOWLEDGE);
  doc.hidden = false;
  V.restore();
  eq("shown again: the current page's title", doc.title, KNOWLEDGE);

  // Another soft nav while a turn's badge is up.
  doc.hidden = true;
  V.noteActivity("token");
  doc.title = MEMORY;                 // the page changed under the badge
  V.noteActivity("token");
  eq("a page changed under the badge is followed", doc.title, DOT + " (2) " + MEMORY);
  doc.hidden = false;
  V.restore();
  eq("and restored to it", doc.title, MEMORY);
}

// A finished turn in a hidden tab: the done marker, then the page's title.
{
  const { V, doc, timers } = load(src);
  doc.title = KNOWLEDGE;
  doc.hidden = true;
  V.noteActivity("token");
  V.endTurn("**Done** - read 3 files");
  eq("done title names the answer and the page", doc.title, CHECK + " Done - read 3 files " + DASH + " " + KNOWLEDGE);
  eq("one flash timer", timers.length, 1);
  timers[0]();
  eq("flash: the page's title with the marker", doc.title, CHECK + " " + KNOWLEDGE);
  timers[0]();
  eq("flash: back to the answer", doc.title, CHECK + " Done - read 3 files " + DASH + " " + KNOWLEDGE);
  timers[0]();
  eq("the marker never stacks", doc.title, CHECK + " " + KNOWLEDGE);
  doc.hidden = false;
  timers[0]();
  eq("shown: the flash ends on the page's own title", doc.title, KNOWLEDGE);
}

// Negative control: the base-title rule before 2026-11-05 (read once, kept).
{
  const old = src.replace(
    /function baseTitle\(\) \{[\s\S]*?\n  \}\n/,
    "function baseTitle() {\n" +
    "    if (_baseTitle == null) {\n" +
    "      _baseTitle = document.title.replace(/^[\\u25CF\\u2022]\\s*/, '');\n" +
    "    }\n" +
    "    return _baseTitle;\n" +
    "  }\n");
  eq("control: the old rule was substituted", old !== src && old.includes("if (_baseTitle == null) {"), true);
  const { V, doc } = load(old);
  V.restore();
  doc.title = KNOWLEDGE;
  V.restore();
  eq("control: the old rule put the Chat title back", doc.title, CHAT);
}

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
