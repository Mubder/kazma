// The observer's D1 store (schema.sql). Every statement is here, so the
// tests run the same SQL on a stand-in database.

const DAY = 86400;
export const READINGS_KEEP_S = 30 * DAY;
export const HOURLY_KEEP_S = 400 * DAY;

export class Store {
  constructor(db) {
    this.db = db;
  }

  // -- installs -----------------------------------------------------------

  async listInstalls() {
    const { results } = await this.db.prepare("SELECT install FROM installs ORDER BY install").all();
    return (results || []).map((r) => r.install);
  }

  async installKey(install) {
    return this.db.prepare("SELECT key FROM installs WHERE install = ?").bind(install).first("key");
  }

  async pair(install, key, now) {
    await this.db.prepare(
      "INSERT INTO installs (install, key, paired_at, last_ts) VALUES (?, ?, ?, 0) "
      + "ON CONFLICT(install) DO UPDATE SET key = excluded.key, paired_at = excluded.paired_at, last_ts = 0",
    ).bind(install, key, now).run();
  }

  /** Stop watching ``install``; with ``forget`` remove everything kept about it. */
  async unpair(install, forget) {
    const tables = ["installs", "heartbeats", "watch_state", "install_settings"];
    if (forget) tables.push("readings", "hourly", "cloudflare_notices", "incidents");
    const notes = forget ? [this.db.prepare(
      "DELETE FROM incident_notes WHERE incident_id IN (SELECT id FROM incidents WHERE install = ?)",
    ).bind(install)] : [];
    await this.db.batch([...notes, ...tables.map(
      (t) => this.db.prepare(`DELETE FROM ${t} WHERE install = ?`).bind(install))]);
  }

  /**
   * Accept a heartbeat at ``ts`` only when it is newer than the last one
   * accepted (a recorded heartbeat cannot be sent again). True if accepted.
   */
  async acceptHeartbeat(install, ts, body, now) {
    const res = await this.db.prepare(
      "UPDATE installs SET last_ts = ? WHERE install = ? AND last_ts < ?",
    ).bind(ts, install, ts).run();
    if (!res.meta || !res.meta.changes) return false;
    await this.db.prepare(
      "INSERT INTO heartbeats (install, received_at, body) VALUES (?, ?, ?) "
      + "ON CONFLICT(install) DO UPDATE SET received_at = excluded.received_at, body = excluded.body",
    ).bind(install, now, JSON.stringify(body)).run();
    return true;
  }

  async heartbeat(install) {
    const row = await this.db.prepare(
      "SELECT received_at, body FROM heartbeats WHERE install = ?").bind(install).first();
    if (!row) return null;
    try {
      return { received_at: row.received_at, body: JSON.parse(row.body) };
    } catch {
      return null;
    }
  }

  // -- the watch ----------------------------------------------------------

  async state(install) {
    const row = await this.db.prepare("SELECT * FROM watch_state WHERE install = ?").bind(install).first();
    let incident = null;
    if (row && row.incident_id) {
      const inc = await this.db.prepare("SELECT * FROM incidents WHERE id = ?").bind(row.incident_id).first();
      if (inc) incident = { ...inc, causes: JSON.parse(inc.causes || "[]"), ...(await this.notes(inc.id)) };
    }
    return {
      condition: row ? row.condition : "up",
      detail: row ? row.detail : null,
      bad_since: row ? row.bad_since : null,
      ready: row && row.ready_at ? { status: row.ready_status, at: row.ready_at } : null,
      incident,
    };
  }

  /** Write the watch's state, and its incident (created when new). */
  async saveState(install, state, now) {
    let incidentId = null;
    for (const inc of [state.closed, state.incident]) {
      if (!inc) continue;
      if (inc.id) {
        await this.db.prepare(
          "UPDATE incidents SET cause = ?, detail = ?, causes = ?, paged_at = ?, last_page_at = ?, "
          + "ended_at = ?, recovery_paged_at = ? WHERE id = ?",
        ).bind(inc.cause, inc.detail, JSON.stringify(inc.causes), inc.paged_at, inc.last_page_at,
          inc.ended_at, inc.recovery_paged_at, inc.id).run();
      } else {
        const res = await this.db.prepare(
          "INSERT INTO incidents (install, cause, detail, causes, started_at, paged_at, last_page_at, "
          + "ended_at, recovery_paged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ).bind(install, inc.cause, inc.detail, JSON.stringify(inc.causes), inc.started_at,
          inc.paged_at, inc.last_page_at, inc.ended_at, inc.recovery_paged_at).run();
        inc.id = res.meta.last_row_id;
      }
      await this.saveNotes(inc);
      if (inc === state.incident) incidentId = inc.id;
    }
    await this.db.prepare(
      "INSERT INTO watch_state (install, condition, detail, bad_since, incident_id, ready_status, ready_at, updated_at) "
      + "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(install) DO UPDATE SET condition = excluded.condition, "
      + "detail = excluded.detail, bad_since = excluded.bad_since, incident_id = excluded.incident_id, "
      + "ready_status = excluded.ready_status, ready_at = excluded.ready_at, updated_at = excluded.updated_at",
    ).bind(install, state.condition, state.detail, state.bad_since, incidentId,
      state.ready ? state.ready.status : null, state.ready ? state.ready.at : null, now).run();
  }

