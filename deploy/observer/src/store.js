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
    const tables = ["installs", "heartbeats", "watch_state"];
    if (forget) tables.push("readings", "hourly", "incidents");
    await this.db.batch(tables.map(
      (t) => this.db.prepare(`DELETE FROM ${t} WHERE install = ?`).bind(install)));
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
      if (inc) incident = { ...inc, causes: JSON.parse(inc.causes || "[]") };
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

  async incidents(install, since, limit = 50) {
    const { results } = await this.db.prepare(
      "SELECT id, cause, detail, causes, started_at, ended_at FROM incidents "
      + "WHERE install = ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY started_at DESC LIMIT ?",
    ).bind(install, since, limit).all();
    return (results || []).map((r) => ({ ...r, causes: JSON.parse(r.causes || "[]") }));
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
}
