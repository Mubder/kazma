"""Memory V2 schema — bi-temporal cognitive state + operational queue.

Two SQLite databases, deliberately split so background consolidation
writes never cause WAL contention with live chat reads:

* **Primary** (:func:`kazma_core.paths.primary_memory_db`) → ``memory_state.db``
  - ``beliefs``          — bi-temporal knowledge graph (functional/set/state)
  - ``episodes``         — 4-tier episodic memory (working/episodic/recall/archived)
  - ``entities``         — canonical entity registry + aliases
  - ``entity_merges``    — quarantine merge ledger (3-tier resolution audit trail)
  - ``procedural_dags``  — parametric action skills (Laplace-smoothed confidence)
  - ``beliefs_archive``  — cold storage for superseded beliefs (>180 days)

* **Operational** (:func:`kazma_core.paths.memory_ops_db`) → ``memory_ops.db``
  - ``memory_task_queue`` — durable async consolidation queue (crash-resilient)
  - ``memory_audit_log``  — immutable mutation audit trail

Both databases use WAL + ``synchronous=NORMAL`` via the shared
:func:`kazma_core.config_store.apply_sqlite_pragmas` helper — never
reinvent the pragma set here.

Resolution of blocking items (2026-07-31):
* ``source_session`` / ``source_turn`` are **nullable** — the post-turn
  hook may not always have provenance available, and the build must never
  fail on a missing session id (resolution #3).
* ``embedding_model_version`` defaults to the **current live model**
  (``BAAI/bge-m3``) — so existing vectors remain valid and no re-index
  migration is required at rollout (resolution #2). The column enables a
  *future* model swap per-row; write sites stamp it explicitly (see
  ``dual_write`` / ``belief_mutation``) so rows stay accurate even when a
  database was created before a model switch.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "PRIMARY_DDL",
    "OPS_DDL",
    "ensure_ops_schema",
    "ensure_primary_schema",
]

logger = logging.getLogger(__name__)

# Default model version — MUST match memory/embedder.py DEFAULT_MODEL so
# existing vectors stay valid. See module docstring (resolution #2). New
# databases get this DEFAULT; write sites stamp the LIVE model explicitly.
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"


# ── Primary cognitive-state DDL (memory_state.db) ─────────────────────────

PRIMARY_DDL = """
-- Bi-temporal belief graph.
-- Tracks real-world validity time (valid_from/valid_until) separately from
-- system ingestion time (ingested_at/invalidated_at). Functional predicates
-- are single-valued: an incoming mutation supersedes the prior active row
-- via supersedes_id. Set-valued predicates append; state predicates log a
-- transition. See belief_mutation.py for the mutation rules.
CREATE TABLE IF NOT EXISTS beliefs (
  id                      TEXT PRIMARY KEY,
  tenant_id               TEXT NOT NULL DEFAULT 'default',
  agent_id                TEXT NOT NULL DEFAULT 'kazma_core',
  visibility              TEXT NOT NULL DEFAULT 'private',   -- private|swarm_shared|global

  subject                 TEXT NOT NULL,                     -- canonical entity slug
  predicate               TEXT NOT NULL,
  predicate_type          TEXT NOT NULL,                     -- functional|set|state
  object                  TEXT NOT NULL,                     -- fact payload

  confidence              REAL NOT NULL DEFAULT 0.5,         -- 0.0..1.0 (LLM certainty)
  structural_importance   INTEGER DEFAULT 1,                 -- 1..5
  source_trust_weight     REAL NOT NULL DEFAULT 1.0,         -- user=1.0 tool=0.85 llm=0.60

  -- Bi-temporal timestamps (Unix epoch seconds)
  valid_from              REAL NOT NULL,
  valid_until             REAL,                              -- NULL = currently valid
  ingested_at             REAL NOT NULL,
  invalidated_at          REAL,                              -- system invalidation ts

  supersedes_id           TEXT,                              -- replaced belief pointer
  source_session          TEXT,                              -- nullable (resolution #3)
  source_turn             INTEGER,                           -- nullable (resolution #3)
  extraction_method       TEXT NOT NULL DEFAULT 'llm_inferred',  -- user_explicit|system_tool|llm_inferred
  embedding_model_version TEXT DEFAULT '%s',
  metadata_json           TEXT DEFAULT '{}',

  FOREIGN KEY (supersedes_id) REFERENCES beliefs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_beliefs_active
  ON beliefs(tenant_id, subject, predicate)
  WHERE valid_until IS NULL AND invalidated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_beliefs_temporal
  ON beliefs(subject, valid_from, valid_until);

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_predicate
  ON beliefs(tenant_id, predicate, valid_until);

-- Covering indexes for entity belief_count / degree (Phase 3 perf). The
-- operator /memory page computes these per-row via correlated subqueries;
-- these partial indexes let the count queries scan only active rows scoped
-- by tenant + endpoint (subject or object) instead of the full table.
CREATE INDEX IF NOT EXISTS idx_beliefs_active_obj
  ON beliefs(tenant_id, object)
  WHERE valid_until IS NULL AND invalidated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_beliefs_active_subj
  ON beliefs(tenant_id, subject)
  WHERE valid_until IS NULL AND invalidated_at IS NULL;

-- Tiered episodic memory.
CREATE TABLE IF NOT EXISTS episodes (
  id                      TEXT PRIMARY KEY,
  tenant_id               TEXT NOT NULL DEFAULT 'default',
  session_id              TEXT NOT NULL,
  turn_number             INTEGER NOT NULL,

  user_text               TEXT,
  assistant_text          TEXT,
  summary_text            TEXT,                              -- compacted summary (post-consolidation)

  tier                    TEXT NOT NULL DEFAULT 'episodic',  -- working|episodic|recall|archived
  structural_importance   INTEGER DEFAULT 1,                 -- 1..5
  access_count            INTEGER DEFAULT 0,
  last_accessed           REAL,
  created_at              REAL NOT NULL,
  expires_at              REAL,                              -- per-tier TTL
  embedding_model_version TEXT DEFAULT '%s',
  metadata_json           TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_episodes_tier_expires
  ON episodes(tenant_id, tier, expires_at);

CREATE INDEX IF NOT EXISTS idx_episodes_session_turn
  ON episodes(session_id, turn_number);

-- Canonical entity registry.
CREATE TABLE IF NOT EXISTS entities (
  id              TEXT PRIMARY KEY,                          -- canonical slug
  tenant_id       TEXT NOT NULL DEFAULT 'default',
  type            TEXT NOT NULL,                             -- person|project|tool|concept|location
  name            TEXT NOT NULL,                             -- display label
  aliases_json    TEXT DEFAULT '[]',
  is_high_stakes  INTEGER DEFAULT 0,                         -- 1 = quarantine before merge
  is_protected    INTEGER DEFAULT 0,                         -- 1 = operator-marked undeletable/unmergeable (F3)
  metadata_json   TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_entities_tenant_type
  ON entities(tenant_id, type);

-- Name lookup for entity search (Phase 3 perf — LIKE fallback path).
CREATE INDEX IF NOT EXISTS idx_entities_tenant_name
  ON entities(tenant_id, name);

-- Quarantine merge ledger — full audit trail of every identity decision.
CREATE TABLE IF NOT EXISTS entity_merges (
  id                TEXT PRIMARY KEY,
  tenant_id         TEXT NOT NULL DEFAULT 'default',
  source_entity_id  TEXT NOT NULL,
  target_entity_id  TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',         -- pending|approved|rejected|auto_merged
  merge_tier        TEXT NOT NULL,                           -- tier1_exact|tier2_vector|tier3_llm
  confidence        REAL NOT NULL,
  requested_at      REAL NOT NULL,
  resolved_at       REAL,
  metadata_json     TEXT DEFAULT '{}',
  FOREIGN KEY (source_entity_id) REFERENCES entities(id),
  FOREIGN KEY (target_entity_id) REFERENCES entities(id)
);

CREATE INDEX IF NOT EXISTS idx_entity_merges_status
  ON entity_merges(tenant_id, status);

-- Graph groupings — operator-defined VIEW-ONLY associations for the /memory
-- canvas. Lets the operator cluster nodes (e.g. "kazma_app belongs under
-- kazma") and tier them (main/major/sub/leaf) for tree layout + per-tier
-- colors WITHOUT mutating beliefs. recall/extraction NEVER read this table;
-- it is purely advisory for the operator graph. See
-- docs/plans/MEMORY_GRAPH_GROUPING_PLAN.md.
CREATE TABLE IF NOT EXISTS graph_associations (
  id            TEXT PRIMARY KEY,                       -- assoc_<uuid>
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  group_root    TEXT NOT NULL,                          -- parent node id (entity id or virtual-fact text)
  member        TEXT NOT NULL,                          -- grouped node id
  member_tier   INTEGER DEFAULT 1,                      -- 0=main(hub),1=major,2=sub,3=leaf
  label         TEXT,                                   -- optional operator note
  created_at    REAL NOT NULL,
  created_by    TEXT DEFAULT 'operator',
  metadata_json TEXT DEFAULT '{}',
  UNIQUE(tenant_id, group_root, member)
);
CREATE INDEX IF NOT EXISTS idx_graph_assoc_root
  ON graph_associations(tenant_id, group_root);
CREATE INDEX IF NOT EXISTS idx_graph_assoc_member
  ON graph_associations(tenant_id, member);

-- Procedural memory — parametric action DAGs learned from tool execution.
CREATE TABLE IF NOT EXISTS procedural_dags (
  id                      TEXT PRIMARY KEY,
  tenant_id               TEXT NOT NULL DEFAULT 'default',
  name                    TEXT NOT NULL,
  description             TEXT NOT NULL,

  precond_signature_hash  TEXT NOT NULL,                     -- SHA256 of canonical precondition AST
  preconditions_json      TEXT NOT NULL,
  dag_steps_json          TEXT NOT NULL,                     -- parametric steps with $SLOT
  postconditions_json     TEXT NOT NULL,

  success_count           INTEGER DEFAULT 0,
  total_trials            INTEGER DEFAULT 0,
  confidence_score        REAL DEFAULT 0.5,                  -- Laplace smoothed C(d)=(S+1)/(N+2)
  status                  TEXT NOT NULL DEFAULT 'active',    -- active|quarantine|retired

  created_at              REAL NOT NULL,
  last_executed           REAL,
  metadata_json           TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_procedural_hash
  ON procedural_dags(tenant_id, precond_signature_hash);

-- Cold storage for superseded beliefs older than the archive threshold.
CREATE TABLE IF NOT EXISTS beliefs_archive (
  id                  TEXT PRIMARY KEY,
  tenant_id           TEXT NOT NULL,
  original_belief_json TEXT NOT NULL,
  archived_at         REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_beliefs_archive_tenant
  ON beliefs_archive(tenant_id, archived_at);
""" % (_DEFAULT_EMBEDDING_MODEL, _DEFAULT_EMBEDDING_MODEL)


# ── Operational DDL (memory_ops.db) ───────────────────────────────────────

OPS_DDL = """
-- Durable task queue — replaces volatile in-memory loop.create_task dispatch.
-- Survives crashes: pending rows are reclaimed on worker restart.
CREATE TABLE IF NOT EXISTS memory_task_queue (
  id            TEXT PRIMARY KEY,
  task_type     TEXT NOT NULL,                               -- micro_consolidation|entity_merge|macro_sleep
  payload_json  TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',             -- pending|processing|completed|failed
  attempts      INTEGER DEFAULT 0,
  max_attempts  INTEGER DEFAULT 3,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  error_log     TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_pending
  ON memory_task_queue(status, created_at) WHERE status = 'pending';

-- Immutable memory audit log — every belief mutation, merge, decay, promote.
CREATE TABLE IF NOT EXISTS memory_audit_log (
  id                  TEXT PRIMARY KEY,
  tenant_id           TEXT NOT NULL DEFAULT 'default',
  timestamp           REAL NOT NULL,
  event_type          TEXT NOT NULL,                         -- supersede|transition|merge|quarantine|decay|promote
  target_table        TEXT NOT NULL,
  target_id           TEXT NOT NULL,
  actor               TEXT NOT NULL,                         -- post_turn_worker|macro_sleep_job|user_override
  reason              TEXT NOT NULL,
  state_before_json   TEXT,
  state_after_json    TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_time
  ON memory_audit_log(tenant_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_audit_event_type
  ON memory_audit_log(event_type, timestamp);
"""


# ── Schema initialization ─────────────────────────────────────────────────


def ensure_primary_schema(conn: Any) -> None:
    """Create all primary cognitive-state tables on a sync connection.

    Idempotent (``CREATE TABLE IF NOT EXISTS``). Applies WAL pragmas via
    the shared helper. Safe to call on every boot.
    """
    from kazma_core.config_store import apply_sqlite_pragmas

    apply_sqlite_pragmas(conn)
    conn.executescript(PRIMARY_DDL)
    # ── Idempotent column additions for embeddings ───────────────────
    # The VectorEngine reads episode embeddings from a dedicated BLOB
    # column (NOT metadata_json — bytes are not JSON-serializable and
    # stuffing them there was a latent bug). Added via ALTER so existing
    # DBs upgrade in place, matching the schema.py convention.
    for col_sql in (
        "ALTER TABLE episodes ADD COLUMN embedding BLOB",
        "ALTER TABLE beliefs ADD COLUMN embedding BLOB",
        # Phase A: access accounting on beliefs (episodes already have these columns)
        "ALTER TABLE beliefs ADD COLUMN access_count INTEGER DEFAULT 0",
        "ALTER TABLE beliefs ADD COLUMN last_accessed REAL",
        # Phase 3: materialized belief_count / graph_degree on entities so the
        # operator /memory page avoids per-row correlated subqueries. Sentinel
        # -1 = "not computed yet" (distinct from a genuine 0). Backfilled
        # below; maintained on write by entity_counts.recompute_entity_counts.
        "ALTER TABLE entities ADD COLUMN belief_count INTEGER DEFAULT -1",
        "ALTER TABLE entities ADD COLUMN graph_degree INTEGER DEFAULT -1",
        # F3: per-entity protection flag — operator-marked undeletable and
        # unmergeable-as-source. Extends the hardcoded _PROTECTED_ENTITIES set
        # (user/assistant/kazma/mubder) to any entity the operator chooses.
        "ALTER TABLE entities ADD COLUMN is_protected INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(col_sql)
        except Exception:
            pass  # column already exists
    # Phase B: real FTS5 indexes (MATCH + bm25) with LIKE fallback in recall.
    _ensure_fts5(conn)
    # Phase 3: one-shot backfill of stale entity counts on first boot after
    # the columns are added (rows default to -1). Idempotent — only touches
    # rows still at the sentinel. Safe to re-run; cheap when nothing's stale.
    _backfill_entity_counts(conn)
    # FK enforcement must be set per-connection in SQLite.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    logger.debug("[schema_v2] primary schema ensured")


def _backfill_entity_counts(conn: Any) -> None:
    """Compute belief_count/graph_degree for rows still at the -1 sentinel.

    Runs once per DB on first boot after the Phase 3 columns are added. The
    per-entity subqueries are the same shape the read path falls back to when
    a count is stale; with the Phase 3 partial indexes (idx_beliefs_active_*)
    these are index scans over active rows only. Best-effort: never raises.
    """
    try:
        stale = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE belief_count = -1"
        ).fetchone()[0]
        if not stale:
            return
        logger.info("[schema_v2] backfilling %d stale entity counts", stale)
        from kazma_core.memory.entity_counts import recompute_entity_counts

        stale_ids = [
            str(r[0])
            for r in conn.execute("SELECT id FROM entities WHERE belief_count = -1 LIMIT 5000").fetchall()
        ]
        # Recompute in batches to bound transaction size on large memories.
        for i in range(0, len(stale_ids), 500):
            recompute_entity_counts(conn, stale_ids[i : i + 500])
        conn.commit()
    except Exception:
        logger.debug("[schema_v2] entity count backfill skipped", exc_info=True)


def _ensure_fts5(conn: Any) -> None:
    """Create episodes_fts + beliefs_fts + entities_fts and keep them in sync.

    External-content FTS5 over ``episodes`` / ``beliefs`` / ``entities`` so
    lexical search uses ``MATCH`` + ``bm25()`` instead of multi-term ``LIKE``.
    Idempotent. On first create (or empty index with existing rows) runs a
    rebuild. ``entities_fts`` indexes name + type + aliases_json so the
    operator entity search matches display names AND aliases (the JSON array
    string tokenizes to its members under unicode61).
    """
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
                user_text,
                assistant_text,
                summary_text,
                content='episodes',
                content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS beliefs_fts USING fts5(
                subject,
                predicate,
                object,
                content='beliefs',
                content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
                name,
                type,
                aliases_json,
                content='entities',
                content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
    except Exception:
        logger.debug("[schema_v2] FTS5 create failed (engine may lack fts5)", exc_info=True)
        return

    # Sync triggers — external content tables require explicit maintainence.
    for sql in (
        """
        CREATE TRIGGER IF NOT EXISTS episodes_fts_ai AFTER INSERT ON episodes BEGIN
          INSERT INTO episodes_fts(rowid, user_text, assistant_text, summary_text)
          VALUES (new.rowid, new.user_text, new.assistant_text, new.summary_text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS episodes_fts_ad AFTER DELETE ON episodes BEGIN
          INSERT INTO episodes_fts(episodes_fts, rowid, user_text, assistant_text, summary_text)
          VALUES ('delete', old.rowid, old.user_text, old.assistant_text, old.summary_text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS episodes_fts_au AFTER UPDATE ON episodes BEGIN
          INSERT INTO episodes_fts(episodes_fts, rowid, user_text, assistant_text, summary_text)
          VALUES ('delete', old.rowid, old.user_text, old.assistant_text, old.summary_text);
          INSERT INTO episodes_fts(rowid, user_text, assistant_text, summary_text)
          VALUES (new.rowid, new.user_text, new.assistant_text, new.summary_text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS beliefs_fts_ai AFTER INSERT ON beliefs BEGIN
          INSERT INTO beliefs_fts(rowid, subject, predicate, object)
          VALUES (new.rowid, new.subject, new.predicate, new.object);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS beliefs_fts_ad AFTER DELETE ON beliefs BEGIN
          INSERT INTO beliefs_fts(beliefs_fts, rowid, subject, predicate, object)
          VALUES ('delete', old.rowid, old.subject, old.predicate, old.object);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS beliefs_fts_au AFTER UPDATE ON beliefs BEGIN
          INSERT INTO beliefs_fts(beliefs_fts, rowid, subject, predicate, object)
          VALUES ('delete', old.rowid, old.subject, old.predicate, old.object);
          INSERT INTO beliefs_fts(rowid, subject, predicate, object)
          VALUES (new.rowid, new.subject, new.predicate, new.object);
        END
        """,
        # entities: aliases_json is indexed as an FTS column so the JSON array
        # string tokenizes to its members (e.g. ["Mubder","You"] → mubder, you).
        """
        CREATE TRIGGER IF NOT EXISTS entities_fts_ai AFTER INSERT ON entities BEGIN
          INSERT INTO entities_fts(rowid, name, type, aliases_json)
          VALUES (new.rowid, new.name, new.type, new.aliases_json);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS entities_fts_ad AFTER DELETE ON entities BEGIN
          INSERT INTO entities_fts(entities_fts, rowid, name, type, aliases_json)
          VALUES ('delete', old.rowid, old.name, old.type, old.aliases_json);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS entities_fts_au AFTER UPDATE ON entities BEGIN
          INSERT INTO entities_fts(entities_fts, rowid, name, type, aliases_json)
          VALUES ('delete', old.rowid, old.name, old.type, old.aliases_json);
          INSERT INTO entities_fts(rowid, name, type, aliases_json)
          VALUES (new.rowid, new.name, new.type, new.aliases_json);
        END
        """,
    ):
        try:
            conn.execute(sql)
        except Exception:
            logger.debug("[schema_v2] FTS trigger create failed", exc_info=True)

    # Rebuild when index is empty but base table has rows (upgrade path).
    try:
        ep_n = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        fts_n = conn.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()[0]
        if ep_n and not fts_n:
            conn.execute("INSERT INTO episodes_fts(episodes_fts) VALUES('rebuild')")
            logger.info("[schema_v2] rebuilt episodes_fts (%d rows)", ep_n)
    except Exception:
        logger.debug("[schema_v2] episodes_fts rebuild skipped", exc_info=True)
    try:
        bel_n = conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
        fts_n = conn.execute("SELECT COUNT(*) FROM beliefs_fts").fetchone()[0]
        if bel_n and not fts_n:
            conn.execute("INSERT INTO beliefs_fts(beliefs_fts) VALUES('rebuild')")
            logger.info("[schema_v2] rebuilt beliefs_fts (%d rows)", bel_n)
    except Exception:
        logger.debug("[schema_v2] beliefs_fts rebuild skipped", exc_info=True)
    try:
        ent_n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        fts_n = conn.execute("SELECT COUNT(*) FROM entities_fts").fetchone()[0]
        if ent_n and not fts_n:
            conn.execute("INSERT INTO entities_fts(entities_fts) VALUES('rebuild')")
            logger.info("[schema_v2] rebuilt entities_fts (%d rows)", ent_n)
    except Exception:
        logger.debug("[schema_v2] entities_fts rebuild skipped", exc_info=True)


def ensure_ops_schema(conn: Any) -> None:
    """Create all operational tables on a sync connection.

    Idempotent. Separate from :func:`ensure_primary_schema` so the ops DB
    can be initialized independently (e.g. by the task queue worker).
    """
    from kazma_core.config_store import apply_sqlite_pragmas

    apply_sqlite_pragmas(conn)
    conn.executescript(OPS_DDL)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    logger.debug("[schema_v2] ops schema ensured")
