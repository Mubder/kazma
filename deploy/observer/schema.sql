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

-- One page per problem (the unified alerts plan, Stage 2) and Cloudflare's
-- notifications (Stage 4). New tables, not new columns: an ALTER TABLE
-- would make applying this file a second time fail.

-- What an incident's pages say beyond its condition, one JSON object per
-- incident: who paged it instead of Kazma Watch (held_by: an inside page's
-- catalog key, a Cloudflare notice's, or "pending"), the note its page
-- carries, the inside pages open when the heartbeats stopped, and the
-- Cloudflare notices that witnessed it.
CREATE TABLE IF NOT EXISTS incident_notes (
  incident_id  INTEGER PRIMARY KEY,
  notes        TEXT NOT NULL DEFAULT '{}'
);

-- Cloudflare's notifications (POST /v1/cloudflare), mapped to the alert
-- catalog (key). ``event``: start (closed by an end), once (paged once).
-- A notice joins an incident Kazma Watch has open for the same cause
-- (incident_id), or is paged on its own (paged_at); its recovery page is
-- due when its end comes (recovery_due, closer).
CREATE TABLE IF NOT EXISTS cloudflare_notices (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  key                TEXT NOT NULL,
  alert_type         TEXT NOT NULL,
  correlation        TEXT NOT NULL,
  event              TEXT NOT NULL,
  detail             TEXT,
  received_at        INTEGER NOT NULL,
  install            TEXT,
  incident_id        INTEGER,
  paged_at           INTEGER,
  last_try_at        INTEGER,
  ended_at           INTEGER,
  closer             TEXT,
  recovery_due       INTEGER NOT NULL DEFAULT 0,
  recovery_paged_at  INTEGER
);
CREATE INDEX IF NOT EXISTS cloudflare_notices_open ON cloudflare_notices (ended_at, correlation);

-- The digest of every notification body taken (7 days): a replayed or
-- retried request is answered, never paged twice.
CREATE TABLE IF NOT EXISTS cloudflare_seen (
  digest       TEXT PRIMARY KEY,
  received_at  INTEGER NOT NULL
);

-- What Kazma told Kazma Watch of its owner (Settings -> Alerts, the unified
-- alerts plan's Stage 3; src/watch_settings.js): the time zone its cards'
-- times are written in, and the email recipient. ``settings_ts`` and
-- ``test_ts`` are the signed timestamps of the last settings and test page
-- accepted: a recorded request cannot be sent again. Removed on unpair.
CREATE TABLE IF NOT EXISTS install_settings (
  install      TEXT PRIMARY KEY,
  timezone     TEXT,
  email_to     TEXT,
  settings_ts  INTEGER NOT NULL DEFAULT 0,
  test_ts      INTEGER NOT NULL DEFAULT 0,
  updated_at   INTEGER NOT NULL DEFAULT 0
);
