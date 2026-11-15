// Problem reports (Kazma REMAINING 3.1): POST /v1/report and GET /reports.
// Run: node --test test/ (Node 22.5+). In process, on the D1 stand-in.

import { test } from "node:test";
import assert from "node:assert/strict";

import { d1 } from "./d1_standin.mjs";
import { handle } from "../src/index.js";
import {
  KEEP_S, PER_DAY, PER_SOURCE_HOUR, REPORT_MAX, cleanNote, invalid, newId, pagerText, pruneReports,
} from "../src/reports.js";

const NOW = 1_800_000_000;

function env(extra = {}) {
  return { DB: d1(), PAIRING_TOKEN: "p".repeat(40), TELEGRAM_BOT_TOKEN: "123:abc", TELEGRAM_CHAT_ID: "42", ...extra };
}

function netWith() {
  const sent = [];
  return {
    sent,
    fetcher: async (url, init = {}) => {
      if (new URL(String(url)).hostname === "api.telegram.org") {
        sent.push(JSON.parse(init.body).text);
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      return new Response("no", { status: 404 });
    },
  };
}

function post(body, ip = "203.0.113.7") {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://status.kazma.ai/v1/report", {
    method: "POST", body: text, headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip },
  });
}

const REPORT = { kind: "kazma-problem-report", version: "0.21.2", note: "The PDF came out empty", system: {} };

test("a report is kept, numbered, and the owner is told", async () => {
  const e = env();
  const net = netWith();
  const res = await handle(post(REPORT), e, NOW, { fetcher: net.fetcher });
  assert.equal(res.status, 200);
  const { id } = await res.json();
  assert.match(id, /^[a-z0-9]{10}$/);
  const row = await e.DB.prepare("SELECT * FROM problem_reports WHERE id = ?").bind(id).first();
  assert.equal(row.note, "The PDF came out empty");
  assert.equal(row.notified, 1);
  assert.notEqual(row.source, "203.0.113.7", "the address is kept only as an HMAC");
  assert.match(net.sent[0], /^🐞 Problem report [a-z0-9]{10}: someone sent a report to the inbox, saying it is from Kazma 0\.21\.2\./);
  assert.match(net.sent[0], /Anyone can send one; nothing in it is checked\.\nTheir words: "The PDF came out empty"$/);
});

// A stranger's report crafted to read as Kazma Watch's own alerts (2026-11-15).
// The version the review sent: 40 characters or fewer, as the old check allowed.
const FAKE_ALERT = "1)\n\n🔴 [Watch] Kazma is down\n\n";

test("a version that is not a version is refused, and nothing is kept or paged", async () => {
  const e = env();
  const net = netWith();
  for (const version of [FAKE_ALERT, "0.21.2\n🔴 down", "0.21", "v0.21.2", "0.21.2 ", "latest", "0.21.2+g4d\u202e"]) {
    const res = await handle(post({ ...REPORT, version }), e, NOW, { fetcher: net.fetcher });
    assert.equal(res.status, 400, version);
    assert.equal((await res.json()).error, "bad_version");
  }
  assert.equal(await e.DB.prepare("SELECT COUNT(*) AS n FROM problem_reports").first("n"), 0);
  assert.deepEqual(net.sent, []);
  for (const version of ["0.21.2", "0.22.0rc1", "0.21.2+g4d37b2c", "1.0.0.dev3"]) {
    assert.equal(invalid({ ...REPORT, version }), "", version);
  }
});

test("a note pages as one plain quoted line: no line break, direction mark or live link", async () => {
  const e = env();
  const net = netWith();
  const note = "help\n\n🔴 [Watch] Kazma is down\r\nOpen: https://evil.example/login \u202egnp.exe\u2066 " +
    "t.me/fake_support or mail admin@evil.example\u2028\u0007end";
  const res = await handle(post({ ...REPORT, note }), e, NOW, { fetcher: net.fetcher });
  assert.equal(res.status, 200);
  const [text] = net.sent;
  const lines = text.split("\n");
  assert.equal(lines.length, 2, "a header line and the quoted words, nothing else");
  assert.match(lines[0], /^🐞 Problem report [a-z0-9]{10}: someone sent a report/);
  assert.ok(lines[1].startsWith('Their words: "help 🔴 [Watch] Kazma is down Open: '), lines[1]);
  for (const live of ["https://", "evil.example", "t.me", "@evil", "\u202e", "\u2066", "\u2028", "\u0007", "\r"]) {
    assert.ok(!text.includes(live), `${JSON.stringify(live)} reached the page`);
  }
  assert.ok(text.includes("https[:]//evil[.]example/login") && text.includes("t[.]me/fake_support"));
  // The note as sent is still kept whole for the owner's reports page (escaped there).
  const row = await e.DB.prepare("SELECT note FROM problem_reports").first();
  assert.equal(row.note, note);
});

test("a long note is cut, and an empty one says so", () => {
  const long = cleanNote("x".repeat(500));
  assert.equal(long.length, 160);
  assert.ok(long.endsWith("…"));
  assert.match(pagerText("abcdefghjk", "0.21.2", "  \n\u200b "), /Their words: "\(no words\)"$/);
});

