// Kazma Watch's half of Settings -> Alerts (the unified alerts plan, Stage 3):
// POST /v1/settings, /v1/test-page and /v1/leave, signed by the install's key,
// and the owner's zone on the cards. Run: node --test test/ (Node 22.5+).
// The handlers run in process against the D1 stand-in and a fake network.

import { test } from "node:test";
import assert from "node:assert/strict";

import { d1 } from "./d1_standin.mjs";
import { handle, runMinute } from "../src/index.js";
import { clock, pageText } from "../src/incidents.js";
import { sign } from "../src/verify.js";
import { testCard, TEST_GAP_S, validZone } from "../src/watch_settings.js";

const INSTALL = "a1b2c3d4e5f6a7b8";
const KEY = "pairing-key-0123456789abcdefghijklmnopqrstuv";
const PAIRING_TOKEN = "p".repeat(40);
const T0 = 1_800_000_000 - (1_800_000_000 % 3600); // on the hour (UTC)

function env(extra = {}) {
  return {
    DB: d1(),
    PULL_URL: "https://my.kazma.example",
    PAIRING_TOKEN,
    TELEGRAM_BOT_TOKEN: "123:abc",
    TELEGRAM_CHAT_ID: "42",
    ...extra,
  };
}

class Net {
  constructor() {
    this.live = 200;
    this.telegramUp = true;
    this.messages = [];
  }

  fetcher = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://my.kazma.example/health/")) {
      return new Response(this.live === 200 ? '{"status":"alive"}' : "", { status: this.live });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      if (!this.telegramUp) return new Response("down", { status: 502 });
      this.messages.push(JSON.parse(init.body).text);
      return Response.json({ ok: true });
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
}

async function post(e, path, body, headers = {}, now = T0, deps = {}) {
  const req = new Request(`https://status.kazma.example${path}`, {
    method: "POST", body: typeof body === "string" ? body : JSON.stringify(body), headers,
  });
  return handle(req, e, now, deps);
}

async function pair(e) {
  const res = await post(e, "/v1/pair", { install: INSTALL, key: KEY }, { Authorization: `Bearer ${PAIRING_TOKEN}` });
  assert.equal(res.status, 200);
}

/** A request signed the way Kazma's outside_watch._signed_post signs it. */
async function signed(e, path, fields = {}, { key = KEY, ts = T0, now = T0, deps = {} } = {}) {
  const raw = JSON.stringify({ v: 1, install: INSTALL, ...fields });
  return post(e, path, raw, {
    "X-Kazma-Install": INSTALL, "X-Kazma-Timestamp": String(ts), "X-Kazma-Signature": await sign(key, ts, raw),
  }, now, deps);
}

async function summary(e, now) {
  const req = new Request("https://status.kazma.example/v1/summary", {
    headers: { "X-Kazma-Install": INSTALL, "X-Kazma-Timestamp": String(now),
      "X-Kazma-Signature": await sign(KEY, now, "GET /v1/summary") } });
  return (await handle(req, e, now)).json();
}

// -- POST /v1/settings ------------------------------------------------------------

test("settings signed by the install are kept and read back in the summary", async () => {
  const e = env();
  await pair(e);
  const res = await signed(e, "/v1/settings", { timezone: "Asia/Kuwait", email_to: "owner@example.com" });
  assert.equal(res.status, 200);
  assert.deepEqual((await res.json()).settings, { timezone: "Asia/Kuwait", email_to: "owner@example.com" });
  const data = await summary(e, T0 + 10);
  assert.equal(data.settings.timezone, "Asia/Kuwait");
  assert.equal(data.settings.email_to, "owner@example.com");
});

