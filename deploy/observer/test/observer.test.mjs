// The outside observer's tests (resilience plan B1). Run: node --test test/
// (Node 22.5+). The Worker's handlers run in process against a D1 stand-in
// over node:sqlite (the real schema.sql) and a fake network: nothing here
// reaches Cloudflare, Telegram or the install.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { d1 } from "./d1_standin.mjs";
import { handle, runMinute, scheduledRun, SITE } from "../src/index.js";
import { classifyLive, diagnose, sanitize } from "../src/diagnose.js";
import { duration, OPEN_AFTER_S } from "../src/incidents.js";
import { forgetCerts } from "../src/access.js";
import { sign } from "../src/verify.js";

const here = dirname(fileURLToPath(import.meta.url));
const VECTOR = JSON.parse(readFileSync(
  join(here, "..", "..", "..", "tests", "fixtures", "observer", "heartbeat_vector.json"), "utf8"));

const INSTALL = "a1b2c3d4e5f6a7b8";
const KEY = "pairing-key-0123456789abcdefghijklmnopqrstuv";
const PAIRING_TOKEN = "p".repeat(40);
const T0 = 1_800_000_000 - (1_800_000_000 % 3600);   // on the hour

function env(extra = {}) {
  return {
    DB: d1(),
    PULL_URL: "https://my.kazma.example",
    PAIRING_TOKEN,
    TELEGRAM_BOT_TOKEN: "123:abc",
    TELEGRAM_CHAT_ID: "42",
    ACCESS_CLIENT_ID: "id.access",
    ACCESS_CLIENT_SECRET: "secret",
    HEALTHCHECKS_URL: "https://hc-ping.example/uuid",
    ...extra,
  };
}

/** The network: the install's site, Telegram, Access's certs, Healthchecks. */
class Net {
  constructor() {
    this.live = 200;
    this.ready = 200;
    this.liveBody = "";
    this.telegramUp = true;
    this.messages = [];
    this.pings = [];
    this.probes = [];
    this.jwks = { keys: [] };
  }

