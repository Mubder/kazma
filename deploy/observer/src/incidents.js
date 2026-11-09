// Incidents: one page when a bad condition has lasted three minutes, a
// reminder every hour while it lasts, one recovery page with how long it
// lasted. Planned maintenance pages nothing. A page no channel took is tried
// again the next minute.
//
// ``step`` is pure: it takes what the observer knew a minute ago and the
// diagnosis now, and returns the new state and the pages to send. The run
// (index.js) sends them and records which went out (``settle``).
//
// One page per problem (Stage 2, receipts.js): an incident Kazma or its
// guard already paged, or a Cloudflare notice Kazma Watch already paged, is
// held -- recorded (``held_by``), never paged, and closed with no recovery
// page: the sender that paged sends it.

import { formatOperatorCard } from "./card.js";
import { GOOD, severityOf, titleOf } from "./diagnose.js";
import { decide, titleOfKey } from "./receipts.js";

// Three failed minutes: the runs are a minute apart, so the third bad
// reading (120 s after the first) opens the incident; 10 s of slack.
export const OPEN_AFTER_S = 110;
export const REMIND_EVERY_S = 3600;
// A recovery page no channel took is given up after this long.
export const RECOVERY_RETRY_S = 3600;

export function duration(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// Times are UTC until the owner's zone reaches Kazma Watch (Settings ->
// Alerts, the unified alerts plan's Stage 3).
export function clock(epoch) {
  return new Date(epoch * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/**
 * prev: {condition, detail, bad_since, incident}; incident:
 * {id, cause, detail, causes[], started_at, paged_at, last_page_at, ended_at,
 *  recovery_paged_at, held_by, note, inside[], witnesses[]} or null.
 *
 * diag: the diagnosis ({condition, detail}) and what answers for it:
 * ``pages`` (the fresh heartbeat's receipts, null when it carries none),
 * ``inside`` (the inside pages open when the heartbeats stopped) and
 * ``notices`` (Cloudflare notices Kazma Watch paged, still open).
 */
export function step(prev, diag, now) {
  const state = {
    condition: diag.condition,
    detail: diag.detail || null,
    bad_since: prev.bad_since ?? null,
    incident: prev.incident ? {
      ...prev.incident,
      causes: [...(prev.incident.causes || [])],
      inside: [...(prev.incident.inside || [])],
      witnesses: [...(prev.incident.witnesses || [])],
    } : null,
  };
  const pages = [];
  const incident = state.incident;

  // A recovery page still to send: retried, then given up.
  if (incident && incident.ended_at) {
    if (now - incident.ended_at > RECOVERY_RETRY_S) {
      state.incident = null;
    } else {
      pages.push({ kind: "recovered" });
      return finish(state, diag, now, pages);
    }
  }

  if (GOOD.has(diag.condition)) {
    state.bad_since = null;
    if (state.incident && diag.condition === "up") {
      state.incident.ended_at = now;
      if (state.incident.paged_at === null && state.incident.held_by) {
        // Held all along: the sender that paged sends the recovery.
        state.closed = state.incident;
        state.incident = null;
      } else {
        pages.push({ kind: "recovered" });
      }
    }
    return finish(state, diag, now, pages);
  }

  if (state.bad_since === null) state.bad_since = now;
  if (!state.incident) {
    if (now - state.bad_since >= OPEN_AFTER_S) {
      state.incident = {
        id: null,
        cause: diag.condition,
        detail: diag.detail || null,
        causes: [diag.condition],
        started_at: state.bad_since,
        paged_at: null,
        last_page_at: null,
        ended_at: null,
        recovery_paged_at: null,
        held_by: null,
        note: null,
        inside: [],
        witnesses: [],
      };
      openOrHold(state.incident, diag, now, state.bad_since, pages);
    }
    return finish(state, diag, now, pages);
  }

  const open = state.incident;
  if (open.cause !== diag.condition) {
    open.cause = diag.condition;
    if (!open.causes.includes(diag.condition)) open.causes.push(diag.condition);
  }
  open.detail = diag.detail || null;
  if (open.paged_at === null) openOrHold(open, diag, now, state.bad_since ?? open.started_at, pages);
  else if (now - (open.last_page_at || open.paged_at) >= REMIND_EVERY_S) pages.push({ kind: "remind" });
  return finish(state, diag, now, pages);
}

/** An incident Kazma Watch has not paged: page it, or hold it (receipts.js). */
function openOrHold(incident, diag, now, badSince, pages) {
  const d = decide(diag.condition, {
    pages: diag.pages, notices: diag.notices, now, badSince, openAfter: OPEN_AFTER_S,
  });
  if (d.act === "hold") {
    incident.held_by = d.by;
    return;
  }
  if (d.act === "wait") {
    // A page from inside may still come: say nothing yet, and nothing at
    // the end if it never had to (held_by "pending").
    if (!incident.held_by) incident.held_by = "pending";
    return;
  }
  // Paging after all: what was held, and what was open inside when the
  // heartbeats stopped, is said on the page.
  const said = [];
  if (incident.held_by && incident.held_by !== "pending") {
    said.push(`Paged before by: ${titleOfKey(incident.held_by)}.`);
  }
  for (const key of diag.inside || []) {
    if (!incident.inside.includes(key)) incident.inside.push(key);
  }
  if (d.note) said.push(d.note);
  incident.note = said.length ? said.join("\n") : null;
  incident.held_by = null;
  pages.push({ kind: "open" });
}

function finish(state, diag, now, pages) {
  return { state, pages: pages.map((p) => ({ ...p, text: pageText(p.kind, state.incident, now) })) };
}

/**
 * The page for ``kind`` ("open", "remind", "recovered"): Kazma's operator
 * card with the source Watch (card.js), its title and severity from the
 * alert catalog.
 */
export function pageText(kind, incident, now) {
  if (!incident) return "";
  if (kind === "open" || kind === "remind") {
    return formatOperatorCard("watch", severityOf(incident.cause), titleOf(incident.cause),
      [incident.detail || "", ...notes(incident)].filter(Boolean).join("\n"), {
        form: kind === "remind" ? "reminder" : "page",
        since: clock(incident.started_at),
        duration: duration(now - incident.started_at),
      });
  }
  const end = incident.ended_at || now;
  const lines = [`It was: ${titleOf(incident.causes[0] || incident.cause)}.`];
  if (incident.causes.length > 1) lines.push(`On the way: ${incident.causes.map(titleOf).join("; ")}.`);
  // "🟢 [Watch] Kazma is back: it lasted 12 min." (watch.recovered)
  return formatOperatorCard("watch", "success", "Kazma", lines.join("\n"),
    { form: "recovery", duration: duration(end - incident.started_at) });
}

/** What a page says beyond the condition: what was paged before, what was
 * open inside when the heartbeats stopped, and Cloudflare's word for it. */
function notes(incident) {
  const out = [];
  if (incident.note) out.push(incident.note);
  if ((incident.inside || []).length) {
    out.push(`Open on Kazma when its heartbeats stopped: ${incident.inside.map(titleOfKey).join("; ")}.`);
  }
  if ((incident.witnesses || []).length) {
    out.push(`Cloudflare says so too: ${incident.witnesses.map(titleOfKey).join("; ")}.`);
  }
  return out;
}

/** Record that ``page`` went out (``sent``) on the state's incident. */
export function settle(state, page, sent, now) {
  const incident = state.incident;
  if (!incident || !sent) return state;
  if (page.kind === "recovered") {
    incident.recovery_paged_at = now;
    return { ...state, incident: null, closed: incident };
  }
  if (incident.paged_at === null) incident.paged_at = now;
  incident.last_page_at = now;
  return state;
}