test("settings need the install's signature: unsigned, forged and replayed are refused", async () => {
  const e = env();
  await pair(e);
  const raw = JSON.stringify({ v: 1, install: INSTALL, timezone: "Asia/Kuwait" });
  assert.equal((await post(e, "/v1/settings", raw)).status, 401, "no signature");
  // Control: a request signed by another key changes nothing.
  assert.equal((await signed(e, "/v1/settings", { timezone: "Asia/Kuwait" }, { key: "x".repeat(40) })).status, 401);
  // The pairing token is not a way in either.
  assert.equal((await post(e, "/v1/settings", raw, { Authorization: `Bearer ${PAIRING_TOKEN}` })).status, 401);
  assert.equal((await summary(e, T0)).settings.timezone, null);
  // Newer wins; a recorded older request cannot put the old zone back.
  assert.equal((await signed(e, "/v1/settings", { timezone: "Asia/Kuwait" }, { ts: T0 + 5, now: T0 + 5 })).status, 200);
  assert.equal((await signed(e, "/v1/settings", { timezone: "Europe/London" }, { ts: T0, now: T0 + 6 })).status, 409);
  assert.equal((await summary(e, T0 + 7)).settings.timezone, "Asia/Kuwait");
});

test("a zone or an address Kazma Watch cannot use is refused", async () => {
  const e = env();
  await pair(e);
  assert.equal((await signed(e, "/v1/settings", { timezone: "Mars/Olympus" })).status, 400);
  assert.equal((await signed(e, "/v1/settings", { email_to: "not an address" }, { ts: T0 + 1 })).status, 400);
  assert.ok(validZone("Asia/Kuwait") && validZone("UTC"));
  assert.ok(!validZone("Mars/Olympus") && !validZone("") && !validZone("x".repeat(80)));
});

// -- the owner's zone on the cards ----------------------------------------------------------

test("the cards' times are in the zone Kazma told, else UTC", async () => {
  // 1_800_000_000 is 2027-01-15 08:00 UTC; Kuwait is UTC+3.
  assert.equal(clock(T0), "2027-01-15 08:00 UTC");
  assert.equal(clock(T0, "Asia/Kuwait"), "2027-01-15 11:00 Asia/Kuwait");
  assert.equal(clock(T0, "Mars/Olympus"), "2027-01-15 08:00 UTC", "a zone that cannot be read is UTC");
  const incident = { cause: "machine_unreachable", detail: "HTTP 530", started_at: T0, causes: ["machine_unreachable"] };
  assert.match(pageText("open", incident, T0 + 600, { timeZone: "Asia/Kuwait" }), /Since 2027-01-15 11:00 Asia\/Kuwait \(10 min\)/);

  const run = async (zone) => {
    const e = env();
    const net = new Net();
    await pair(e);
    if (zone) assert.equal((await signed(e, "/v1/settings", { timezone: zone })).status, 200);
    net.live = 530;
    for (let i = 0; i < 4; i += 1) await runMinute(e, T0 + i * 60, { fetcher: net.fetcher });
    return net.messages;
  };
  const told = await run("Asia/Kuwait");
  assert.equal(told.length, 1);
  assert.match(told[0], /Since 2027-01-15 11:00 Asia\/Kuwait/);
  // Control: an install that told no zone gets UTC (Stage 1's card).
  const untold = await run(null);
  assert.match(untold[0], /Since 2027-01-15 08:00 UTC/);
});

test("an email page goes to the recipient Kazma told", async () => {
  const sent = [];
  const e = env({ TELEGRAM_BOT_TOKEN: "", EMAIL_FROM: "watch@kazma.example", EMAIL_TO: "default@example.com",
    EMAIL: { send: async (m) => { sent.push(m); } } });
  const net = new Net();
  await pair(e);
  assert.equal((await signed(e, "/v1/settings", { email_to: "owner@example.com" })).status, 200);
  net.live = 530;
  for (let i = 0; i < 4; i += 1) {
    await runMinute(e, T0 + i * 60, { fetcher: net.fetcher });
  }
  assert.equal(sent.length, 1);
  assert.equal(sent[0].to, "owner@example.com");
  assert.deepEqual(sent[0].from, { email: "watch@kazma.example", name: "Kazma Watch" });
});

// -- POST /v1/test-page ------------------------------------------------------------