test("negative control: the old check and page made a crafted report a fake alert", () => {
  // The inbox as it was: any version of 40 characters or fewer, the note's
  // whitespace folded, nothing else.
  const oldInvalid = (report) => (typeof report.version !== "string" || report.version.length > 40 ? "bad_version" : "");
  const oldPage = (id, version, note) =>
    `🐞 Problem report ${id} (Kazma ${version}): ${note.replace(/\s+/g, " ").trim().slice(0, 160) || "(no words)"}`;
  assert.equal(oldInvalid({ ...REPORT, version: FAKE_ALERT }), "");
  const text = oldPage("abcdefghjk", FAKE_ALERT, "Open https://evil.example now \u202e");
  assert.ok(text.split("\n").some((line) => line.startsWith("🔴 [Watch] Kazma is down")));
  assert.ok(text.includes("https://evil.example") && text.includes("\u202e"));
  assert.equal(invalid({ ...REPORT, version: FAKE_ALERT }), "bad_version");
});

test("anything that is not Kazma's report is refused, and nothing is kept", async () => {
  const e = env();
  for (const [body, error] of [
    ["not json", "not_json"],
    [[1, 2], "not_an_object"],
    [{ ...REPORT, kind: "other" }, "wrong_kind"],
    [{ ...REPORT, note: "x".repeat(4001) }, "bad_note"],
    [{ ...REPORT, version: 7 }, "bad_version"],
  ]) {
    const res = await handle(post(body), e, NOW);
    assert.equal(res.status, 400);
    assert.equal((await res.json()).error, error);
  }
  const big = await handle(post({ ...REPORT, pad: "x".repeat(REPORT_MAX) }), e, NOW);
  assert.equal(big.status, 413);
  assert.equal(await e.DB.prepare("SELECT COUNT(*) AS n FROM problem_reports").first("n"), 0);
});

test("one address is held to its hourly share, the inbox to its daily one", async () => {
  const e = env();
  const net = netWith();
  for (let i = 0; i < PER_SOURCE_HOUR; i += 1) {
    assert.equal((await handle(post(REPORT), e, NOW + i, { fetcher: net.fetcher })).status, 200);
  }
  const held = await handle(post(REPORT), e, NOW + 10, { fetcher: net.fetcher });
  assert.equal(held.status, 429);
  assert.equal((await held.json()).error, "too_many_from_you");
  // Another address is not held by the first's share.
  assert.equal((await handle(post(REPORT, "198.51.100.9"), e, NOW + 11, { fetcher: net.fetcher })).status, 200);
  // The daily cap, whatever the address.
  for (let i = 0; i < PER_DAY; i += 1) {
    await e.DB.prepare("INSERT INTO problem_reports (id, received_at, source, version, note, body) VALUES (?, ?, ?, ?, ?, ?)")
      .bind(`fill${i}xx`, NOW, `s${i}`, "0", "", "{}").run();
  }
  const full = await handle(post(REPORT, "192.0.2.1"), e, NOW + 12, { fetcher: net.fetcher });
  assert.equal(full.status, 429);
  assert.equal((await full.json()).error, "inbox_full_today");
});

test("the reports page is closed without Cloudflare Access, and old reports go", async () => {
  const e = env();
  const res = await handle(new Request("https://status.kazma.ai/reports"), e, NOW);
  assert.equal(res.status, 503);
  const one = await handle(new Request("https://status.kazma.ai/reports/abcdefghjk"), e, NOW);
  assert.equal(one.status, 503);
  await e.DB.prepare("INSERT INTO problem_reports (id, received_at, source, version, note, body) VALUES (?, ?, ?, ?, ?, ?)")
    .bind("oldoldold1", NOW - KEEP_S - 1, "s", "0", "", "{}").run();
  await pruneReports(e.DB, NOW);
  assert.equal(await e.DB.prepare("SELECT COUNT(*) AS n FROM problem_reports").first("n"), 0);
});

test("a report id draws every letter evenly: a byte past the last whole alphabet is drawn again", () => {
  const ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
  // 248 is 8 x 31: those bytes would give a..h one more chance each than the rest.
  let calls = 0;
  const id = newId((n) => {
    calls += 1;
    return calls === 1 ? new Uint8Array(n).fill(250) : new Uint8Array(n).fill(1);
  });
  assert.equal(id, "b".repeat(10));
  assert.equal(calls, 2);
  // Control: the old draw, b % 31, took 250 as the letter "c" (248..255 give a..h).
  assert.equal(ALPHABET[250 % ALPHABET.length], "c");
  // Every byte below the limit maps to each letter the same number of times.
  const counts = new Map();
  for (let b = 0; b < 248; b += 1) counts.set(ALPHABET[b % 31], (counts.get(ALPHABET[b % 31]) || 0) + 1);
  assert.deepEqual(new Set(counts.values()), new Set([8]));
  assert.match(newId(), /^[a-hjkmnp-z2-9]{10}$/);
});
