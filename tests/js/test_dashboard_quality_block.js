/**
 * The Dashboard's answer-quality block stays on screen, and its two charts
 * draw the ledger's buckets.
 *
 * The block was emptied and rebuilt on every 10 s poll and emptied to one
 * line on any failed read until the next: on the live install it vanished
 * and came back while Kazma was idle (2026-10-22). Now an unchanged reading
 * is not redrawn, a failed read keeps the last one, and a second failure in
 * a row says so. The charts drew running totals the open page had sampled;
 * they now draw /api/dashboard/usage's buckets. Runs the REAL code extracted
 * from static/js/dashboard.js; the old loader is the negative control.
 *
 * Run: node tests/js/test_dashboard_quality_block.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "dashboard.js"),
  "utf8").replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

const qStart = src.indexOf("  var _qualityShown = null;");
const qEnd = src.indexOf("  function fetchStatusFallback()");
ok("the quality loader is extractable", qStart > 0 && qEnd > qStart);
const cStart = src.indexOf("  function refreshCharts(range) {");
const cEnd = src.indexOf("  function drawTokenChart()");
ok("the chart loader is extractable", cStart > 0 && cEnd > cStart);

function el(id) {
  return {
    id, children: [], textContent: "", style: {}, wipes: 0,
    replaceChildren() { this.children = []; this.wipes += 1; },
    appendChild(c) { this.children.push(c); return c; },
  };
}
const documentStub = {
  createElement: (tag) => ({ tag, style: {}, textContent: "", children: [],
    appendChild(c) { this.children.push(c); return c; } }),
  createTextNode: (t) => ({ text: t }),
};
const windowStub = { tOr: (key, en) => en };
const tick = () => new Promise((r) => setImmediate(r));

function answer(kind, reading) {
  if (kind === "lost") return Promise.reject(new TypeError("Failed to fetch"));
  if (kind === "unavailable") return Promise.resolve({ ok: true, json: async () => ({ available: false }) });
  return Promise.resolve({ ok: true, json: async () => reading });
}
const READING = { available: true, totals: { turns: 6, argument_recheck: 1 },
  recent: [{ ts: "2026-10-22T09:00:00+00:00", turn_id: "t1", signals: ["argument_recheck"] }] };

function quality(text, answers) {
  const dom = { "answer-quality-status": el("s"), "answer-quality-counts": el("c"), "answer-quality-recent": el("r") };
  let i = 0;
  const fetch = () => answer(answers[Math.min(i++, answers.length - 1)], READING);
  // eslint-disable-next-line no-new-func
  const fn = new Function("$", "fetch", "window", "document",
    text + "\nreturn fetchAnswerQuality;")((id) => dom[id], fetch, windowStub, documentStub);
  return { fn, dom };
}

const OLD = `
  function fetchAnswerQuality() {
    var status = $('answer-quality-status');
    var counts = $('answer-quality-counts');
    var recent = $('answer-quality-recent');
    fetch('/api/dashboard/answer-quality')
      .then(function(r) { if (!r.ok) throw new Error('unavailable'); return r.json(); })
      .then(function(data) {
        counts.replaceChildren();
        recent.replaceChildren();
        if (!data.available) throw new Error('unavailable');
        status.textContent = 'Retained graph turns: ' + data.totals.turns;
        counts.appendChild({}); recent.appendChild({});
      })
      .catch(function() {
        counts.replaceChildren();
        recent.replaceChildren();
        status.textContent = 'unavailable';
      });
  }`;

(async () => {
  const qText = src.slice(qStart, qEnd);

  // An unchanged reading is not redrawn.
  {
    const q = quality(qText, ["ok"]);
    q.fn(); await tick(); await tick();
    const wipes = q.dom["answer-quality-counts"].wipes;
    q.fn(); await tick(); await tick();
    q.fn(); await tick(); await tick();
    ok("the first reading is drawn", q.dom["answer-quality-counts"].children.length === 7 && wipes === 1, wipes);
    ok("an unchanged reading is not redrawn", q.dom["answer-quality-counts"].wipes === 1,
      q.dom["answer-quality-counts"].wipes);
  }
  // One failed read keeps the reading on screen, unmarked.
  for (const kind of ["lost", "unavailable"]) {
    const q = quality(qText, ["ok", kind, "ok"]);
    q.fn(); await tick(); await tick();
    const before = q.dom["answer-quality-status"].textContent;
    q.fn(); await tick(); await tick();
    ok(`one ${kind} read keeps the counts`, q.dom["answer-quality-counts"].children.length === 7);
    ok(`one ${kind} read keeps the list`, q.dom["answer-quality-recent"].children.length === 1);
    ok(`one ${kind} read changes no text`, q.dom["answer-quality-status"].textContent === before);
  }
  // The control: the old loader emptied the block on one failed read.
  {
    const q = quality(OLD, ["ok", "lost"]);
    q.fn(); await tick(); await tick();
    q.fn(); await tick(); await tick();
    ok("negative control: the old loader emptied the block on one failed read",
      q.dom["answer-quality-counts"].children.length === 0 && q.dom["answer-quality-status"].textContent === "unavailable");
  }
  // Two failures in a row say the reading is old; the next good one clears it.
  {
    const q = quality(qText, ["ok", "lost", "lost", "ok"]);
    for (let n = 0; n < 3; n += 1) { q.fn(); await tick(); await tick(); }
    ok("a second failure says the reading is old",
      /Could not refresh; showing the last reading\./.test(q.dom["answer-quality-status"].textContent));
    ok("and keeps the counts", q.dom["answer-quality-counts"].children.length === 7);
    q.fn(); await tick(); await tick();
    ok("the next good read clears the note",
      !/Could not refresh/.test(q.dom["answer-quality-status"].textContent), q.dom["answer-quality-status"].textContent);
  }
  // Nothing ever read: the block says it is unavailable.
  {
    const q = quality(qText, ["lost"]);
    q.fn(); await tick(); await tick();
    ok("never read: unavailable", /unavailable/.test(q.dom["answer-quality-status"].textContent));
  }

  // The charts draw the ledger's buckets, per bucket.
  {
    const asked = [];
    const drawn = [];
    const buckets = [{ start: 100, tokens: 1500, cost: 0.03 }, { start: 400, tokens: 0, cost: 0 },
      { start: 700, tokens: 70, cost: 0.003 }];
    const fetch = (url) => { asked.push(url); return answer("ok", { available: true, buckets }); };
    // eslint-disable-next-line no-new-func
    const api = new Function("fetch", "drawn",
      "var chartRange = 'hour'; var chartBuckets = [];\n" +
      "function drawTokenChart() { drawn.push(['tokens', chartSeries('tokens')]); }\n" +
      "function drawCostChart() { drawn.push(['cost', chartSeries('cost')]); }\n" +
      src.slice(cStart, cEnd) + "\nreturn { refreshCharts: refreshCharts, range: function() { return chartRange; } };",
    )(fetch, drawn);
    api.refreshCharts("day");
    await tick(); await tick();
    ok("the chart asks the ledger for its range", asked[0] === "/api/dashboard/usage?range=day", asked[0]);
    const tokens = drawn.find((d) => d[0] === "tokens");
    ok("the token bars are each bucket's spend, not a running total",
      tokens && tokens[1].map((p) => p.v).join(",") === "1500,0,70", tokens && JSON.stringify(tokens[1]));
    ok("each bar is placed at its bucket's time", tokens && tokens[1][2].t === 700000);
    const cost = drawn.find((d) => d[0] === "cost");
    ok("the cost line is each bucket's cost", cost && cost[1].map((p) => p.v).join(",") === "0.03,0,0.003");
    api.refreshCharts();
    await tick(); await tick();
    ok("a refresh keeps the chosen range", asked[1] === "/api/dashboard/usage?range=day", asked[1]);
  }

  if (fail) {
    console.log(fail + " failed");
    process.exit(1);
  }
  console.log("all passed");
})();
