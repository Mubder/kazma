// Problem reports (Kazma REMAINING 3.1): the private inbox Kazma's `?`, `kazma
// report` and `/report` send to by default (kazma_core.support.problem_report).
//
//   POST /v1/report      open (any Kazma install sends here): at most
//                        REPORT_MAX bytes of JSON of kind kazma-problem-report;
//                        PER_SOURCE_HOUR from one address an hour, PER_DAY in
//                        all. Stored in D1; the owner is told on Kazma Watch's
//                        channels (at most NOTIFY_PER_DAY a day).
//   GET  /reports        the owner's list, behind Cloudflare Access (and the
//   GET  /reports/<id>   Worker's own check of its token, access.js).
//
// A sender's address is kept only as an HMAC (the pairing token as key), to
// count; a report is kept KEEP_S, then deleted by the minute run.

import { checkAccess } from "./access.js";
import { page } from "./alerts.js";
import { json } from "./http.js";

export const REPORT_MAX = 64 * 1024;
export const PER_SOURCE_HOUR = 5;
export const PER_DAY = 300;
export const NOTIFY_PER_DAY = 30;
export const KEEP_S = 180 * 86400;
const KIND = "kazma-problem-report";
const ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";

async function readCapped(request, max) {
  const declared = Number(request.headers.get("Content-Length") || "0");
  if (declared > max) return null;
  const raw = await request.arrayBuffer();
  return raw.byteLength > max ? null : new TextDecoder().decode(raw);
}

async function sourceOf(request, env) {
  const address = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = new TextEncoder().encode(String(env.PAIRING_TOKEN || "kazma-reports"));
  const hmacKey = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", hmacKey, new TextEncoder().encode(address)));
  return [...mac.slice(0, 12)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function newId() {
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  return [...bytes].map((b) => ID_ALPHABET[b % ID_ALPHABET.length]).join("");
}

/** Why *report* is not a problem report Kazma makes, or "". */
export function invalid(report) {
  if (!report || typeof report !== "object" || Array.isArray(report)) return "not_an_object";
  if (report.kind !== KIND) return "wrong_kind";
  if (typeof report.version !== "string" || report.version.length > 40) return "bad_version";
  if (typeof report.note !== "string" || report.note.length > 4000) return "bad_note";
  return "";
}

export async function handleReport(request, env, now, deps = {}) {
  const text = await readCapped(request, REPORT_MAX);
  if (text === null) return json({ error: "too_large" }, 413);
  let report;
  try {
    report = JSON.parse(text);
  } catch {
    return json({ error: "not_json" }, 400);
  }
  const why = invalid(report);
  if (why) return json({ error: why }, 400);
  const db = env.DB;
  const source = await sourceOf(request, env);
  const fromSource = await db.prepare(
    "SELECT COUNT(*) AS n FROM problem_reports WHERE source = ? AND received_at > ?",
  ).bind(source, now - 3600).first("n");
  if (Number(fromSource) >= PER_SOURCE_HOUR) return json({ error: "too_many_from_you" }, 429);
  const today = await db.prepare("SELECT COUNT(*) AS n FROM problem_reports WHERE received_at > ?")
    .bind(now - 86400).first("n");
  if (Number(today) >= PER_DAY) return json({ error: "inbox_full_today" }, 429);
  const id = newId();
  const note = report.note.slice(0, 4000);
  await db.prepare(
    "INSERT INTO problem_reports (id, received_at, source, version, note, body, notified) VALUES (?, ?, ?, ?, ?, ?, 0)",
  ).bind(id, now, source, report.version, note, text).run();
  const notified = await db.prepare(
    "SELECT COUNT(*) AS n FROM problem_reports WHERE notified = 1 AND received_at > ?",
  ).bind(now - 86400).first("n");
  if (Number(notified) < NOTIFY_PER_DAY) {
    const words = note.replace(/\s+/g, " ").trim().slice(0, 160) || "(no words)";
    const sent = await page(env, `🐞 Problem report ${id} (Kazma ${report.version}): ${words}`, now,
      { fetcher: deps.fetcher || fetch }).catch(() => false);
    if (sent) await db.prepare("UPDATE problem_reports SET notified = 1 WHERE id = ?").bind(id).run();
  }
  return json({ ok: true, id });
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function htmlPage(title, body) {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title>
<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:1100px}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ccc;padding:6px;text-align:start;vertical-align:top}pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f4;padding:12px}
@media (prefers-color-scheme: dark){body{background:#111;color:#eee}pre{background:#222}td,th{border-color:#444}a{color:#8ab4ff}}</style>
</head><body>${body}</body></html>`, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export async function handleReportsPage(request, env, now, deps = {}) {
  const access = await checkAccess(request, env, { fetcher: deps.fetcher || fetch, now: now * 1000 });
  if (!access.ok) {
    return new Response(access.status === 503 ? "The reports page is not set up." : "Forbidden.", {
      status: access.status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  const path = new URL(request.url).pathname;
  const one = path.match(/^\/reports\/([a-z0-9]{6,20})$/);
  if (one) {
    const row = await env.DB.prepare("SELECT * FROM problem_reports WHERE id = ?").bind(one[1]).first();
    if (!row) return new Response("No such report.", { status: 404 });
    let pretty = row.body;
    try { pretty = JSON.stringify(JSON.parse(row.body), null, 2); } catch { /* shown as received */ }
    return htmlPage(`Report ${row.id}`, `<p><a href="/reports">All reports</a></p><h1>Report ${esc(row.id)}</h1>
<p>${esc(new Date(row.received_at * 1000).toISOString())} · Kazma ${esc(row.version)}</p><pre>${esc(pretty)}</pre>`);
  }
  const rows = (await env.DB.prepare(
    "SELECT id, received_at, version, note FROM problem_reports ORDER BY received_at DESC LIMIT 200",
  ).all()).results || [];
  const list = rows.map((r) => `<tr><td><a href="/reports/${esc(r.id)}">${esc(r.id)}</a></td>
<td>${esc(new Date(r.received_at * 1000).toISOString().slice(0, 16).replace("T", " "))}</td>
<td>${esc(r.version)}</td><td>${esc(String(r.note || "").slice(0, 200))}</td></tr>`).join("");
  return htmlPage("Problem reports", `<h1>Problem reports</h1><p>${rows.length} newest first; kept 180 days.</p>
<table><thead><tr><th>Number</th><th>Received (UTC)</th><th>Kazma</th><th>Their words</th></tr></thead><tbody>${list}</tbody></table>`);
}

/** The minute run's share: reports older than KEEP_S go. */
export async function pruneReports(db, now) {
  await db.prepare("DELETE FROM problem_reports WHERE received_at < ?").bind(now - KEEP_S).run();
}
