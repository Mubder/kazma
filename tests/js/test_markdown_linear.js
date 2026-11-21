"use strict";
// streaming.js's image, link and table-alignment patterns stay linear
// (Sonar S8786, 2026-11-16).
//
// The link pass, /\[([^\]]+)\]\(([^)]+)\)/g, read the rest of the paragraph
// from every "[" (a reply of "[" characters cost 2.4 s per render at 100,000);
// the image pass did the same from every "![" (1.2 s); and the table-alignment
// test split a leading whitespace run every way (2.5 s). A label, alt or
// address now holds no "[" -- the innermost "[x](u)" is the link, as
// CommonMark has it -- and the alignment row is trimmed before it is tested.
// The renderer is loaded from streaming.js itself; the old renderer is the
// same source with the three old patterns put back (the negative control).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

global.window = global;

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "streaming.js"), "utf8",
).replace(/\r\n/g, "\n");

const SWAPS = [
  [String.raw`return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(String(line).trim());`,
    String.raw`return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);`],
  [String.raw`html.replace(/!\[([^\][]*)\]\(([^)[]+)\)/g`, String.raw`html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g`],
  [String.raw`html.replace(/\[([^\][]+)\]\(([^)[]+)\)/g`, String.raw`html.replace(/\[([^\]]+)\]\(([^)]+)\)/g`],
];

function loadMdRender(source) {
  const start = source.indexOf("var mdRender = (function()");
  const end = source.indexOf("function copyCode", start);
  assert.ok(start >= 0 && end > start, "mdRender not found in streaming.js");
  const expr = source.slice(start, end).trim().replace(/^var mdRender = /, "").replace(/;?\s*$/, "");
  // eslint-disable-next-line no-eval
  return eval("(" + expr + ")");
}

function oldSource() {
  let source = SRC;
  for (const [now, before] of SWAPS) source = source.split(now).join(before);
  return source;
}

const render = loadMdRender(SRC);
const renderBefore = loadMdRender(oldSource());

function seeded(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

function timed(fn) {
  const t = process.hrtime.bigint();
  fn();
  return Number(process.hrtime.bigint() - t) / 1e9;
}

// A "[" between a "[" and the next "]", or between "](" and the next ")": the
// only places the new patterns read differently.
function bracketInside(text) {
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== "[") continue;
    const close = text.indexOf("]", i + 1);
    if (close < 0) continue;
    if (text.lastIndexOf("[", close) > i) return true;
    if (text[close + 1] === "(") {
      const end = text.indexOf(")", close + 2);
      const stop = end < 0 ? text.length : end;
      if (text.slice(close + 2, stop).includes("[")) return true;
    }
  }
  return false;
}

test("streaming.js holds the three new patterns", () => {
  for (const [now] of SWAPS) assert.equal(SRC.split(now).length - 1, 1, now);
});

test("links and images render as before wherever no label or address holds a '['", () => {
  const rnd = seeded(20261116);
  const tokens = ["[", "]", "(", ")", "![", "a", " ", "https://e.example/p", "/p", "x.png", "](", "\n"];
  let compared = 0;
  for (let i = 0; i < 20000; i++) {
    let text = "";
    const k = Math.floor(rnd() * 12);
    for (let j = 0; j < k; j++) text += tokens[Math.floor(rnd() * tokens.length)];
    if (bracketInside(text)) continue;
    compared++;
    assert.equal(render(text), renderBefore(text), JSON.stringify(text));
  }
  assert.ok(compared > 10000, String(compared));
});

test("tables render as before (the alignment row is the same row, trimmed)", () => {
  const rnd = seeded(7);
  const cells = ["---", ":---:", "---:", " --- ", "\t---\t", "--", "x", ""];
  for (let i = 0; i < 5000; i++) {
    const width = 1 + Math.floor(rnd() * 3);
    const pick = () => cells[Math.floor(rnd() * cells.length)];
    const row = (lead, trail) => lead + "|" + Array.from({ length: width }, pick).join("|") + "|" + trail;
    const pad = () => [" ", "  ", "\t", ""][Math.floor(rnd() * 4)];
    const text = "| a | b |\n" + row(pad(), pad()) + "\n| 1 | 2 |";
    assert.equal(render(text), renderBefore(text), JSON.stringify(text));
  }
});

test("intended difference: the innermost [x](u) is the link, and an address holds no '['", () => {
  const nested = render("[x [y](https://a.example/p)");
  assert.match(nested, />y<\/a>/);
  assert.ok(nested.includes("[x "), nested);
  assert.match(renderBefore("[x [y](https://a.example/p)"), />x \[y<\/a>/);
  // An IPv6 address in brackets is no longer a labelled link (it was one
  // before); the bare-address pass still links the address itself.
  assert.ok(!render("[v6](http://[::1]/)").includes(">v6</a>"));
  assert.ok(render("[v6](http://[::1]/)").includes('href="http://[::1]/"'));
  assert.ok(renderBefore("[v6](http://[::1]/)").includes(">v6</a>"));
});

test("hostile replies render at once", () => {
  for (const text of ["[".repeat(100000), "![".repeat(50000), "[a](".repeat(25000), "![a](".repeat(20000),
    "| a | b |\n" + " ".repeat(100000) + "x", "| a | b |\n|---" + " ".repeat(100000) + "x"]) {
    const seconds = timed(() => render(text));
    assert.ok(seconds < 1, `${JSON.stringify(text.slice(0, 12))}: ${seconds.toFixed(3)} s`);
  }
});

test("negative control: the old patterns grow faster than their input", () => {
  const oldLink = (s) => s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "x");
  const oldImage = (s) => s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, "x");
  const oldAlign = (s) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(s);
  const grows = (fn, make, n) => {
    fn(make(n));
    // The fastest of five runs each way: a single slow run on a busy machine
    // must not read as growth (it did, at 30,000 characters, 2026-11-20).
    const once = () => {
      const small = Math.min(...[0, 1, 2, 3, 4].map(() => timed(() => fn(make(n)))));
      const large = Math.min(...[0, 1, 2, 3, 4].map(() => timed(() => fn(make(4 * n)))));
      return large > 6 * small;
    };
    // The majority of three measurements (two that agree end it): this
    // control failed once on a loaded machine (2026-11-21), and one noisy
    // measurement must decide neither way.
    const first = once();
    const second = once();
    return first === second ? first : once();
  };
  assert.ok(grows(oldLink, (n) => "[".repeat(n), 3000));
  assert.ok(grows(oldImage, (n) => "![".repeat(n), 1500));
  assert.ok(grows(oldAlign, (n) => " ".repeat(n) + "x", 3000));
  // The linear pattern at a size whose run takes long enough to measure (at
  // 30,000 characters a run took 0.02 ms, and noise passed for growth).
  assert.ok(!grows((s) => s.replace(/\[([^\][]+)\]\(([^)[]+)\)/g, "x"), (n) => "[".repeat(n), 1000000));
});
