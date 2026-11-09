// Replays a scenario built by tests/test_one_page_per_problem.py: the guard's
// real heartbeats (built by heartbeat_body, signed by sign_heartbeat), one a
// minute, through the Worker's own handlers on the D1 stand-in, with the
// pull probe's answers the scenario gives. Prints what Kazma Watch paged.
//
//   node test/replay_scenario.mjs <scenario.json>
//
// scenario: {install, key, minutes: [{now, live, ready,
//            heartbeat: {body, ts, signature} | null}]}
// prints:   {"messages": [{"at", "text"}], "beats": [<status>, ...]}

import { readFileSync } from "node:fs";

import { d1 } from "./d1_standin.mjs";
import { handle, runMinute } from "../src/index.js";

const scenario = JSON.parse(readFileSync(process.argv[2], "utf8"));
const env = {
  DB: d1(),
  PULL_URL: "https://kazma.example",
  PAIRING_TOKEN: "p".repeat(40),
  TELEGRAM_BOT_TOKEN: "1:replay",
  TELEGRAM_CHAT_ID: "1",
};
const messages = [];
let live = 200;
let ready = 200;
let current = 0;

async function fetcher(url, init = {}) {
  const u = String(url);
  if (u.startsWith("https://kazma.example/health/")) {
    return new Response("{}", { status: u.endsWith("/live") ? live : ready });
  }
  if (u.startsWith("https://api.telegram.org/")) {
    messages.push({ at: current, text: JSON.parse(init.body).text });
    return Response.json({ ok: true });
  }
  throw new Error(`unexpected fetch: ${u}`);
}

function post(path, body, headers, now) {
  return handle(new Request(`https://status.kazma.example${path}`, { method: "POST", body, headers }), env, now);
}

const first = scenario.minutes[0].now;
const paired = await post("/v1/pair", JSON.stringify({ install: scenario.install, key: scenario.key }),
  { Authorization: `Bearer ${"p".repeat(40)}` }, first);
if (paired.status !== 200) throw new Error(`pairing refused: ${paired.status}`);

const beats = [];
for (const m of scenario.minutes) {
  current = m.now;
  live = m.live;
  ready = m.ready;
  if (m.heartbeat) {
    const res = await post("/v1/heartbeat", m.heartbeat.body, {
      "X-Kazma-Install": scenario.install,
      "X-Kazma-Timestamp": String(m.heartbeat.ts),
      "X-Kazma-Signature": m.heartbeat.signature,
    }, m.now);
    beats.push(res.status);
  }
  await runMinute(env, m.now, { fetcher });
}
process.stdout.write(JSON.stringify({ messages, beats }));
