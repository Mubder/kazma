/**
 * The Dashboard's System Resources show the telemetry stream's reading,
 * and a stream that cannot be read leaves the section on the page.
 *
 * Live 2026-11-05: CPU and RAM read "–" whatever the machine did. The page
 * read cpu_percent / memory_mb / memory_percent; /api/telemetry/stream sends
 * TelemetrySnapshot.to_dict() -- cpu, ram_used_gb, ram_total_gb -- and has
 * since the page was written (2026-06-26). A stream error also hid the whole
 * section until a reload, and a proxy's 502 while Kazma restarts closes an
 * EventSource for good.
 *
 * Runs the REAL startResourceMonitor extracted from static/js/dashboard.js,
 * fed the payload the Python side builds (argv[2], a JSON file written by
 * tests/test_dashboard_resources.py; a copy of its shape when run alone).
 * Negative control: the old message handler leaves both values at "–".
 *
 * Run: node tests/js/test_dashboard_resources.js [payload.json]
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

const PAYLOAD = process.argv[2]
  ? JSON.parse(fs.readFileSync(process.argv[2], "utf8"))
  : { cpu: 28.9, ram_used_gb: 38.39, ram_total_gb: 63.77, gpu: 37.0,
      vram_used_gb: 6.91, vram_total_gb: 22.49, timestamp: 1762300000 };

const start = src.indexOf("  function _formatShare(percent) {");
const end = src.indexOf("\n  }\n", src.indexOf("  function startResourceMonitor() {")) + 4;
ok("the resources code is extractable", start > 0 && end > start);
const setStart = src.indexOf("  function setMetric(id, value) {");
const setCode = src.slice(setStart, src.indexOf("\n  }\n", setStart) + 4);
ok("setMetric is extractable", setStart > 0);

function el(id) { return { id, textContent: "–", style: { width: "0%" } }; }

function load(lang) {
  const els = {};
  ["res-cpu", "cpu-bar", "res-memory", "mem-bar", "resources-section"].forEach((id) => { els[id] = el(id); });
  const document = { getElementById: (id) => els[id] || null };
  const window = {
    KazmaFormat: {
      // locale_format.js: Intl in the page's language, Latin digits.
      number: (n, options) => new Intl.NumberFormat(lang === "ar" ? "ar-u-nu-latn" : "en-US", options).format(n),
    },
  };
  const sources = [];
  const timers = [];
  function EventSource(url) { this.url = url; this.readyState = 1; sources.push(this); }
  function setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }
  // eslint-disable-next-line no-new-func
  const api = new Function("document", "window", "EventSource", "setTimeout",
    "var _timers = []; var _resourceStream = null;\n" +
    "function $(id) { return document.getElementById(id); }\n" +
    setCode + src.slice(start, end) +
    "\nreturn { start: startResourceMonitor, timers: function () { return _timers; }," +
    " stream: function () { return _resourceStream; } };",
  )(document, window, EventSource, setTimeout);
  return { api, els, sources, timers };
}

// English: the stream's reading is shown.
{
  const t = load("en");
  t.api.start();
  ok("one stream opened", t.sources.length === 1 && t.sources[0].url === "/api/telemetry/stream");
  t.sources[0].onmessage({ data: JSON.stringify(PAYLOAD) });
  const cpu = t.els["res-cpu"].textContent;
  const mem = t.els["res-memory"].textContent;
  ok("cpu shown", cpu !== "–" && /\d/.test(cpu) && cpu.includes("%"), cpu);
  ok("cpu bar follows", t.els["cpu-bar"].style.width === Math.min(100, PAYLOAD.cpu) + "%", t.els["cpu-bar"].style.width);
  ok("memory shown as used / total", mem !== "–" && mem.includes("/") && /GB/.test(mem) && mem.includes("%"), mem);
  const share = (PAYLOAD.ram_used_gb / PAYLOAD.ram_total_gb) * 100;
  ok("memory bar follows", Math.abs(parseFloat(t.els["mem-bar"].style.width) - share) < 0.001, t.els["mem-bar"].style.width);
  if (!process.argv[2]) {
    ok("en exact cpu", cpu === "28.9%", cpu);
    ok("en exact memory", mem === "38.4 GB / 63.8 GB (60.2%)", mem);
  }

  // A reading without numbers (the snapshot's error case) says "–".
  t.sources[0].onmessage({ data: JSON.stringify({ cpu: 0, ram_used_gb: 0, ram_total_gb: 0, error: "psutil missing" }) });
  ok("error reading: memory unknown", t.els["res-memory"].textContent === "–", t.els["res-memory"].textContent);
  ok("error reading: cpu unknown, not 0%", t.els["res-cpu"].textContent === "–", t.els["res-cpu"].textContent);
  ok("error reading: bars empty", t.els["cpu-bar"].style.width === "0%" && t.els["mem-bar"].style.width === "0%");
  t.sources[0].onmessage({ data: "not json" });
  ok("unreadable frame: cpu unknown", t.els["res-cpu"].textContent === "–", t.els["res-cpu"].textContent);

  // A dropped stream the browser reconnects: values reset, nothing restarted.
  t.sources[0].onmessage({ data: JSON.stringify(PAYLOAD) });
  t.sources[0].readyState = 0;
  t.sources[0].onerror();
  ok("error: values reset", t.els["res-cpu"].textContent === "–" && t.els["res-memory"].textContent === "–");
  ok("error: the section stays", t.els["resources-section"].style.display !== "none");
  ok("reconnecting: no restart scheduled", t.timers.length === 0, t.timers.length);

  // A stream closed for good (a 502 while Kazma restarts): opened again later.
  t.sources[0].readyState = 2;
  t.sources[0].onerror();
  ok("closed: one restart scheduled and kept for teardown", t.timers.length === 1 && t.api.timers().length === 1);
  ok("closed: the restart waits", t.timers[0].ms >= 5000, t.timers[0].ms);
  t.timers[0].fn();
  ok("closed: a new stream opened", t.sources.length === 2 && t.api.stream() === t.sources[1]);
  t.sources[1].onmessage({ data: JSON.stringify(PAYLOAD) });
  ok("the new stream is read", t.els["res-cpu"].textContent !== "–");
}

// Arabic: the same reading, in the page's language, Latin digits.
{
  const t = load("ar");
  t.api.start();
  t.sources[0].onmessage({ data: JSON.stringify(PAYLOAD) });
  const mem = t.els["res-memory"].textContent;
  ok("ar memory shown", mem !== "–" && /[0-9]/.test(mem) && !/[٠-٩]/.test(mem), mem);
  ok("ar cpu shown", /[0-9]/.test(t.els["res-cpu"].textContent), t.els["res-cpu"].textContent);
}

// Negative control: the page's handler before 2026-11-05.
{
  const els = { "res-cpu": el("res-cpu"), "res-memory": el("res-memory") };
  const data = PAYLOAD;
  if (data.cpu_percent !== undefined) {
    els["res-cpu"].textContent = data.cpu_percent.toFixed(1) + "%";
    els["res-memory"].textContent = data.memory_mb ? data.memory_mb.toFixed(0) + " MB" : "–";
  }
  ok("the old handler showed nothing", els["res-cpu"].textContent === "–" && els["res-memory"].textContent === "–");
}

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