  async record(install, now, condition, live, heartbeatAge, up) {
    const hour = now - (now % 3600);
    const ms = live && Number.isFinite(live.ms) ? Math.round(live.ms) : null;
    await this.db.batch([
      this.db.prepare(
        "INSERT OR REPLACE INTO readings (install, at, condition, live_status, live_ms, heartbeat_age_s) "
        + "VALUES (?, ?, ?, ?, ?, ?)",
      ).bind(install, now, condition, live && live.status ? live.status : null, ms, heartbeatAge),
      this.db.prepare(
        "INSERT INTO hourly (install, hour, minutes, up_minutes, ms_total, ms_count) VALUES (?, ?, 1, ?, ?, ?) "
        + "ON CONFLICT(install, hour) DO UPDATE SET minutes = minutes + 1, up_minutes = up_minutes + excluded.up_minutes, "
        + "ms_total = ms_total + excluded.ms_total, ms_count = ms_count + excluded.ms_count",
      ).bind(install, hour, up ? 1 : 0, ms ?? 0, ms === null ? 0 : 1),
    ]);
  }

  async prune(now) {
    await this.db.batch([
      this.db.prepare("DELETE FROM readings WHERE at < ?").bind(now - READINGS_KEEP_S),
      this.db.prepare("DELETE FROM hourly WHERE hour < ?").bind(now - HOURLY_KEEP_S),
    ]);
  }

  // -- reading back ---------------------------------------------------------

  /** The incidents of ``install`` open since ``since``, newest first; each
   * with ``held_by`` (its notes: who paged it instead, unified alerts
   * Stage 2), so a reader can tell a page that failed from one never due. */
  async incidents(install, since, limit = 50) {
    const { results } = await this.db.prepare(
      "SELECT i.id, i.cause, i.detail, i.causes, i.started_at, i.ended_at, i.paged_at, i.recovery_paged_at, "
      + "n.notes FROM incidents i LEFT JOIN incident_notes n ON n.incident_id = i.id "
      + "WHERE i.install = ? AND (i.ended_at IS NULL OR i.ended_at >= ?) ORDER BY i.started_at DESC LIMIT ?",
    ).bind(install, since, limit).all();
    return (results || []).map(({ notes, ...r }) => {
      let heldBy = null;
      try {
        const n = notes ? JSON.parse(notes) : {};
        heldBy = typeof n.held_by === "string" ? n.held_by : null;
      } catch {
        heldBy = null;
      }
      return { ...r, causes: JSON.parse(r.causes || "[]"), held_by: heldBy };
    });
  }

  /** Per day: the share of watched minutes up (or in planned maintenance). */
  async daily(install, since) {
    const { results } = await this.db.prepare(
      "SELECT (hour - (hour % 86400)) AS day, SUM(minutes) AS minutes, SUM(up_minutes) AS up, "
      + "SUM(ms_total) AS ms_total, SUM(ms_count) AS ms_count FROM hourly "
      + "WHERE install = ? AND hour >= ? GROUP BY day ORDER BY day",
    ).bind(install, since).all();
    return results || [];
  }

  async hours(install, since) {
    const { results } = await this.db.prepare(
      "SELECT hour, minutes, up_minutes, ms_total, ms_count FROM hourly WHERE install = ? AND hour >= ? ORDER BY hour",
    ).bind(install, since).all();
    return results || [];
  }

  // -- one page per problem: an incident's notes (unified alerts, Stage 2) --

  /** What an incident's pages say beyond its condition (incident_notes). */
  async notes(incidentId) {
    const raw = await this.db.prepare("SELECT notes FROM incident_notes WHERE incident_id = ?")
      .bind(incidentId).first("notes");
    let n = {};
    try {
      n = raw ? JSON.parse(raw) : {};
    } catch {
      n = {};
    }
    return {
      held_by: typeof n.held_by === "string" ? n.held_by : null,
      note: typeof n.note === "string" ? n.note : null,
      inside: Array.isArray(n.inside) ? n.inside : [],
      witnesses: Array.isArray(n.witnesses) ? n.witnesses : [],
    };
  }

  /** Keep an incident's notes, when it has any. */
  async saveNotes(inc) {
    const notes = {
      held_by: inc.held_by || null,
      note: inc.note || null,
      inside: inc.inside || [],
      witnesses: inc.witnesses || [],
    };
    if (!notes.held_by && !notes.note && !notes.inside.length && !notes.witnesses.length) return;
    await this.db.prepare(
      "INSERT INTO incident_notes (incident_id, notes) VALUES (?, ?) "
      + "ON CONFLICT(incident_id) DO UPDATE SET notes = excluded.notes",
    ).bind(inc.id, JSON.stringify(notes)).run();
  }

