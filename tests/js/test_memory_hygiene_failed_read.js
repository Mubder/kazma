"use strict";
// The Memory page's Hygiene tab says when its preview could not be read
// (Sonar S9383, 2026-11-16).
//
// loadHygiene awaited the preview with no catch: a network failure rejected
// out of it (the page's global toast said "Load failed" for five seconds) and
// left the counts of the last read, and an error answer set them all to
// "(0)" -- "Purge empty (0)" over a store whose preview had failed. A failed
// read now marks the preview failed, memory.html says so in the card and
// hides the counts; a good read clears it. Both an error answer (HTTP 503)
// and a network failure are driven through the real loader. Negative
// control: the loader before this change never marks the preview failed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const ROOT = path.join(__dirname, "../../kazma-ui/kazma_ui");
const source = fs.readFileSync(path.join(ROOT, "static/js/memory.js"), "utf8").replace(/\r\n/g, "\n");
const template = fs.readFileSync(path.join(ROOT, "templates/memory.html"), "utf8").replace(/\r\n/g, "\n");

function page(code, answer) {
  const window = { __MEM_STRINGS: {}, KazmaFormat: null };
  vm.runInNewContext(code, {
    window,
    document: { getElementById: () => null, querySelector: () => null },
    fetch: async (url) => answer(url),
    URLSearchParams,
    console,
  });
  return window.memoryPage();
}

const refused = async () => ({ ok: false, status: 503, json: async () => ({ error: "internal_error" }) });
const offline = async () => { throw new TypeError("Failed to fetch"); };
const preview = async () => ({
  ok: true, status: 200,
  json: async () => ({ ok: true, empty_entities: [{ id: "a" }, { id: "b" }], near_dup_noted: [], invalidated_belief_count: 3 }),
});

test("an error answer marks the preview failed and shows no counts", async () => {
  const p = page(source, refused);
  await p.loadHygiene();
  assert.equal(p.hygieneFailed, true);
  assert.deepEqual(JSON.parse(JSON.stringify(p.hygienePreview)), {});
});

test("a network failure marks the preview failed and does not reject", async () => {
  const p = page(source, offline);
  await p.loadHygiene();  // resolves: the failure is shown on the tab, not lost in a rejection
  assert.equal(p.hygieneFailed, true);
});

test("a good read shows the counts and clears an earlier failure", async () => {
  const p = page(source, preview);
  p.hygieneFailed = true;
  await p.loadHygiene();
  assert.equal(p.hygieneFailed, false);
  assert.equal(p.hygienePreview.empty_entities.length, 2);
  assert.equal(p.hygienePreview.invalidated_belief_count, 3);
});

test("memory.html says the preview failed, from the catalog, and hides the counts then", () => {
  const hygiene = template.slice(template.indexOf("{# HYGIENE #}"), template.indexOf("{# HYGIENE #}") + 2500);
  assert.match(hygiene, /<div x-show="hygieneFailed" role="alert"[^>]*>\{\{ t\('memory\.console\.load_failed'\) \}\}<\/div>/);
  const counts = hygiene.match(/<span[^>]*x-text="'\(' \+ \(hygienePreview\.[^"]*"><\/span>/g) || [];
  assert.equal(counts.length, 3, "three counts");
  for (const span of counts) assert.match(span, /x-show="!hygieneFailed"/, span);
});

test("negative control: the loader before this change never marks the preview failed", async () => {
  const old = source.replace(
    /async loadHygiene\(\) \{[\s\S]*?\n {4}\},/,
    'async loadHygiene() {\n      const d = await api("/api/memory/v2/hygiene/preview");\n      this.hygienePreview = d.ok ? d : {};\n    },',
  );
  assert.notEqual(old, source, "the control must change the loader");
  const refusedPage = page(old, refused);
  await refusedPage.loadHygiene();
  assert.notEqual(refusedPage.hygieneFailed, true);
  const offlinePage = page(old, offline);
  await assert.rejects(offlinePage.loadHygiene());
  assert.notEqual(offlinePage.hygieneFailed, true);
});
