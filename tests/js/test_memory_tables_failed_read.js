"use strict";
// The Memory page's tables say when a read failed (2026-10-15).
//
// The beliefs, entities and pending-merges tables painted "No rows" for a
// failed read (an error answer has no rows), and then -- once "No rows" waited
// for a successful read -- nothing at all: an empty table with no word. A
// failed read now marks its table failed, and memory.html shows the
// catalog's "load failed" line in the table area; a successful empty answer
// still says "No rows". Both an error answer (HTTP 503) and a network failure
// are driven through the real loaders. Negative control: the loaders before
// this change never mark a table failed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const ROOT = path.join(__dirname, "../../kazma-ui/kazma_ui");
const source = fs.readFileSync(path.join(ROOT, "static/js/memory.js"), "utf8").replace(/\r\n/g, "\n");
const template = fs.readFileSync(path.join(ROOT, "templates/memory.html"), "utf8").replace(/\r\n/g, "\n");

const TABLES = [
  ["beliefs", "loadBeliefs", "memory.console.load_failed"],
  ["entities", "loadEntities", "memory.console.load_failed"],
  ["merges", "loadMerges", "memory.console.merges_load_failed"],
];

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
const empty = async () => ({ ok: true, status: 200, json: async () => ({ beliefs: [], entities: [], merges: [], total: 0 }) });

for (const [name, loader] of TABLES) {
  test(`${name}: an error answer marks the table failed, never empty`, async () => {
    const p = page(source, refused);
    await p[loader]();
    assert.equal(p[name + "Failed"], true);
    assert.equal(p[name + "Loaded"], false);
  });

  test(`${name}: a network failure marks the table failed and still reaches the caller`, async () => {
    const p = page(source, offline);
    await assert.rejects(p[loader]());
    assert.equal(p[name + "Failed"], true);
    assert.equal(p[name + "Loaded"], false);
  });

  test(`${name}: an empty answer is loaded, not failed`, async () => {
    const p = page(source, empty);
    await p[loader]();
    assert.equal(p[name + "Failed"], false);
    assert.equal(p[name + "Loaded"], true);
  });
}

test("memory.html shows each table's failure line, worded from the catalog", () => {
  for (const [name, , key] of TABLES) {
    const row = new RegExp(
      `<template x-if="${name}Failed">\\s*<tr><td colspan="\\d+" role="alert"[^>]*>\\{\\{ t\\('${key.replace(/\./g, "\\.")}'\\) \\}\\}</td></tr>`,
    );
    assert.match(template, row, `${name}: no failure row`);
    // The empty row still waits for a successful read.
    assert.ok(template.includes(`<template x-if="${name}Loaded && !${name}.length">`), name);
  }
});

test("negative control: the loaders before this change never mark a table failed", async () => {
  const old = source
    .replace(/this\._readTable\("(beliefs|entities|merges)", append,\s*/g, "api(")
    .replace(/\n\s*if \(!append\) this\.(beliefs|entities|merges)Failed = !!d\.error;/g, "");
  assert.notEqual(old, source, "the control must change the loaders");
  for (const [name, loader] of TABLES) {
    const p = page(old, refused);
    await p[loader]();
    assert.notEqual(p[name + "Failed"], true, `${name}: the control marked the table failed`);
  }
});
