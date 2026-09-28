/**
 * A finished turn's tab title and desktop notification read as plain text.
 *
 * Live 2026-09-28: after /replay the background tab's title read
 * "✓ This chat has 6 saved step(s) ... [Open this chat in Time Trav…" --
 * the answer's markdown, link syntax and all. Runs the REAL plainText from
 * modules/turn_visibility.js.
 *
 * Run: node tests/js/test_turn_title_plain.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_visibility.js"),
  "utf8");

const ctx = {
  window: {},
  document: { title: "Kazma — Chat", hidden: false, addEventListener() {} },
  fetch: () => Promise.resolve({ ok: false }),
  setInterval, clearInterval,
};
ctx.window.window = ctx.window;
vm.createContext(ctx);
vm.runInContext(src, ctx);
const plain = ctx.window.KazmaTurnVisibility.plainText;

let fail = 0;
function eq(name, got, want) {
  if (got === want) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "\n  got:  " + JSON.stringify(got) + "\n  want: " + JSON.stringify(want));
}

eq("a link keeps its words",
  plain("This chat has 6 saved step(s). [Open this chat in Time Travel](/replay?thread=abc)"),
  "This chat has 6 saved step(s). Open this chat in Time Travel");
eq("emphasis and code marks go",
  plain("**Done** — `file_read` found *three* ~~bugs~~ issues"),
  "Done — file_read found three bugs issues");
eq("snake_case survives", plain("run file_read_tool now"), "run file_read_tool now");
eq("headings and list marks go", plain("## Result\n- one\n- two\n1. three"), "Result one two three");
eq("a code fence is dropped", plain("Here:\n```js\nconst a = 1;\n```\nthat's it"), "Here: that's it");
eq("an image keeps its alt text", plain("![a chart](/x.png) above"), "a chart above");
eq("Arabic passes through", plain("**تم** — [افتح المحادثة](/chat)"), "تم — افتح المحادثة");
eq("empty is empty", plain(""), "");

// Negative control: the old title text kept the markdown.
const old = (s) => String(s || "").replace(/\s+/g, " ").trim();
eq("control: the old text kept the link syntax",
  old("[Open](/replay)").includes("]("), true);

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
