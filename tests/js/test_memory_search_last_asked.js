"use strict";
// A list of the Memory page shows the answer to what was asked LAST (W6, the
// review of the Arabic lists). A search of a large memory answers after the
// word typed next, and the older rows -- each with its Forget button -- were
// drawn under a search box that says something else. Driven through the real
// loaders: the older answer comes last. Negative control: the loaders without
// the check draw it.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const JS = path.join(__dirname, "../../kazma-ui/kazma_ui/static/js");
const read = (name) => fs.readFileSync(path.join(JS, name), "utf8").replace(/\r\n/g, "\n");

function answers() {
  const waiting = [];
  const fetch = (url) => new Promise((resolve) => waiting.push({
    url,
    answer: (body) => resolve({ ok: true, status: 200, json: async () => body }),
    refuse: () => resolve({ ok: false, status: 503, json: async () => ({ error: "internal_error" }) }),
  }));
  return { waiting, fetch };
}

function tables(code) {
  const { waiting, fetch } = answers();
  const window = { __MEM_STRINGS: {}, KazmaFormat: null };
  vm.runInNewContext(code, {
    window, document: { getElementById: () => null, querySelector: () => null }, fetch, URLSearchParams, console,
  });
  return { page: window.memoryPage(), waiting };
}

const CASES = [["beliefs", "loadBeliefs", "beliefQ"], ["entities", "loadEntities", "entityQ"]];
for (const [name, loader, box] of CASES) {
  const body = (id) => ({ [name]: [{ id }], total: 1, offset: 0, limit: 50 });

  test(`${name}: the older answer does not replace the newer one`, async () => {
    const { page, waiting } = tables(read("memory.js"));
    page[box] = "first";
    const first = page[loader]();
    page[box] = "second";
    const second = page[loader]();
    waiting[1].answer(body("new"));
    await second;
    waiting[0].answer(body("old"));
    await first;
    assert.deepEqual(page[name].map((r) => r.id), ["new"]);
  });

  test(`${name}: an older failure does not mark the newer answer failed`, async () => {
    const { page, waiting } = tables(read("memory.js"));
    const first = page[loader]();
    const second = page[loader]();
    waiting[1].answer(body("new"));
    await second;
    waiting[0].refuse();
    await first;
    assert.deepEqual(page[name].map((r) => r.id), ["new"]);
    assert.notEqual(page[name + "Failed"], true);
  });

  test(`${name}: more rows still come, and not into a table being read anew`, async () => {
    const { page, waiting } = tables(read("memory.js"));
    const first = page[loader]();
    waiting[0].answer(body("one"));
    await first;
    const more = page[loader](true);
    waiting[1].answer(body("two"));
    await more;
    assert.deepEqual(page[name].map((r) => r.id), ["one", "two"]);
    const anew = page[loader]();
    await page[loader](true);
    assert.equal(waiting.length, 3);
    waiting[2].answer(body("three"));
    await anew;
    assert.deepEqual(page[name].map((r) => r.id), ["three"]);
  });

  test(`${name}: negative control, without the check the older answer is drawn`, async () => {
    const code = read("memory.js").replace("const stale = () => asked !== read.asked;", "const stale = () => false;");
    const { page, waiting } = tables(code);
    const first = page[loader]();
    const second = page[loader]();
    waiting[1].answer(body("new"));
    await second;
    waiting[0].answer(body("old"));
    await first;
    assert.deepEqual(page[name].map((r) => r.id), ["old"]);
  });
}

// The console's two lists, cut out of their file with what they call.
function list(code, from, to, element, extra) {
  const { waiting, fetch } = answers();
  const drawn = { innerHTML: "", textContent: "", querySelectorAll: () => [], style: {} };
  const start = code.indexOf(from);
  const body = code.slice(start, code.indexOf(to, start));
  const context = {
    document: { getElementById: (id) => (id === element ? drawn : null) }, fetch, encodeURIComponent, Number, String, Math,
    _MEM_PAGE: 30, _memOffset: 0, _memQuery: "", _esc: String, _attr: String, _mt: (_k, en) => en, _mtData: (_k, v) => v,
    _memWhen: String, _wholeMemoryFold: () => "", _bindWholeMemory() {}, _pageRange() {}, _pagerButtons() {},
    _forgetEpisode() {}, _v2gPredVar: () => "", window: {}, ...extra,
  };
  vm.runInNewContext(body, context);
  return { drawn, waiting, context };
}

const LISTS = [
  ["the memories panel", "let _memAsked = 0;", "document.getElementById('v2-memories-refresh')", "v2-memories-list",
    (c) => c.loadV2Memories(), (id) => ({ episodes: [{ id, preview: id }], total: 1 })],
  ["the facts list", "async function loadV2Beliefs", "// Select a belief's OBJECT node", "v2-belief-list",
    (c, n) => c.loadV2Beliefs("q" + n), () => ({ beliefs: [] })],
];
for (const [name, from, to, element, load, body] of LISTS) {
  const text = (drawn) => drawn.innerHTML + drawn.textContent;

  test(`${name}: the older answer does not replace the newer one`, async () => {
    const { drawn, waiting, context } = list(read("memory_console.js"), from, to, element, { _beliefsAsked: 0 });
    const first = load(context, 1);
    const second = load(context, 2);
    waiting[1].answer(body("new"));
    await second;
    const shown = text(drawn);
    assert.notEqual(shown, "");
    waiting[0].answer(body("row-old"));
    await first;
    assert.equal(text(drawn), shown);
    assert.ok(!text(drawn).includes("row-old"));
  });

  test(`${name}: an older failure is not drawn over the newer answer`, async () => {
    const { drawn, waiting, context } = list(read("memory_console.js"), from, to, element, { _beliefsAsked: 0 });
    const first = load(context, 1);
    const second = load(context, 2);
    waiting[1].answer(body("new"));
    await second;
    const shown = text(drawn);
    waiting[0].refuse();
    await first;
    assert.equal(text(drawn), shown);
  });

  test(`${name}: negative control, without the check the older failure is drawn`, async () => {
    const code = read("memory_console.js").replace(/if \(asked (!==|===) _(mem|beliefs)Asked\)/g,
      (_m, op) => (op === "!==" ? "if (false)" : "if (true)"));
    const { drawn, waiting, context } = list(code, from, to, element, { _beliefsAsked: 0 });
    const first = load(context, 1);
    const second = load(context, 2);
    waiting[1].answer(body("new"));
    await second;
    const shown = text(drawn);
    waiting[0].refuse();
    await first;
    assert.notEqual(text(drawn), shown);
  });
}