  fetcher = async (url, init = {}) => {
    const u = String(url);
    if (u.startsWith("https://my.kazma.example/health/")) {
      this.probes.push({ url: u, headers: init.headers });
      const status = u.endsWith("/live") ? this.live : this.ready;
      if (status === "throw") throw new TypeError("network down");
      return new Response(status === 200 ? '{"status":"alive"}' : this.liveBody, { status });
    }
    if (u.startsWith("https://api.telegram.org/")) {
      const body = JSON.parse(init.body);
      if (!this.telegramUp) return new Response("down", { status: 502 });
      this.messages.push(body.text);
      return Response.json({ ok: true });
    }
    if (u.includes("/cdn-cgi/access/certs")) return Response.json(this.jwks);
    if (u.startsWith("https://hc-ping.example/")) {
      this.pings.push(u);
      return new Response("OK");
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
}

function heartbeatBody(extra = {}) {
  return {
    v: 1,
    install: INSTALL,
    sent_at: 0,
    seq: 1,
    guard: { state: "supervising", uptime_s: 100, code: "abcdef012345" },
    kazma: { state: "ready", ready: true, why: [], uptime_s: 90, build: "0123456789ab" },
    maintenance: false,
    database: { state: "answering", confirmed: false },
    docker: "up",
    cloudflared: "running",
    disk: { free_pct: 40, level: "ok" },
    power: null,
    ...extra,
  };
}

async function post(e, path, body, headers = {}, now = T0) {
  const req = new Request(`https://status.kazma.example${path}`, {
    method: "POST", body: typeof body === "string" ? body : JSON.stringify(body), headers,
  });
  return handle(req, e, now);
}

async function pair(e, install = INSTALL, key = KEY) {
  const res = await post(e, "/v1/pair", { install, key }, { Authorization: `Bearer ${PAIRING_TOKEN}` });
  assert.equal(res.status, 200);
}

async function beat(e, now, body = heartbeatBody(), { key = KEY, ts = now, install = INSTALL } = {}) {
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  const signature = await sign(key, ts, raw);
  return post(e, "/v1/heartbeat", raw, {
    "X-Kazma-Install": install, "X-Kazma-Timestamp": String(ts), "X-Kazma-Signature": signature,
  }, now);
}

// -- signatures -----------------------------------------------------------------

test("the shared vector verifies, and a changed body or timestamp does not", async () => {
  assert.equal(await sign(VECTOR.key, VECTOR.timestamp, VECTOR.body), VECTOR.signature);
  assert.notEqual(await sign(VECTOR.key, VECTOR.timestamp + 1, VECTOR.body), VECTOR.signature);
  assert.notEqual(await sign(VECTOR.key, VECTOR.timestamp, '{"v":2}'), VECTOR.signature);
});

// -- the heartbeat endpoint -------------------------------------------------------

test("a signed heartbeat is kept, cut to the allowlist", async () => {
  const e = env();
  await pair(e);
  const planted = heartbeatBody({ chat: "CANARY chat text", kazma: {
    state: "not_ready", ready: false, why: ["database", "C:/secret path"], build: "../../etc" } });
  const res = await beat(e, T0, planted);
  assert.equal(res.status, 200);
  const row = e.DB.raw.prepare("SELECT body FROM heartbeats WHERE install = ?").get(INSTALL);
  assert.ok(!row.body.includes("CANARY"));
  assert.ok(!row.body.includes("secret"));
  const kept = JSON.parse(row.body);
  assert.deepEqual(kept.kazma.why, ["database"]);
  assert.equal(kept.kazma.build, null);
  // Control: the body as sent holds what the allowlist drops.
  assert.ok(JSON.stringify(planted).includes("CANARY"));
});

test("a replayed, stale, forged, unknown or oversized heartbeat is refused", async () => {
  const e = env();
  await pair(e);
  assert.equal((await beat(e, T0)).status, 200);
  assert.equal((await beat(e, T0)).status, 409, "the same timestamp again");
  assert.equal((await beat(e, T0 + 60, heartbeatBody(), { ts: T0 - 400 })).status, 401, "stale");
  assert.equal((await beat(e, T0 + 60, heartbeatBody(), { key: "x".repeat(40) })).status, 401, "forged");
  assert.equal((await beat(e, T0 + 60, heartbeatBody(), { install: "ffffffffffffffff" })).status, 401);
  assert.equal((await beat(e, T0 + 60, heartbeatBody({ install: "0000000000000000" }))).status, 400);
  const big = JSON.stringify(heartbeatBody({ pad: "x".repeat(5000) }));
  assert.equal((await beat(e, T0 + 120, big)).status, 413);
  assert.equal((await beat(e, T0 + 180)).status, 200, "a newer one is still taken");
});

test("pairing needs the pairing token, and a forget removes what was kept", async () => {
  const e = env();
  let res = await post(e, "/v1/pair", { install: INSTALL, key: KEY }, { Authorization: "Bearer wrong" });
  assert.equal(res.status, 401);
  res = await post(env({ PAIRING_TOKEN: "" }), "/v1/pair", { install: INSTALL, key: KEY },
    { Authorization: "Bearer " });
  assert.equal(res.status, 503);
  res = await post(e, "/v1/pair", { install: INSTALL, key: "short" }, { Authorization: `Bearer ${PAIRING_TOKEN}` });
  assert.equal(res.status, 400);
  await pair(e);
  await beat(e, T0);
  await runMinute(e, T0, { fetcher: new Net().fetcher });
  res = await post(e, "/v1/unpair", { install: INSTALL, forget: true }, { Authorization: `Bearer ${PAIRING_TOKEN}` });
  assert.equal(res.status, 200);
  for (const table of ["installs", "heartbeats", "watch_state", "readings", "hourly", "incidents"]) {
    assert.equal(e.DB.raw.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n, 0, table);
  }
});

// -- the diagnosis table ------------------------------------------------------------

const NOW = T0 + 600;
const fresh = (extra = {}) => ({ received_at: NOW - 30, body: sanitize(heartbeatBody(extra)) });
const missing = { received_at: NOW - 600, body: sanitize(heartbeatBody()) };

test("each row of the diagnosis table", () => {
  const cases = [
    [{ status: 200 }, fresh(), "up"],
    [{ status: 530 }, null, "machine_unreachable"],
    [{ status: 502, hint: "error code: 1033" }, missing, "machine_unreachable"],
    [{ error: "timeout" }, null, "machine_unreachable"],
    [{ status: 530 }, fresh({ cloudflared: "not_running" }), "tunnel_down"],
    [{ status: 502 }, fresh({ kazma: { state: "down", why: [] } }), "kazma_restarting"],
    [{ status: 504 }, fresh({ kazma: { state: "starting", why: [] } }), "kazma_restarting"],
    [{ status: 200 }, fresh({ kazma: { state: "not_ready", why: ["database"] } }), "dependency_down"],
    [{ status: 200 }, null, "guard_gone"],
    [{ status: 502 }, fresh({ maintenance: true }), "maintenance"],
    [{ status: 200 }, fresh({ disk: { free_pct: 3, level: "critical" } }), "disk_critical"],
    [{ status: 403 }, fresh(), "observer_refused"],
    [{ status: 302, hint: "https://team.cloudflareaccess.com/cdn-cgi/access/login" }, fresh(), "observer_refused"],
  ];
  for (const [live, heartbeat, want] of cases) {
    const got = diagnose({ live, ready: null, heartbeat, now: NOW });
    assert.equal(got.condition, want, `${JSON.stringify(live)} -> ${got.condition}`);
  }
  const dep = diagnose({ live: { status: 200 }, ready: null, now: NOW,
    heartbeat: fresh({ kazma: { state: "not_ready", why: ["database"] }, docker: "down" }) });
  assert.equal(dep.detail, "database, docker");
  // A fresh "ready 503" from the pull probe says the same as the heartbeat.
  assert.equal(diagnose({ live: { status: 200 }, ready: { status: 503, at: NOW - 60 },
    heartbeat: fresh(), now: NOW }).condition, "dependency_down");
});

test("without the heartbeat the probe alone cannot tell the machine from the tunnel", () => {
  // The negative control for the heartbeat: the same outside answers, the
  // site alone, read the same.
  const machine = diagnose({ live: { status: 530 }, heartbeat: null, now: NOW, expectHeartbeat: false });
  const tunnel = diagnose({ live: { status: 530 }, heartbeat: null, now: NOW, expectHeartbeat: false });
  assert.equal(machine.condition, "unreachable");
  assert.equal(tunnel.condition, "unreachable");
  assert.equal(classifyLive({ status: 530 }), "tunnel");
  assert.equal(diagnose({ live: { status: 502 }, now: NOW, expectHeartbeat: false }).condition, "kazma_down");
});

// -- incidents and pages ----------------------------------------------------------

async function minutes(e, net, from, count, { beatEach = true, body = heartbeatBody } = {}) {
  const pages = [];
  for (let i = 0; i < count; i += 1) {
    const now = from + i * 60;
    if (beatEach) await beat(e, now, body(now));
    const run = await runMinute(e, now, { fetcher: net.fetcher });
    pages.push(...Object.values(run.seen).flatMap((s) => s.pages));
  }
  return pages;
}

test("one page after three failed minutes, an hourly reminder, one recovery page", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  assert.deepEqual(await minutes(e, net, T0, 5), []);
  // The machine goes off: no heartbeat, the tunnel's 530.
  net.live = 530;
  const down = T0 + 300;
  const first = await minutes(e, net, down, 2, { beatEach: false });
  assert.deepEqual(first, [], "two minutes are not an incident");
  const opened = await minutes(e, net, down + 120, 1, { beatEach: false });
  assert.equal(opened.length, 1);
  assert.equal(opened[0].kind, "open");
  assert.match(opened[0].text, /machine is not reachable/);
  assert.equal(net.messages.length, 1);
  // Quiet for the rest of the hour, then one reminder.
  const hour = await minutes(e, net, down + 180, 60, { beatEach: false });
  assert.equal(hour.filter((p) => p.kind === "remind").length, 1);
  assert.equal(hour.length, 1);
  // Back: one recovery page with how long it lasted.
  net.live = 200;
  const back = down + 180 + 3600;
  const recovered = await minutes(e, net, back, 3);
  assert.equal(recovered.length, 1);
  assert.equal(recovered[0].kind, "recovered");
  assert.match(recovered[0].text, /lasted 1 h 3 min/);
  const inc = e.DB.raw.prepare("SELECT * FROM incidents").all();
  assert.equal(inc.length, 1);
  assert.equal(inc[0].cause, "machine_unreachable");
  assert.ok(inc[0].ended_at && inc[0].recovery_paged_at);
});

test("planned maintenance pages nothing", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  net.live = 502;
  const pages = await minutes(e, net, T0, 30, {
    body: () => heartbeatBody({ maintenance: true, kazma: { state: "down", why: [] } }) });
  assert.deepEqual(pages, []);
  // Control: the same outage without the maintenance flag pages.
  const e2 = env();
  await pair(e2);
  const pages2 = await minutes(e2, net, T0, 30, {
    body: () => heartbeatBody({ kazma: { state: "down", why: [] } }) });
  assert.equal(pages2.length, 1);
  assert.match(pages2[0].text, /restarting it/);
});

