/**
 * The Documents page's Files panel (Spaces S2-12, piece 7).
 * Run: node tests/js/test_space_files.js
 *
 * A move whose answer does not arrive is never "could not be moved" and never
 * silent: the list is read again and the person is told what is known. The
 * control lets it through, as the handler did when it was parked.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "documents.js"), "utf8");

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

/** A page in the person's own space with one shared space, and what it said. */
function page(options) {
  const said = [];
  const window = {
    kazmaSpace: {
      id: "s-own",
      spaces: [{ id: "s-own", personal: true, kind: "sandboxed", name: "Mine" },
        { id: "s-family", personal: false, kind: "sandboxed", name: "Family" },
        { id: "s-repo", personal: false, kind: "host", name: "Repo" }],
    },
    kazmaConfirm: async () => true,
    kazmaGetJson: async () => (options.lists.length > 1 ? options.lists.shift() : options.lists[0]),
    addEventListener() {},
  };
  window.kazmaT = (key, english) => key + "|" + english;
  const sandbox = { window, console, document: { addEventListener() {} }, kazmaT: window.kazmaT,
    fetch: options.fetch, setTimeout, clearTimeout, URLSearchParams };
  vm.createContext(sandbox);
  vm.runInContext((options.source || SCRIPT) + "\nthis.documentsPage = documentsPage;", sandbox);
  const p = sandbox.documentsPage();
  p.toast = (text, tone) => said.push({ key: String(text).split("|")[0], tone });
  return { p, said };
}

const FILE = { path: "رحلة/خطة.txt", size: 3, movable: true, mine: false };
const list = (files, more) => Object.assign(
  { ok: true, available: true, files, total: files.length, cut: false, taken_out: [] }, more || {});
const lost = async () => { throw new TypeError("Failed to fetch"); };
const answers = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });

(async () => {
  let t = page({ fetch: lost, lists: [list([])] });
  await t.p.moveFile(FILE, "s-family");
  assert("a lost answer, the file gone", t.said[0] && t.said[0].key === "documents.files.lost_gone", t.said);
  assert("the page can act again", t.p.acting === false);

  t = page({ fetch: lost, lists: [list([FILE])] });
  await t.p.moveFile(FILE, "s-family");
  assert("a lost answer, the file here", t.said[0] && t.said[0].key === "documents.files.lost_here", t.said);

  t = page({ fetch: lost, lists: [null] });
  await t.p.moveFile(FILE, "s-family");
  assert("a lost answer, no list", t.said[0] && t.said[0].key === "documents.files.lost_unknown", t.said);

  t = page({ fetch: lost, lists: [list([{ path: "a.txt" }], { total: 80 })] });
  await t.p.moveFile(FILE, "s-family");
  assert("a lost answer, only a page of the list", t.said[0] && t.said[0].key === "documents.files.lost_unknown", t.said);

  t = page({ fetch: async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError("<html>"); } }),
    lists: [list([FILE])] });
  await t.p.moveFile(FILE, "s-family");
  assert("a gateway's page is no answer", t.said[0] && t.said[0].key === "documents.files.lost_here", t.said);

  t = page({ fetch: answers(409, { ok: false, code: "changed_meanwhile" }), lists: [list([FILE])] });
  await t.p.moveFile(FILE, "s-family");
  assert("a refusal is worded", t.said[0] && t.said[0].key === "documents.files.err.changed_meanwhile", t.said);
  assert("the list was read again after it", t.p.files.length === 1);

  t = page({ fetch: answers(429, { detail: "slow down" }), lists: [list([FILE])] });
  await t.p.moveFile(FILE, "s-family");
  assert("too many moves are said", t.said[0] && t.said[0].key === "documents.files.err.busy", t.said);

  t = page({ fetch: answers(200, { ok: true }), lists: [list([])] });
  await t.p.moveFile(FILE, "s-family");
  assert("a move is said", t.said[0] && t.said[0].key === "documents.files.shared_done", t.said);

  assert("a space with a project folder is no target",
    t.p.fileShareTargets().map((s) => s.id).join() === "s-family", t.p.fileShareTargets());

  // The last page emptied by a move: the page before it is shown.
  t = page({ fetch: lost, lists: [list([], { total: 50 }), list([FILE], { total: 50 })] });
  t.p.filesOffset = 50;
  await t.p.loadFiles();
  assert("an emptied last page shows the one before", t.p.filesOffset === 0 && t.p.files.length === 1, t.p.filesOffset);

  t = page({ fetch: lost, lists: [null] });
  await t.p.loadFiles();
  assert("a list that cannot be read is said, never hidden", t.p.filesShown() && t.p.filesError);

  // Control: a lost answer let through, as the parked handler did.
  const kept = "j = null; // no answer, or one that is none: the list says what is known";
  assert("the page catches a lost answer in one place", SCRIPT.split(kept).length === 2);
  t = page({ fetch: lost, lists: [list([FILE])], source: SCRIPT.replace(kept, "throw e;") });
  let threw = false;
  await t.p.moveFile(FILE, "s-family").catch(() => { threw = true; });
  assert("control: let through, it is an unhandled error and nothing is said", threw && t.said.length === 0, t.said);

  if (fail) process.exit(1);
})();