  /** When the incident ``id`` ended: null while it is open, 0 when it is gone. */
  async incidentEnded(id) {
    const row = await this.db.prepare("SELECT ended_at FROM incidents WHERE id = ?").bind(id).first();
    return row ? row.ended_at ?? null : 0;
  }

  // -- Cloudflare's notifications (unified alerts, Stage 4) -----------------

  /** Take a request body's digest once. True when it is new. */
  async takeDigest(digest, now) {
    const res = await this.db.prepare(
      "INSERT OR IGNORE INTO cloudflare_seen (digest, received_at) VALUES (?, ?)",
    ).bind(digest, now).run();
    return !!(res.meta && res.meta.changes);
  }

  async addNotice(n) {
    const res = await this.db.prepare(
      "INSERT INTO cloudflare_notices (key, alert_type, correlation, event, detail, received_at) "
      + "VALUES (?, ?, ?, ?, ?, ?)",
    ).bind(n.key, n.alert_type, n.correlation, n.event, n.detail ?? null, n.received_at).run();
    return { ...n, id: res.meta.last_row_id, install: null, incident_id: null, paged_at: null,
      last_try_at: null, ended_at: null, closer: null, recovery_due: 0, recovery_paged_at: null };
  }

  async saveNotice(n) {
    await this.db.prepare(
      "UPDATE cloudflare_notices SET key = ?, install = ?, incident_id = ?, paged_at = ?, last_try_at = ?, "
      + "ended_at = ?, closer = ?, recovery_due = ?, recovery_paged_at = ? WHERE id = ?",
    ).bind(n.key, n.install ?? null, n.incident_id ?? null, n.paged_at ?? null, n.last_try_at ?? null,
      n.ended_at ?? null, n.closer ?? null, n.recovery_due ? 1 : 0, n.recovery_paged_at ?? null, n.id).run();
  }

  /** The open start notice of ``correlation``, if any. */
  async openNotice(correlation) {
    return this.db.prepare(
      "SELECT * FROM cloudflare_notices WHERE correlation = ? AND ended_at IS NULL AND event = 'start' "
      + "ORDER BY id DESC LIMIT 1",
    ).bind(correlation).first();
  }

  /** Notices with something left to do: open, or a recovery page due. */
  async activeNotices() {
    const { results } = await this.db.prepare(
      "SELECT * FROM cloudflare_notices WHERE ended_at IS NULL OR recovery_due = 1 ORDER BY id",
    ).all();
    return results || [];
  }

  /** How many notices were paged since ``since`` (the hourly cap). */
  async noticesPagedSince(since) {
    return this.db.prepare("SELECT COUNT(*) AS n FROM cloudflare_notices WHERE paged_at >= ?")
      .bind(since).first("n");
  }

  async pruneNotices(now) {
    await this.db.batch([
      this.db.prepare("DELETE FROM cloudflare_seen WHERE received_at < ?").bind(now - 7 * DAY),
      this.db.prepare("DELETE FROM cloudflare_notices WHERE ended_at IS NOT NULL AND ended_at < ? "
        + "AND recovery_due = 0").bind(now - 90 * DAY),
    ]);
  }

  // -- what Kazma told Kazma Watch (Settings -> Alerts, watch_settings.js) --

  async watchSettings(install) {
    const row = await this.db.prepare(
      "SELECT timezone, email_to, updated_at FROM install_settings WHERE install = ?").bind(install).first();
    return {
      timezone: row && row.timezone ? row.timezone : null,
      email_to: row && row.email_to ? row.email_to : null,
      updated_at: row && row.updated_at ? row.updated_at : null,
    };
  }

  /** Keep the settings sent at ``ts``, only when newer than the last kept. */
  async saveWatchSettings(install, ts, settings, now) {
    const res = await this.db.prepare(
      "INSERT INTO install_settings (install, timezone, email_to, settings_ts, updated_at) VALUES (?, ?, ?, ?, ?) "
      + "ON CONFLICT(install) DO UPDATE SET timezone = excluded.timezone, email_to = excluded.email_to, "
      + "settings_ts = excluded.settings_ts, updated_at = excluded.updated_at "
      + "WHERE install_settings.settings_ts < excluded.settings_ts",
    ).bind(install, settings.timezone, settings.email_to, ts, now).run();
    return !!(res.meta && res.meta.changes);
  }

  /** Claim a test page asked at ``ts``: newer than the last by ``gap`` seconds. */
  async claimTest(install, ts, now, gap) {
    const res = await this.db.prepare(
      "INSERT INTO install_settings (install, test_ts, updated_at) VALUES (?, ?, ?) "
      + "ON CONFLICT(install) DO UPDATE SET test_ts = excluded.test_ts "
      + "WHERE excluded.test_ts >= install_settings.test_ts + ?",
    ).bind(install, ts, now, gap).run();
    return !!(res.meta && res.meta.changes);
  }
}
