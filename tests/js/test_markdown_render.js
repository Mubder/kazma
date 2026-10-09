/**
 * Smoke tests for KazmaStream mdRender (extracted from streaming.js).
 * Run: node tests/js/test_markdown_render.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

// renderTable reads window.KazmaBidi (RTL tables). This file was not run by CI
// for a long time and failed on that ReferenceError without anyone seeing it;
// tests/test_js_suite.py now runs every tests/js/test_*.js.
global.window = global;

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

// Original live blemish: unmatched triple ticks shifted all later inline spans.
for (const prefix of ["Fence markers: ", "علامات السياج: "]) {
  const text = prefix + "``` or ~~~; `_protected` then `_join_prose_paragraphs`.";
  const rendered = mdRender(text);
  assert(prefix + "literal fence", rendered.includes("``` or ~~~;"), rendered);
  for (const name of ["_protected", "_join_prose_paragraphs"]) {
    assert(prefix + name, rendered.includes('<code class="inline-code" dir="ltr">' + name + '</code>'), rendered);
  }
  const old = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  assert(prefix + "negative control", !old.includes('<code>_protected</code>'), old);
}
const codeCases = [
  ["``a`b``", "a`b"],
  ["```a``b```", "a``b"],
  ["`**bold** _x_ ~~strike~~`", "**bold** _x_ ~~strike~~"],
  ["`[link](https://example.com) ![image](/image.png)`", "[link](https://example.com) ![image](/image.png)"],
  ["`<script>&`", "&lt;script&gt;&amp;"],
];
for (const [input, body] of codeCases) {
  const rendered = mdRender(input);
  assert("protected " + input, rendered.includes('<code class="inline-code" dir="ltr">' + body + '</code>'), rendered);
  assert("no embedded styles or navigation " + input, !/<strong>|<del>|<a |<img /.test(rendered), rendered);
}
const multiline = mdRender("Before `a\nb` after.");
assert("inline never crosses newline", !multiline.includes('class="inline-code"'), multiline);
const codeBody = '  print("one")  \n\nprint("two")';
const fenced = mdRender("```python\n" + codeBody + "\n```");
assert("fenced whitespace preserved", fenced.includes('<code>  print(&quot;one&quot;)  \n\nprint(&quot;two&quot;)</code>'), fenced);

process.exit(fail ? 1 : 0);
