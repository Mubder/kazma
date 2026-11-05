"use strict";
// The Swarm page's Recent Results card lists the tasks the store holds, whoever
// dispatched them (live 2026-11-05: "No tasks dispatched yet" over five
// finished tasks a chat's dispatch_swarm had run). The empty state shows only
// after a read that succeeded and found nothing; a failed read says it failed.
// Negative controls: the old template showed the empty state before any read,
// and the old page never read the store at all.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const { makeDocument } = require("./_dom.js");

const root = path.join(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "kazma-ui/kazma_ui/static/js/swarm.js"), "utf8")
  .replace(/\r\n/g, "\n");
const template = fs.readFileSync(path.join(root, "kazma-ui/kazma_ui/templates/swarm.html"), "utf8")
  .replace(/\r\n/g, "\n");

function slice(name) {
  const start = source.indexOf("  function " + name + "(");
  assert(start > 0, name + " not found in swarm.js");
  const end = source.indexOf("\n  }\n", start) + 4;
  return source.slice(start, end);
}

function page(fetchImpl) {
  const { document, El } = makeDocument();
  const make = document.createElement;
  document.createElement = (tag) => { const el = make(tag); el.style = {}; return el; };
  const byId = {};
  for (const id of ["task-results-status", "task-results-empty", "task-results-list"]) {
    const el = document.createElement("div");
    el.setAttribute("id", id);
    byId[id] = el;
  }
  byId["task-results-empty"].style.display = "none";
  const context = {
    document,
    fetch: fetchImpl,
    $: (id) => byId[id] || null,
    t: (key) => "[" + key + "]",
    esc: (s) => String(s == null ? "" : s),
    stateLabel: (s) => "state:" + s,
    workerLabels: (ws) => (ws || []).join(", "),
  };
  vm.createContext(context);
  vm.runInContext(
    source.slice(source.indexOf("  var RECENT_RESULTS"), source.indexOf("\n", source.indexOf("  var RECENT_RESULTS"))) +
    ["hideResultsNotes", "recentResultOutput", "recentResultCard", "hasResultCard", "loadRecentResults"].map(slice).join("\n"),
    context);
  return { context, byId, El };
}

function answer(body, ok = true, status = 200) {
  return () => Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
}

(async () => {
  // The template: nothing says "none" before the read; a loading note does.
  const emptyTag = /<div id="task-results-empty"[^>]*>/.exec(template)[0];
  assert(/display:\s*none/.test(emptyTag), "the empty state shows before the store was read");
  assert(/id="task-results-status"[^>]*>\{\{ t\('common\.loading'\) \}\}/.test(template),
         "no loading note while the store is read");
  // Control: the old markup, empty state visible by default, fails the same check.
  assert(!/display:\s*none/.test('<div id="task-results-empty" style="text-align:center;padding:24px;color:var(--text-muted);">'));

  // The page reads the store when it opens.
  const initStart = source.indexOf("function init()");
  const initBody = source.slice(initStart, source.indexOf("\n  }\n", initStart));
  assert(initStart > 0 && /loadRecentResults\(\);/.test(initBody), "the page never reads the stored tasks");

  // Stored tasks are listed, newest first, each opening its details.
  const tasks = [
    { id: "t-new", status: "completed", workers: ["generalist-pool-a-1"], prompt: "Summarize WAL", synthesized_output: "WAL lets readers..." },
    { id: "t-old", status: "failed", workers: ["coder"], prompt: "Build it", error: "worker crashed" },
  ];
  let p = page(answer({ tasks }));
  await p.context.loadRecentResults();
  const cards = p.byId["task-results-list"].children;
  assert.deepStrictEqual(cards.map((c) => c.getAttribute("data-task-id")), ["t-new", "t-old"]);
  assert(/WAL lets readers/.test(cards[0].innerHTML) && /state:completed/.test(cards[0].innerHTML));
  assert(/worker crashed/.test(cards[1].innerHTML));
  assert.strictEqual(p.byId["task-results-empty"].style.display, "none", "the empty state shows over stored tasks");
  assert.strictEqual(p.byId["task-results-status"].style.display, "none");

  // A second read adds nothing twice.
  await p.context.loadRecentResults();
  assert.strictEqual(p.byId["task-results-list"].children.length, 2, "a second read duplicated the cards");

  // An empty store, read successfully: now the empty state.
  p = page(answer({ tasks: [] }));
  await p.context.loadRecentResults();
  assert.strictEqual(p.byId["task-results-empty"].style.display, "block");

  // A refused read and a failed one: the failure is said, never "none".
  for (const fetchImpl of [answer({}, false, 503), () => Promise.reject(new Error("offline"))]) {
    p = page(fetchImpl);
    await p.context.loadRecentResults();
    assert.notStrictEqual(p.byId["task-results-empty"].style.display, "block", "a failed read said there are no tasks");
    assert.strictEqual(p.byId["task-results-status"].textContent, "[common.request_failed]");
    assert.strictEqual(p.byId["task-results-status"].style.display, "block");
  }

  console.log("swarm Recent Results lists stored tasks; empty only after a successful read");
})().catch((err) => { console.error(err); process.exit(1); });