test("a page no channel took is tried again the next minute", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  net.live = 530;
  net.telegramUp = false;
  const failed = await minutes(e, net, T0, 4, { beatEach: false });
  assert.ok(failed.length >= 1 && failed.every((p) => !p.ok));
  net.telegramUp = true;
  const retried = await minutes(e, net, T0 + 240, 1, { beatEach: false });
  assert.equal(retried.length, 1);
  assert.equal(retried[0].ok, true);
  assert.equal(net.messages.length, 1);
});

test("the email channel carries a page when configured", async () => {
  const sent = [];
  const e = env({ TELEGRAM_BOT_TOKEN: "", EMAIL_FROM: "watch@kazma.example", EMAIL_TO: "owner@example.com",
    EMAIL: { send: async (m) => { sent.push(m); } } });
  const net = new Net();
  net.live = 530;
  await pair(e);
  for (let i = 0; i < 4; i += 1) {
    await runMinute(e, T0 + i * 60, { fetcher: net.fetcher,
      makeMessage: async (from, to, raw) => ({ from, to, raw }) });
  }
  assert.equal(sent.length, 1);
  assert.match(sent[0].raw, /Subject: .*not reachable/);
  assert.equal(sent[0].to, "owner@example.com");
});

test("the pull probe sends the Access service token, and readiness every five minutes", async () => {
  const e = env();
  const net = new Net();
  await minutes(e, net, T0, 10, { beatEach: false });
  const live = net.probes.filter((p) => p.url.endsWith("/live"));
  const ready = net.probes.filter((p) => p.url.endsWith("/ready"));
  assert.equal(live.length, 10);
  assert.equal(ready.length, 2);
  assert.equal(live[0].headers["CF-Access-Client-Id"], "id.access");
});

