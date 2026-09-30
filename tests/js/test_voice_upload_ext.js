/**
 * The voice upload names the recording's real container (2026-09-30).
 *
 * STT providers (Whisper) read the audio format from the file extension.
 * Safari's MediaRecorder records audio/mp4, and voice.js sent it as
 * voice.webm, so an iPhone recording could not be decoded. Runs the REAL
 * voice.js and checks its uploadExtFor() against the MIME types browsers'
 * MediaRecorder actually produces; the old inline mapping is the control.
 *
 * Run: node tests/js/test_voice_upload_ext.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "voice.js"), "utf8");

const ctx = { console, String, Math, JSON, Promise, setTimeout, clearTimeout };
ctx.window = ctx;
ctx.addEventListener = function () {};
// A page still loading: voice.js defers its button setup (fetch, navigator)
// to DOMContentLoaded, which never fires here — only the pure helpers run.
ctx.document = { readyState: "loading", getElementById: () => null, addEventListener() {} };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const ext = ctx.KazmaVoice && ctx.KazmaVoice.uploadExtFor;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + (detail ? " — " + detail : ""));
}

ok("voice.js exposes uploadExtFor", typeof ext === "function");

// What MediaRecorder produces per browser, and the extension Whisper accepts.
const cases = [
  ["audio/webm;codecs=opus", "webm"],   // Chrome, Edge, Firefox
  ["audio/webm", "webm"],
  ["audio/ogg;codecs=opus", "ogg"],     // Firefox (ogg mode)
  ["audio/mp4", "mp4"],                 // Safari (macOS + iOS)
  ["audio/mp4;codecs=mp4a.40.2", "mp4"],
  ["audio/x-m4a", "m4a"],
  ["audio/mpeg", "mp3"],
  ["audio/wav", "wav"],
  ["", "webm"],                          // unknown -> the historical default
  [undefined, "webm"],
];
for (const [mime, want] of cases) {
  const got = ext ? ext(mime) : undefined;
  ok(`${JSON.stringify(mime)} -> ${want}`, got === want, `got ${got}`);
}

// Negative control: the old inline mapping sent Safari's mp4 as webm.
function oldExt(type) {
  let e = "webm";
  if (type.includes("ogg")) e = "ogg";
  else if (type.includes("mp3")) e = "mp3";
  else if (type.includes("wav")) e = "wav";
  return e;
}
ok("control: the old mapping mislabels Safari's audio/mp4",
   oldExt("audio/mp4") === "webm");

if (fail) {
  console.log(`\n${fail} failure(s)`);
  process.exit(1);
}
console.log("\nall voice upload extension checks passed");
