// The status page (the owner's only, behind Cloudflare Access): the
// condition now, the latest heartbeat, 90 days of uptime bars, the
// incidents with their causes and lengths, and the last day's response
// times. Plain HTML, no script, no outside resources.

import { titleOf } from "./diagnose.js";
import { duration } from "./incidents.js";

const DAY = 86400;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function clock(epoch) {
  return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function uptimePct(days) {
  const minutes = days.reduce((n, d) => n + (d.minutes || 0), 0);
  const up = days.reduce((n, d) => n + (d.up || 0), 0);
  return minutes ? Math.round((1000 * up) / minutes) / 10 : null;
}

function bar(day) {
  const pct = day.minutes ? (100 * day.up) / day.minutes : null;
  const cls = pct === null ? "none" : pct >= 99.9 ? "good" : pct >= 99 ? "fair" : "bad";
  const label = `${clock(day.day).slice(0, 10)}: ${pct === null ? "no readings" : `${Math.round(pct * 10) / 10}% up`}`;
  return `<span class="day ${cls}" title="${esc(label)}" aria-label="${esc(label)}"></span>`;
}

function heartbeatRows(hb, now) {
  if (!hb) return "<p class=\"muted\">No heartbeat yet.</p>";
  const b = hb.body;
  const rows = [
    ["Received", `${clock(hb.received_at)} (${duration(now - hb.received_at)} ago)`],
    ["Guard", b.guard.state],
    ["Kazma", `${b.kazma.state}${b.kazma.why.length ? ` (${b.kazma.why.join(", ")})` : ""}`],
    ["Build", b.kazma.build],
    ["Database", b.database.state],
    ["Docker", b.docker],
    ["Tunnel (cloudflared)", b.cloudflared],
    ["Disk", b.disk.level ? `${b.disk.level}, ${b.disk.free_pct}% free` : null],
    ["Power", b.power ? `${b.power.on_battery ? "on battery" : "on mains"}, ${b.power.percent ?? "?"}%` : "no battery"],
    ["Maintenance", b.maintenance ? "yes" : "no"],
  ];
  return `<dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v ?? "unknown")}</dd>`).join("")}</dl>`;
}

function incidentRows(incidents, now) {
  if (!incidents.length) return "<p class=\"muted\">No incidents in the last 90 days.</p>";
  const rows = incidents.map((i) => {
    const length = i.ended_at ? duration(i.ended_at - i.started_at) : `${duration(now - i.started_at)} so far`;
    const causes = i.causes.map(titleOf).join("; ");
    return `<tr><td>${esc(clock(i.started_at))}</td><td>${esc(causes)}${i.detail ? `<br><span class="muted">${esc(i.detail)}</span>` : ""}</td><td>${esc(length)}</td></tr>`;
  }).join("");
  return `<table><thead><tr><th scope="col">Started</th><th scope="col">Cause</th><th scope="col">Length</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function responseRows(hours) {
  const with_ms = hours.filter((h) => h.ms_count);
  if (!with_ms.length) return "<p class=\"muted\">No response times in the last day.</p>";
  const max = Math.max(...with_ms.map((h) => h.ms_total / h.ms_count));
  return `<ol class="ms">${with_ms.map((h) => {
    const ms = Math.round(h.ms_total / h.ms_count);
    const width = Math.max(2, Math.round((100 * ms) / max));
    return `<li><span class="when">${esc(clock(h.hour).slice(11, 16))}</span><span class="track"><span class="fill" style="width:${width}%"></span></span><span class="val">${ms} ms</span></li>`;
  }).join("")}</ol>`;
}

/** One install's section. ``view`` comes from Store reads in index.js. */
export function installSection(view, now) {
  const good = view.state.condition === "up" || view.state.condition === "maintenance";
  const headline = view.state.condition === "up" ? "Up" : view.state.condition === "maintenance"
    ? "Planned maintenance" : titleOf(view.state.condition);
  return `<section class="install">
  <h2>${esc(view.name)}</h2>
  <p class="now ${good ? "good" : "bad"}"><strong>${esc(headline)}</strong>${view.state.detail ? ` <span class="muted">${esc(view.state.detail)}</span>` : ""}</p>
  <p>Up ${esc(view.up7 ?? "?")}% in 7 days, ${esc(view.up30 ?? "?")}% in 30 days, ${esc(view.up90 ?? "?")}% in 90 days.</p>
  <div class="bars" role="img" aria-label="Uptime, last 90 days">${view.days.map(bar).join("")}</div>
  <h3>Latest heartbeat</h3>
  ${heartbeatRows(view.heartbeat, now)}
  <h3>Incidents</h3>
  ${incidentRows(view.incidents, now)}
  <h3>Response times, last 24 hours</h3>
  ${responseRows(view.hours)}
</section>`;
}

/** Fill 90 days, oldest first, with the days that have no readings. */
export function ninetyDays(rows, now) {
  const today = now - (now % DAY);
  const byDay = new Map(rows.map((r) => [r.day, r]));
  const out = [];
  for (let d = today - 89 * DAY; d <= today; d += DAY) {
    out.push(byDay.get(d) || { day: d, minutes: 0, up: 0 });
  }
  return out;
}

export function statusPage(views, now) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Kazma Watch</title>
<style>
:root { --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e3de;
  --good:#2f8f4e; --fair:#c98a12; --bad:#c2412d; --none:#d6d6d0; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg:#151514; --fg:#ecece8; --muted:#9a9a94;
  --card:#1f1f1d; --line:#33332f; --none:#3a3a36; } }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
main { max-width:860px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:1.4rem; margin:0 0 4px; } h2 { margin:0 0 8px; font-size:1.1rem; }
h3 { font-size:.95rem; margin:20px 0 6px; }
.install { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-top:16px; }
.muted { color:var(--muted); } .now.good strong { color:var(--good); } .now.bad strong { color:var(--bad); }
.bars { display:grid; grid-template-columns:repeat(90, 1fr); gap:2px; height:28px; }
.day { border-radius:2px; } .day.good { background:var(--good); } .day.fair { background:var(--fair); }
.day.bad { background:var(--bad); } .day.none { background:var(--none); }
dl { display:grid; grid-template-columns:max-content 1fr; gap:2px 16px; margin:0; } dt { color:var(--muted); } dd { margin:0; }
table { width:100%; border-collapse:collapse; } th, td { text-align:start; padding:6px 4px; border-top:1px solid var(--line); vertical-align:top; }
.ms { list-style:none; padding:0; margin:0; } .ms li { display:grid; grid-template-columns:3.2em 1fr 5em; gap:8px; align-items:center; }
.track { background:var(--none); height:6px; border-radius:3px; } .fill { display:block; height:6px; border-radius:3px; background:var(--good); }
.val { text-align:end; font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<main>
<h1>Kazma Watch</h1>
<p class="muted">Watched from outside the machine. Updated ${esc(clock(now))}.</p>
${views.length ? views.map((v) => installSection(v, now)).join("\n") : "<p>No install is paired yet. Run <code>kazma observer pair</code> on the install.</p>"}
</main>
</body>
</html>`;
}
