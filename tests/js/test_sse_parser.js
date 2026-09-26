/**
 * Node tests for static/js/streaming.js — the text/event-stream reader.
 * Run: node tests/js/test_sse_parser.js
 *
 * Live 2026-09-26: behind Cloudflare Tunnel the chat lost tool rows and
 * approval cards and threw "Cannot read properties of null (reading
 * 'tool_name')" mid-turn. The reader kept each frame's half-built state
 * (event type, data lines, id) in variables local to ONE network read, so
 * a frame cut across two reads lost its event type and its orphaned data
 * line was glued onto the next frame, whose JSON then failed to parse and
 * reached the handlers as null. A direct connection almost never cuts a
 * frame, which is why no local test ever saw it; Cloudflare re-chunks.
 *
 * So these tests cut the stream everywhere: at every byte position, and in
 * hundreds of random multi-cuts, in LF, CRLF and CR line endings, with
 * multi-byte text cut mid-character. The reader must produce exactly the
 * frames the server wrote. The old reader is kept below as a negative
 * control and must FAIL the same harness — a test nobody has seen fail
 * proves nothing (AGENTS.md §28).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "streaming.js",
);
const src = fs.readFileSync(srcPath, "utf8");

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail === undefined ? "" : detail).slice(0, 600));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

// ── The stream, written exactly as kazma_ui.sse_utils.sse_frame writes it ──

function sseFrame(event, data, id) {
  const prefix = id === undefined || id === null ? "" : "id: " + id + "\n";
  return prefix + "event: " + event + "\ndata: " + JSON.stringify(data) + "\n\n";
}

const EXPECTED = [
  { type: "resumed", data: { from: 0, to: 12, running: true }, id: null },
  { type: "token", data: { content: "Let me pull your stored reset info" }, id: "13" },
  { type: "token", data: { content: " — مرحبا 👋 كيف الحال" }, id: "14" },
  { type: "tool_call", data: { tool_name: "memory_search", tool_call_id: "c1", inputs: { query: "reset", limit: 15 } }, id: "15" },
  { type: "tool_result", data: { tool_name: "memory_search", tool_call_id: "c1", result: "x".repeat(900) + " نتيجة " + "y".repeat(900) }, id: "16" },
  { type: "status_update", data: { status: "thinking", message: "Still working… (45s)" }, id: "17" },
  { type: "approval_required", data: { tool: "python_exec", interrupt_id: "10b04c55", args: { code: "print(6*7)\n" }, view: { interactive: true } }, id: "18" },
  { type: "done", data: { content: "Here are your resets.", interrupted: true }, id: "19" },
];

function streamText(eol) {
  let s = "";
  EXPECTED.forEach(function (f, i) {
    s += sseFrame(f.type, f.data, f.id);
    if (i === 2 || i === 5) s += ": keepalive\n\n"; // the server's idle comment
  });
  return eol === "\n" ? s : s.replace(/\n/g, eol);
}

// ── Harness: bytes cut into pieces -> TextDecoder(stream) -> parser ──────────

function runPieces(makeParser, bytes, cuts) {
  const out = [];
  const parser = makeParser(function (type, raw, id) {
    let data = null;
    try { data = JSON.parse(raw); } catch (e) { data = { __unparsed: raw }; }
    out.push({ type: type, data: data, id: id === undefined ? null : id });
  });
  const dec = new TextDecoder();
  let prev = 0;
  cuts.concat([bytes.length]).forEach(function (c) {
    parser.push(dec.decode(bytes.subarray(prev, c), { stream: true }));
    prev = c;
  });
  parser.push(dec.decode());
  if (parser.end) parser.end();
  return out;
}

function sameFrames(got, want) {
  return JSON.stringify(got) === JSON.stringify(want);
}

function countFailures(makeParser, eol, cutSets) {
  const bytes = new Uint8Array(Buffer.from(streamText(eol), "utf8"));
  let bad = 0;
  let first = null;
  cutSets(bytes.length).forEach(function (cuts) {
    const got = runPieces(makeParser, bytes, cuts);
    if (!sameFrames(got, EXPECTED)) {
      bad += 1;
      if (!first) first = { cuts: cuts.slice(0, 6), got: JSON.stringify(got).slice(0, 300) };
    }
  });
  return { bad: bad, first: first };
}

function everySingleCut(n) {
  const sets = [];
  for (let p = 1; p < n; p++) sets.push([p]);
  return sets;
}

function randomCuts(n) {
  // Deterministic PRNG so a failure reproduces.
  let seed = 0x9e3779b9;
  function rnd() {
    seed ^= seed << 13; seed >>>= 0;
    seed ^= seed >>> 17;
    seed ^= seed << 5; seed >>>= 0;
    return seed / 4294967296;
  }
  const sets = [];
  for (let k = 0; k < 400; k++) {
    const cuts = [];
    let p = 0;
    for (;;) {
      p += 1 + Math.floor(rnd() * 40);
      if (p >= n) break;
      cuts.push(p);
    }
    sets.push(cuts);
  }
  return sets;
}

// ── The reader under test ────────────────────────────────────────────────

function loadStream(fetchImpl) {
  const sandbox = {
    console: console,
    AbortController: AbortController,
    TextDecoder: TextDecoder,
    fetch: fetchImpl || function () { return new Promise(function () {}); },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.runInNewContext(src, sandbox);
  return sandbox;
}

const KS = loadStream().KazmaStream;
assert("createSseParser is exported", typeof KS.createSseParser === "function");
const makeParser = KS.createSseParser;

const whole = runPieces(makeParser, new Uint8Array(Buffer.from(streamText("\n"), "utf8")), []);
assert("whole stream: every frame, in order, with its id", sameFrames(whole, EXPECTED), JSON.stringify(whole).slice(0, 400));

["\n", "\r\n", "\r"].forEach(function (eol) {
  const label = eol === "\n" ? "LF" : eol === "\r\n" ? "CRLF" : "CR";
  const single = countFailures(makeParser, eol, everySingleCut);
  assert(label + ": a cut at every byte position changes nothing", single.bad === 0, JSON.stringify(single.first));
  const multi = countFailures(makeParser, eol, randomCuts);
  assert(label + ": 400 random 1-40 byte cuttings change nothing", multi.bad === 0, JSON.stringify(multi.first));
});

// A CR at the end of one read and its LF at the start of the next is ONE
// line end; read as two it is a blank line, which dispatches early.
(function () {
  const got = [];
  const p = makeParser(function (t, raw, id) { got.push([t, raw, id]); });
  p.push("event: token\r");
  p.push("\ndata: {\"a\":1}\r");
  p.push("\ndata: {\"b\":2}\r\n\r\n");
  p.end();
  assert("CRLF cut between CR and LF is one line end",
    JSON.stringify(got) === JSON.stringify([["token", "{\"a\":1}\n{\"b\":2}", null]]),
    JSON.stringify(got));
})();

// A frame the server never finished is dropped, not dispatched half-built.
(function () {
  const got = [];
  const p = makeParser(function (t) { got.push(t); });
  p.push(sseFrame("token", { content: "a" }, 1));
  p.push("id: 2\nevent: token\ndata: {\"content\":");
  p.end();
  assert("an unfinished last frame is not dispatched", JSON.stringify(got) === JSON.stringify(["token"]), JSON.stringify(got));
})();

// ── Negative control: the reader as it was before 2026-09-26 ─────────────
// Its per-read state, verbatim in shape. The harness above must catch it.

function legacyParser(onFrame) {
  let buffer = "";
  return {
    push: function (text) {
      buffer += text;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let eventType = null;
      let dataLines = [];
      let frameId = null;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (line.startsWith("event: ")) eventType = line.slice(7).trim();
        else if (line.startsWith("id: ")) frameId = line.slice(4).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
        else if (line === "" && eventType) {
          onFrame(eventType, dataLines.join("\n"), frameId);
          eventType = null;
          dataLines = [];
          frameId = null;
        }
      }
    },
  };
}
const legacyWhole = runPieces(legacyParser, new Uint8Array(Buffer.from(streamText("\n"), "utf8")), []);
assert("control: the old reader is fine when nothing is cut", sameFrames(legacyWhole, EXPECTED), JSON.stringify(legacyWhole).slice(0, 300));
const legacyCut = countFailures(legacyParser, "\n", everySingleCut);
assert("control: the old reader FAILS when frames are cut (the harness can see the bug)",
  legacyCut.bad > 0, "no cut position broke the old reader — the harness is blind");
assert("control: most cut positions break the old reader",
  legacyCut.bad > (Buffer.byteLength(streamText("\n")) / 4), legacyCut.bad);

// ── End to end through KazmaStream.sse with a re-chunking body ───────────

function chunkedResponse(text, cuts) {
  const bytes = new Uint8Array(Buffer.from(text, "utf8"));
  const pieces = [];
  let prev = 0;
  cuts.concat([bytes.length]).forEach(function (c) { pieces.push(bytes.subarray(prev, c)); prev = c; });
  let i = 0;
  return {
    ok: true,
    body: {
      getReader: function () {
        return {
          read: function () {
            if (i < pieces.length) return Promise.resolve({ done: false, value: pieces[i++] });
            return Promise.resolve({ done: true });
          },
        };
      },
    },
  };
}

function runSse(text, cuts, callbacks) {
  return new Promise(function (resolve) {
    const sandbox = loadStream(function () { return Promise.resolve(chunkedResponse(text, cuts)); });
    const done = callbacks.onDone;
    callbacks.onDone = function (d) { if (done) done(d); resolve(); };
    sandbox.KazmaStream.sse("/api/chat/stream", {}, callbacks);
  });
}

(async function main() {
  const text = streamText("\n");
  const cuts = randomCuts(Buffer.byteLength(text))[7];

  // 1. Every handler gets its frame, whole, however the body was cut.
  const seen = { tokens: [], tools: [], results: [], approvals: [], done: null, errors: [], frameErrors: [] };
  const handle = await runSse(text, cuts, {
    onToken: function (d) { seen.tokens.push(d.content); },
    onToolCall: function (d) { seen.tools.push(d.tool_name); },
    onToolResult: function (d) { seen.results.push(d.tool_name + ":" + d.result.length); },
    onApprovalRequired: function (d) { seen.approvals.push(d.interrupt_id); },
    onStatus: function () {},
    onEvent: function () {},
    onError: function (m) { seen.errors.push(m); },
    onFrameError: function (t, e) { seen.frameErrors.push(t + ":" + e); },
    onDone: function (d) { seen.done = d; },
  });
  void handle;
  assert("e2e: both tokens arrive whole", JSON.stringify(seen.tokens) === JSON.stringify([
    "Let me pull your stored reset info", " — مرحبا 👋 كيف الحال"]), JSON.stringify(seen.tokens));
  assert("e2e: the tool call arrives", JSON.stringify(seen.tools) === "[\"memory_search\"]", JSON.stringify(seen.tools));
  assert("e2e: the tool result arrives whole", JSON.stringify(seen.results) === "[\"memory_search:1807\"]", JSON.stringify(seen.results));
  assert("e2e: the approval arrives", JSON.stringify(seen.approvals) === "[\"10b04c55\"]", JSON.stringify(seen.approvals));
  assert("e2e: done carries its content", seen.done && seen.done.content === "Here are your resets.", JSON.stringify(seen.done));
  assert("e2e: no transport error, no frame error", seen.errors.length === 0 && seen.frameErrors.length === 0,
    JSON.stringify([seen.errors, seen.frameErrors]));

  // 2. A handler that throws costs its own frame, never the stream. It used
  //    to escape into the read loop's catch, which stopped reading for good
  //    and reported a lost connection.
  const after = { tools: 0, results: 0, approvals: 0, done: false, errors: [], frameErrors: [] };
  await runSse(text, cuts, {
    onToken: function () {},
    onToolCall: function () { after.tools += 1; throw new TypeError("paint failed"); },
    onToolResult: function () { after.results += 1; },
    onApprovalRequired: function () { after.approvals += 1; },
    onStatus: function () {},
    onEvent: function () {},
    onError: function (m) { after.errors.push(m); },
    onFrameError: function (t, e) { after.frameErrors.push(t + ":" + (e && e.message)); },
    onDone: function () { after.done = true; },
  });
  assert("isolation: frames after a throwing handler still arrive",
    after.results === 1 && after.approvals === 1 && after.done, JSON.stringify(after));
  assert("isolation: the failure is reported as that frame's, not the stream's",
    JSON.stringify(after.frameErrors) === "[\"tool_call:paint failed\"]" && after.errors.length === 0,
    JSON.stringify(after));

  // 3. Data that is not JSON is that frame's problem: no handler is ever
  //    called with null (the old reader passed null on, and the handler
  //    threw reading 'tool_name').
  const bad = "id: 1\nevent: tool_result\ndata: {not json\n\n" + sseFrame("done", { content: "ok" }, 2);
  const nulls = { calls: 0, frameErrors: [], done: null };
  await runSse(bad, [], {
    onToolResult: function (d) { nulls.calls += 1; if (d === null) throw new Error("null payload"); },
    onFrameError: function (t) { nulls.frameErrors.push(t); },
    onDone: function (d) { nulls.done = d; },
  });
  assert("unparseable data never reaches a handler",
    nulls.calls === 0 && JSON.stringify(nulls.frameErrors) === "[\"tool_result\"]" && nulls.done && nulls.done.content === "ok",
    JSON.stringify(nulls));

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})().catch(function (e) {
  console.error("FAIL unexpected", e && e.stack || e);
  process.exit(1);
});
