-- The outside observer's D1 database (resilience plan B1).
-- Apply once: npx wrangler d1 execute kazma-observer --remote --file=schema.sql
-- Every statement is idempotent, so applying it again is safe.

-- One row per paired install. ``install`` is a hash (16 hex), never a name;
-- ``key`` is the pairing key the guard signs its heartbeats with.
CREATE TABLE IF NOT EXISTS installs (
  install    TEXT PRIMARY KEY,
  key        TEXT NOT NULL,
  paired_at  INTEGER NOT NULL,
  -- The newest heartbeat timestamp accepted: a heartbeat must be newer, so
  -- a recorded one cannot be sent again.
  last_ts    INTEGER NOT NULL DEFAULT 0
);

-- The latest heartbeat of each install, already cut to the allowlist.
CREATE TABLE IF NOT EXISTS heartbeats (
  install      TEXT PRIMARY KEY,
  received_at  INTEGER NOT NULL,
  body         TEXT NOT NULL
);

-- One reading a minute per install: kept 30 days.
CREATE TABLE IF NOT EXISTS readings (
  install          TEXT NOT NULL,
  at               INTEGER NOT NULL,
  condition        TEXT NOT NULL,
  live_status      INTEGER,
  live_ms          INTEGER,
  heartbeat_age_s  INTEGER,
  PRIMARY KEY (install, at)
);

-- Hourly summaries: kept a year (400 days). ``up_minutes`` counts up and
-- planned maintenance; the rest is downtime.
CREATE TABLE IF NOT EXISTS hourly (
  install     TEXT NOT NULL,
  hour        INTEGER NOT NULL,
  minutes     INTEGER NOT NULL DEFAULT 0,
  up_minutes  INTEGER NOT NULL DEFAULT 0,
  ms_total    INTEGER NOT NULL DEFAULT 0,
  ms_count    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (install, hour)
);

-- What the observer last concluded about each install.
CREATE TABLE IF NOT EXISTS watch_state (
  install       TEXT PRIMARY KEY,
  condition     TEXT NOT NULL,
  detail        TEXT,
  bad_since     INTEGER,
  incident_id   INTEGER,
  ready_status  INTEGER,
  ready_at      INTEGER,
  updated_at    INTEGER NOT NULL
);

-- Incidents: kept for good (a forget request removes an install's).
CREATE TABLE IF NOT EXISTS incidents (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  install       TEXT NOT NULL,
  cause         TEXT NOT NULL,
  detail        TEXT,
  causes        TEXT NOT NULL DEFAULT '[]',
  started_at    INTEGER NOT NULL,
  paged_at      INTEGER,
  last_page_at  INTEGER,
  ended_at      INTEGER,
  recovery_paged_at INTEGER
);
CREATE INDEX IF NOT EXISTS incidents_by_install ON incidents (install, started_at);