test("a test page goes through every channel and says which took it", async () => {
  const sentMail = [];
  const e = env({ EMAIL_FROM: "watch@kazma.example", EMAIL: { send: async (m) => { sentMail.push(m); } } });
  const net = new Net();
  await pair(e);
  assert.equal((await signed(e, "/v1/settings", { timezone: "Asia/Kuwait", email_to: "owner@example.com" })).status, 200);
  const deps = { fetcher: net.fetcher };
  const res = await signed(e, "/v1/test-page", {}, { ts: T0 + 1, now: T0 + 1, deps });
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.deepEqual(data, { ok: true, channels: { telegram: "sent", email: "sent" }, reasons: {} });
  assert.equal(net.messages.length, 1);
  assert.equal(net.messages[0], testCard(T0 + 1, "Asia/Kuwait"));
  assert.match(net.messages[0], /^🔵 \[Watch\] Test page\n.*11:00 Asia\/Kuwait/);
  assert.equal(sentMail[0].to, "owner@example.com");
});

test("a test page no channel took says so, and a channel not set up is off", async () => {
  const e = env();
  const net = new Net();
  net.telegramUp = false;
  await pair(e);
  const res = await signed(e, "/v1/test-page", {}, { deps: { fetcher: net.fetcher } });
  assert.deepEqual(await res.json(), { ok: false, channels: { telegram: "failed", email: "off" }, reasons: {} });
});

test("a refused email says why, with Email Service's own code", async () => {
  const refuse = (code) => async () => { throw Object.assign(new Error("refused"), { code }); };
  const run = async (send, zoneTs) => {
    const e = env({ TELEGRAM_BOT_TOKEN: "", EMAIL_FROM: "watch@kazma.example", EMAIL: { send } });
    await pair(e);
    assert.equal((await signed(e, "/v1/settings", { email_to: "owner@example.com" }, { ts: zoneTs })).status, 200);
    return (await signed(e, "/v1/test-page", {}, { ts: zoneTs + 1, now: zoneTs + 1 })).json();
  };
  assert.deepEqual(await run(refuse("E_SENDER_NOT_VERIFIED"), T0),
    { ok: false, channels: { telegram: "off", email: "failed" }, reasons: { email: "E_SENDER_NOT_VERIFIED" } });
  // Anything that is not one of Email Service's codes is not repeated to Kazma.
  assert.deepEqual((await run(refuse("<script>"), T0)).reasons, { email: "E_SEND_FAILED" });
  assert.deepEqual((await run(async () => { throw new TypeError("boom"); }, T0)).reasons, { email: "E_SEND_FAILED" });
});

test("no recipient means email is off, not an email to nobody", async () => {
  const sent = [];
  const e = env({ TELEGRAM_BOT_TOKEN: "", EMAIL_FROM: "watch@kazma.example", EMAIL: { send: async (m) => { sent.push(m); } } });
  await pair(e);
  const res = await signed(e, "/v1/test-page", {});
  assert.deepEqual(await res.json(), { ok: false, channels: { telegram: "off", email: "off" }, reasons: {} });
  assert.equal(sent.length, 0);
});

test("test pages need the signature and come at most every 20 seconds", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  const deps = { fetcher: net.fetcher };
  assert.equal((await post(e, "/v1/test-page", JSON.stringify({ v: 1, install: INSTALL }), {}, T0, deps)).status, 401);
  assert.equal((await signed(e, "/v1/test-page", {}, { key: "z".repeat(40), deps })).status, 401);
  assert.equal(net.messages.length, 0, "an unsigned request sends nothing");
  assert.equal((await signed(e, "/v1/test-page", {}, { ts: T0, now: T0, deps })).status, 200);
  // The same request again (a replay) and one 5 s later are refused.
  assert.equal((await signed(e, "/v1/test-page", {}, { ts: T0, now: T0 + 1, deps })).status, 429);
  assert.equal((await signed(e, "/v1/test-page", {}, { ts: T0 + 5, now: T0 + 5, deps })).status, 429);
  assert.equal((await signed(e, "/v1/test-page", {}, { ts: T0 + TEST_GAP_S, now: T0 + TEST_GAP_S, deps })).status, 200);
  assert.equal(net.messages.length, 2);
});

