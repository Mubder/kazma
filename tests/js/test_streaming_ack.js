/**
 * Node behavioral tests for static/js/streaming.js — SSE dispatch routing.
 * Run: node tests/js/test_streaming_ack.js
 *
 * Locks the 2026-09-05 capacity-ack incident class end to end at the
 * transport layer:
 *   - a capacity ack's done frame must paint via paintCapacityReply
 *     (REPLACE semantics, content-key idempotent) and must NEVER be fed
 *     through onToken — the token path APPENDS and doubled the reply
 *     when the capacity frame had already painted (§29F class);
 *   - a lost capacity frame must still paint from the done frame alone
 *     (no "_No response received." watchdog card);
 *   - a REAL turn's done frame (no capacity flag) keeps the pre-existing
 *     onToken content fallback.
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

let currentResponse = null;
function encodeFrame(text) {
  return new Uint8Array(Buffer.from(text, "utf8"));
}
function makeSseResponse(frames) {
  const chunks = frames.map(
    (f) => "event: " + f.event + "\ndata: " + JSON.stringify(f.data) + "\n\n",
  );
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read() {
            if (i < chunks.length) {
              return Promise.resolve({ done: false, value: encodeFrame(chunks[i++]) });
            }
            return Promise.resolve({ done: true });
          },
        };
      },
    },
  };
}

const sandbox = {
  console,
  AbortController,
  TextDecoder,
  fetch: function () {
    return Promise.resolve(currentResponse);
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.runInNewContext(src, sandbox);
const KS = sandbox.KazmaStream;

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail ?? "").slice(0, 400));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

let calls;
sandbox.window.KazmaChat = {
  paintCapacityReply: function (reply, turnId) {
    calls.capPaints.push({ head: String(reply).slice(0, 40), tid: turnId });
  },
  refreshCapacity: function () {},
};

function run(frames) {
  calls = { capPaints: [], tokens: [], done: [], errors: [] };
  currentResponse = makeSseResponse(frames);
  return new Promise(function (resolve) {
    KS.sse("/api/chat/stream", {}, {
      onToken: function (d) { calls.tokens.push(String(d.content || "").slice(0, 40)); },
      onDone: function (d) { calls.done.push(!!d); resolve(); },
      onError: function (m) { calls.errors.push(String(m)); resolve(); },
    });
  });
}

(async function main() {
  // 1) Normal fast path: capacity frame paints, done carries content+capacity.
  await run([
    { event: "capacity", data: { reply: "MISSION ON", long_active: true } },
    { event: "done", data: { tokens: 1, content: "MISSION ON", capacity: true } },
  ]);
  assert("ack: onToken never fed", calls.tokens.length === 0, JSON.stringify(calls));
  assert(
    "ack: done frame painted via paintCapacityReply",
    calls.capPaints.some(function (c) { return c.head === "MISSION ON"; }),
    JSON.stringify(calls),
  );
  assert("ack: stream completed once", calls.done.length === 1 && calls.done[0] === true);

  // 2) Capacity frame LOST in transit — the done frame alone must paint
  //    the reply (this was the "_No response received." card).
  await run([
    { event: "done", data: { tokens: 1, content: "YOLO ON", capacity: true } },
  ]);
  assert(
    "lost-capacity: reply painted from done frame alone",
    calls.capPaints.some(function (c) { return c.head === "YOLO ON"; }),
    JSON.stringify(calls),
  );
  assert("lost-capacity: onToken never fed", calls.tokens.length === 0);

  // 3) REAL turn (no capacity flag): pre-existing onToken content fallback.
  await run([
    { event: "done", data: { tokens: 42, content: "Real assistant reply" } },
  ]);
  assert(
    "real turn: onToken fallback intact",
    calls.tokens.length === 1 && calls.tokens[0] === "Real assistant reply",
    JSON.stringify(calls),
  );
  assert("real turn: no capacity paint", calls.capPaints.length === 0);

  // 4) Real streamed tokens suppress the done-content fallback (pre-existing).
  await run([
    { event: "token", data: { content: "streamed " } },
    { event: "token", data: { content: "reply" } },
    { event: "done", data: { tokens: 2, content: "streamed reply" } },
  ]);
  assert(
    "real tokens: no double feed from done",
    calls.tokens.join("|") === "streamed |reply",
    JSON.stringify(calls),
  );

  if (fail) process.exit(1);
  console.log("all ok");
})().catch(function (e) {
  console.error("HARNESS FAIL", e);
  process.exit(1);
});