test("with nothing paired the site alone is watched", async () => {
  const e = env();
  const net = new Net();
  net.live = 530;
  const pages = await minutes(e, net, T0, 4, { beatEach: false });
  assert.equal(pages.length, 1);
  assert.match(pages[0].text, /not reachable/);
  assert.equal(e.DB.raw.prepare("SELECT install FROM watch_state").get().install, SITE);
});

test("each run pings Healthchecks, and a failed run says so", async () => {
  const net = new Net();
  assert.equal(await scheduledRun(env(), T0, { fetcher: net.fetcher }), true);
  const broken = env();
  broken.DB = { prepare() { throw new Error("D1 down"); }, batch() { throw new Error("D1 down"); } };
  assert.equal(await scheduledRun(broken, T0, { fetcher: net.fetcher }), false);
  assert.deepEqual(net.pings, ["https://hc-ping.example/uuid", "https://hc-ping.example/uuid/fail"]);
});

test("readings are kept 30 days, hourly summaries 400", async () => {
  const e = env();
  const net = new Net();
  await runMinute(e, T0, { fetcher: net.fetcher });
  const later = T0 + 31 * 86400;
  const pruneMinute = later - (later % 3600) + 7 * 60;
  await runMinute(e, pruneMinute, { fetcher: net.fetcher });
  const ats = e.DB.raw.prepare("SELECT at FROM readings").all().map((r) => r.at);
  assert.deepEqual(ats, [pruneMinute]);
  assert.equal(e.DB.raw.prepare("SELECT COUNT(*) AS n FROM hourly").get().n, 2);
});

