// Problem reports (Kazma REMAINING 3.1): POST /v1/report and GET /reports.
// Run: node --test test/ (Node 22.5+). In process, on the D1 stand-in.

import { test } from "node:test";
import assert from "node:assert/strict";

import { d1 } from "./d1_standin.mjs";
import { handle } from "../src/index.js";
import { KEEP_S, PER_DAY, PER_SOURCE_HOUR, REPORT_MAX, pruneReports } from "../src/reports.js";

const NOW = 1_800_000_000;

function env(extra = {}) {
  return { DB: d1(), PAIRING_TOKEN: "p".repeat(40), TELEGRAM_BOT_TOKEN: "123:abc", TELEGRAM_CHAT_ID: "42", ...extra };
}

function netWith() {
  const sent = [];
  return {
    sent,
    fetcher: async (url, init = {}) => {
      if (String(url).includes("api.telegram.org")) {
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
  assert.match(net.sent[0], /Problem report .* \(Kazma 0\.21\.2\): The PDF came out empty/);
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
