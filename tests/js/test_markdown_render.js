/**
 * Smoke tests for KazmaStream mdRender (extracted from streaming.js).
 * Run: node tests/js/test_markdown_render.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const srcPath = path.join(
  __dirname,
  "..",
  "..",
  "kazma-ui",
  "kazma_ui",
  "static",
  "js",
  "streaming.js",
);
const src = fs.readFileSync(srcPath, "utf8");
const start = src.indexOf("var mdRender = (function()");
const end = src.indexOf("function copyCode", start);
if (start < 0 || end < 0) {
  console.error("Could not locate mdRender in streaming.js");
  process.exit(1);
}
const chunk = src.slice(start, end).trim();
const expr = chunk.replace(/^var mdRender = /, "").replace(/;?\s*$/, "");
// eslint-disable-next-line no-eval
const mdRender = eval("(" + expr + ")");

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, (detail || "").slice(0, 240));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

const table = mdRender(
  "| Check | Result |\n|---|---|\n| Turn=0 | 0 |\n| Git | Preserved |",
);
assert(
  "table",
  table.includes("<table") && table.includes("<th") && table.includes("Turn=0"),
  table,
);
assert("table-wrap", table.includes("md-table-wrap"), table);

const ul = mdRender("- alpha\n- beta\n  - nested");
assert("ul", ul.includes("<ul") && ul.includes("<li") && ul.includes("alpha"), ul);

const ol = mdRender("1. one\n2. two");
assert("ol", ol.includes("<ol") && ol.includes("<li") && ol.includes("one"), ol);

const task = mdRender("- [x] done\n- [ ] todo");
assert(
  "task",
  task.includes("checkbox") && task.includes("checked") && task.includes("todo"),
  task,
);

const quote = mdRender("> note\n> second");
assert("quote", quote.includes("<blockquote") && quote.includes("note"), quote);

const header = mdRender("## Title\n\npara **bold** and `code`");
assert(
  "header",
  header.includes("<h2") &&
    header.includes("<strong>") &&
    header.includes("inline-code"),
  header,
);

// Prose with a single pipe must NOT become a table
const prose = mdRender("Use A | B as alternatives.");
assert("prose-pipe", !prose.includes("<table"), prose);

process.exit(fail ? 1 : 0);