test("durations read as people say them", () => {
  assert.equal(duration(30), "1 min");
  assert.equal(duration(14 * 60), "14 min");
  assert.equal(duration(3 * 3600 + 5 * 60), "3 h 5 min");
  assert.equal(duration(2 * 3600), "2 h");
  assert.ok(OPEN_AFTER_S > 60 && OPEN_AFTER_S <= 120);
});

// -- the install's summary -----------------------------------------------------------

test("the summary answers only the install's own signed request", async () => {
  const e = env();
  const net = new Net();
  await pair(e);
  net.live = 530;
  await minutes(e, net, T0, 5, { beatEach: false });
  const ts = T0 + 300;
  const get = async (key) => handle(new Request("https://status.kazma.example/v1/summary", {
    headers: { "X-Kazma-Install": INSTALL, "X-Kazma-Timestamp": String(ts),
      "X-Kazma-Signature": await sign(key, ts, "GET /v1/summary") } }), e, ts);
  const res = await get(KEY);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.condition, "machine_unreachable");
  assert.equal(data.incidents_7d.length, 1);
  assert.equal(data.incidents_7d[0].cause, "machine_unreachable");
  assert.equal((await get("y".repeat(40))).status, 401);
});

// -- the status page --------------------------------------------------------------

function b64url(bytes) {
  return Buffer.from(bytes).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function accessToken(claims) {
  const pair_ = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = { ...(await crypto.subtle.exportKey("jwk", pair_.publicKey)), kid: "k1" };
  const head = b64url(Buffer.from(JSON.stringify({ alg: "RS256", kid: "k1", typ: "JWT" })));
  const body = b64url(Buffer.from(JSON.stringify(claims)));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair_.privateKey,
    new TextEncoder().encode(`${head}.${body}`));
  return { token: `${head}.${body}.${b64url(new Uint8Array(sig))}`, jwk };
}

test("the status page is the owner's only", async () => {
  forgetCerts();
  const team = "team.cloudflareaccess.example";
  const aud = "aud-tag-1";
  const net = new Net();
  const get = (e, token) => handle(new Request("https://status.kazma.example/", {
    headers: token ? { "Cf-Access-Jwt-Assertion": token } : {} }), e, T0, { fetcher: net.fetcher });

  assert.equal((await get(env(), null)).status, 503, "Access not configured: closed");
  const e = env({ ACCESS_TEAM_DOMAIN: team, ACCESS_AUD: aud });
  assert.equal((await get(e, null)).status, 403, "no token");

  const claims = { aud: [aud], iss: `https://${team}`, exp: T0 + 600, email: "owner@example.com" };
  const good = await accessToken(claims);
  net.jwks = { keys: [good.jwk] };
  const res = await get(e, good.token);
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.match(html, /Kazma Watch/);
  assert.match(res.headers.get("Content-Security-Policy"), /default-src 'none'/);

  forgetCerts();
  const other = await accessToken({ ...claims, aud: ["another-app"] });
  net.jwks = { keys: [other.jwk] };
  assert.equal((await get(e, other.token)).status, 403, "another application's token");
  forgetCerts();
  const expired = await accessToken({ ...claims, exp: T0 - 10 });
  net.jwks = { keys: [expired.jwk] };
  assert.equal((await get(e, expired.token)).status, 403, "expired");
  forgetCerts();
  const forged = await accessToken(claims);
  net.jwks = { keys: [good.jwk] };   // signed by a key Access never published
  assert.equal((await get(e, forged.token)).status, 403, "forged");
});

test("unknown paths are 404", async () => {
  const res = await handle(new Request("https://status.kazma.example/admin"), env(), T0);
  assert.equal(res.status, 404);
});