// -- POST /v1/leave ------------------------------------------------------------------

test("an install leaves with its own signature, and forget removes what was kept", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  await signed(e, "/v1/settings", { timezone: "Asia/Kuwait" });
  net.live = 530;
  for (let i = 0; i < 4; i += 1) await runMinute(e, T0 + i * 60, { fetcher: net.fetcher });
  // Control: unsigned, or signed by another key, changes nothing.
  assert.equal((await post(e, "/v1/leave", JSON.stringify({ v: 1, install: INSTALL }), {}, T0 + 300)).status, 401);
  assert.equal((await signed(e, "/v1/leave", {}, { key: "q".repeat(40), ts: T0 + 300, now: T0 + 300 })).status, 401);
  const count = (table) => e.DB.raw.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n;
  assert.equal(count("installs"), 1);
  const res = await signed(e, "/v1/leave", { forget: true }, { ts: T0 + 300, now: T0 + 300 });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, unpaired: INSTALL, forgotten: true });
  for (const table of ["installs", "heartbeats", "watch_state", "install_settings", "incidents", "readings"]) {
    assert.equal(count(table), 0, table);
  }
  // Gone: a second leave meets an install Kazma Watch does not know.
  const again = await signed(e, "/v1/leave", {}, { ts: T0 + 301, now: T0 + 301 });
  assert.equal(again.status, 401);
  assert.equal((await again.json()).error, "unknown_install");
});

// -- where Stages 2, 3 and 4 meet ----------------------------------------------------

const CF_SECRET = "cf-webhook-secret-0123456789abcdef";

async function cloudflareNotice(e, net, now) {
  const body = {
    name: "Tunnel health", text: "Tunnel kazma is down", ts: now, alert_type: "tunnel_health_event",
    account_id: "acc", policy_id: "pol",
    data: { tunnel_id: "t-1", tunnel_name: "kazma", new_status: "TUNNEL_STATUS_TYPE_DOWN" },
  };
  const req = new Request("https://status.kazma.example/v1/cloudflare", {
    method: "POST", headers: { "cf-webhook-auth": CF_SECRET }, body: JSON.stringify(body) });
  return handle(req, e, now, { fetcher: net.fetcher });
}

test("a Cloudflare card is in the zone Kazma told too, and a held incident names who paged it", async () => {
  const run = async (zone) => {
    const e = env({ CF_WEBHOOK_SECRET: CF_SECRET });
    const net = new Net();
    await pair(e);
    if (zone) assert.equal((await signed(e, "/v1/settings", { timezone: zone })).status, 200);
    assert.equal((await cloudflareNotice(e, net, T0)).status, 200);
    net.live = 530;
    for (let i = 1; i <= 6; i += 1) await runMinute(e, T0 + i * 60, { fetcher: net.fetcher });
    return { e, messages: net.messages };
  };
  const told = await run("Asia/Kuwait");
  assert.equal(told.messages.length, 1, "Kazma Watch's own incident is held by the notice: one page");
  assert.match(told.messages[0], /^🔴 \[Cloudflare\] /);
  assert.match(told.messages[0], /Since 2027-01-15 11:00 Asia\/Kuwait/);
  // The summary says who paged the held incident, so Kazma's Recent alerts
  // never lists it as a page of Kazma Watch's that failed.
  const s = await summary(told.e, T0 + 7 * 60);
  assert.equal(s.incidents_7d.length, 1);
  assert.equal(s.incidents_7d[0].paged_at, null);
  assert.equal(s.incidents_7d[0].held_by, "cloudflare.tunnel_down");
  // Control: an install that told no zone gets the card in UTC.
  const untold = await run(null);
  assert.match(untold.messages[0], /Since 2027-01-15 08:00 UTC/);
});
