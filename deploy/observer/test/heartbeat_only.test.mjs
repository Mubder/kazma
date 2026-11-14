// A watch with no address to look at (REMAINING 3.2, `kazma observer setup`
// for an install with no public address): it judges by the heartbeats alone,
// and their silence is a page of its own. Run: node --test test/

import { test } from "node:test";
import assert from "node:assert/strict";

import { d1 } from "./d1_standin.mjs";
import { handle, runMinute } from "../src/index.js";
import { diagnose } from "../src/diagnose.js";
import { signV2 } from "../src/verify.js";

const INSTALL = "a1b2c3d4e5f6a7b8";
const KEY = "pairing-key-0123456789abcdefghijklmnopqrstuv";
const PAIRING_TOKEN = "p".repeat(40);
const T0 = 1_800_000_000 - (1_800_000_000 % 3600);
const NO_URL = { error: "no_url" };

function env() {
  return { DB: d1(), PULL_URL: "", PAIRING_TOKEN, TELEGRAM_BOT_TOKEN: "123:abc", TELEGRAM_CHAT_ID: "42" };
}

function body(extra = {}) {
  return {
    v: 1, install: INSTALL, sent_at: 0, seq: 1,
    guard: { state: "supervising", uptime_s: 100, code: "abcdef012345" },
    kazma: { state: "ready", ready: true, why: [], uptime_s: 90, build: "0123456789ab" },
    maintenance: false, database: { state: "answering", confirmed: false }, docker: "up",
    cloudflared: "running", disk: { free_pct: 40, level: "ok" }, power: null, ...extra,
  };
}

function net() {
  const sent = [];
  const probes = [];
  return {
    sent, probes,
    fetcher: async (url, init = {}) => {
      const u = String(url);
      if (u.startsWith("https://api.telegram.org/")) {
        sent.push(JSON.parse(init.body).text);
        return Response.json({ ok: true });
      }
      probes.push(u);
      throw new Error(`unexpected fetch: ${u}`);
    },
  };
}

async function post(e, path, raw, headers, now) {
  return handle(new Request(`https://kazma-watch.example.workers.dev${path}`,
    { method: "POST", body: raw, headers }), e, now);
}

async function beat(e, now, extra = {}) {
  const raw = JSON.stringify(body(extra));
  const signature = await signV2(KEY, now, "POST", "/v1/heartbeat", raw);
  const res = await post(e, "/v1/heartbeat", raw,
    { "X-Kazma-Install": INSTALL, "X-Kazma-Timestamp": String(now), "X-Kazma-Signature": signature }, now);
  assert.equal(res.status, 200);
}

test("with no address, the heartbeat alone says how Kazma is", () => {
  const now = T0;
  const fresh = (extra) => ({ received_at: now - 30, body: body(extra) });
  assert.deepEqual(diagnose({ live: NO_URL, ready: null, heartbeat: fresh(), now }), { condition: "up" });
  assert.equal(diagnose({ live: NO_URL, ready: null, heartbeat: fresh({ kazma: { state: "starting", ready: false, why: [] } }), now }).condition,
    "kazma_restarting");
  assert.equal(diagnose({ live: NO_URL, ready: null,
    heartbeat: fresh({ kazma: { state: "not_ready", ready: false, why: ["database"] } }), now }).condition, "dependency_down");
  assert.equal(diagnose({ live: NO_URL, ready: null, heartbeat: fresh({ disk: { free_pct: 2, level: "critical" } }), now }).condition,
    "disk_critical");
  assert.equal(diagnose({ live: NO_URL, ready: null, heartbeat: null, now }).condition, "heartbeat_silent");
  // The negative control: the same answer read as an address that failed --
  // what the watch concluded before -- blames the tunnel for a Kazma that is up.
  assert.equal(diagnose({ live: { error: "fetch_failed" }, ready: null, heartbeat: fresh(), now }).condition,
    "tunnel_down");
});

test("with no address and no install paired, nothing is watched and nothing is fetched", async () => {
  const e = env();
  const n = net();
  const run = await runMinute(e, T0, { fetcher: n.fetcher });
  assert.deepEqual(run.seen, {});
  assert.deepEqual(n.probes, []);
});

test("a paired install with no address: quiet while it beats, one page when it stops, one when it is back", async () => {
  const e = env();
  const n = net();
  const res = await post(e, "/v1/pair", JSON.stringify({ install: INSTALL, key: KEY }),
    { Authorization: `Bearer ${PAIRING_TOKEN}` }, T0);
  assert.equal(res.status, 200);
  for (let i = 0; i < 5; i += 1) {
    await beat(e, T0 + i * 60);
    await runMinute(e, T0 + i * 60, { fetcher: n.fetcher });
  }
  assert.deepEqual(n.sent, []);
  assert.deepEqual(n.probes, [], "no address: nothing is fetched but Telegram");
  // The machine goes quiet: no heartbeat for long enough to open an incident.
  for (let i = 5; i < 20; i += 1) await runMinute(e, T0 + i * 60, { fetcher: n.fetcher });
  assert.equal(n.sent.length, 1, n.sent.join("\n---\n"));
  assert.match(n.sent[0], /hears nothing from Kazma/);
  // It beats again: one recovery.
  await beat(e, T0 + 20 * 60);
  await runMinute(e, T0 + 20 * 60, { fetcher: n.fetcher });
  assert.equal(n.sent.length, 2);
  assert.match(n.sent[1], /back/i);
});
