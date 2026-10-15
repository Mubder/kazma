"""Every store that can hold memory: what Forget does there, and what Export says.

Memory audit M01 (2026-10-13). Forget reached the episode and the fact's
value column and nothing else: the fact's vector, its operator-edit history,
its archived copy, the audit log's copy of the value, a queued turn's text,
the legacy tables, and ``unified_items`` -- a copy of every fact and every
Knowledge chunk that federated search served, with no delete path at all.
"Forget removes the memory and the facts it learned from it" was true of one
row in each.

This is the one list. Each memory table of ``memory_state.db`` and
``memory_ops.db`` (and each store outside them that holds memory) declares:

- ``holds``: what kind of text it keeps;
- ``forget``: what Forget does there (the function), or why there is nothing;
- ``export``: the section of the memory export it appears in, or why not
  (M22 derives the export from this list);
- ``retention`` and ``bound``: how long it is kept and what limits its size.

``tests/test_memory_store_registry.py`` fails on a table the schema creates
that is not here, and on an entry no schema creates; the forget-completeness
test there plants a token through the product writers, forgets, and scans
every text column of both databases.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ERASURE_HORIZON",
    "EXTERNAL_STORES",
    "MEMORY_TABLES",
    "MemoryStore",
    "exported_sections",
]


@dataclass(frozen=True)
class MemoryStore:
    """One store that can hold memory, and how each promise reaches it."""

    name: str
    #: ``state`` (memory_state.db), ``ops`` (memory_ops.db), ``journal``
    #: (memory_forget_journal.db), ``legacy`` (a table only older installs
    #: have) or ``external``.
    where: str
    #: ``forget`` (Forget erases what the turn put there), ``index`` (a
    #: search index Forget rebuilds), ``no_turn_text`` (nothing a turn
    #: said), ``retention`` (kept for its retention, by design) or
    #: ``retired``.
    erase: str
    #: ``turn_text`` / ``fact_text`` / ``derived_text`` / ``user_text`` /
    #: ``operator_text`` / ``names_only`` / ``ids_only`` / ``retired``.
    holds: str
    forget: str
    #: The export section, or "" with ``excluded`` saying why.
    export: str
    excluded: str = ""
    retention: str = "kept"
    bound: str = ""


MEMORY_TABLES: tuple[MemoryStore, ...] = (
    # ── memory_state.db ────────────────────────────────────────────────────
    MemoryStore(
        "episodes", "state", "forget", "turn_text",
        forget="forget._tombstone: question, answer, summary emptied, vector dropped, tier "
               "'forgotten'; every copy of the turn and the agent's notes from it",
        export="memories", retention="kept (archival is cold storage, never deletion)",
        bound="one row per turn plus the agent's notes",
    ),
    MemoryStore(
        "episodes_fts", "state", "index", "derived_text",
        forget="the trigger re-indexes the emptied row; forget.purge_search_index merges the "
               "index so no deleted word stays in its segments",
        export="", excluded="search index over episodes, rebuilt from them",
        bound="as episodes",
    ),
    MemoryStore(
        "beliefs", "state", "forget", "fact_text",
        forget="forget._forget_fact_rows: no longer current, value, vector and every metadata "
               "field but provenance emptied; the remote vector deleted (forget._after_facts)",
        export="facts", retention="kept as history; superseded facts archived after 180 days",
        bound="facts per subject; history rows per functional (subject, predicate)",
    ),
    MemoryStore(
        "beliefs_fts", "state", "index", "derived_text",
        forget="the trigger re-indexes the emptied row; forget.purge_search_index merges the "
               "index so no deleted word stays in its segments",
        export="", excluded="search index over beliefs, rebuilt from them",
        bound="as beliefs",
    ),
    MemoryStore(
        "beliefs_archive", "state", "forget", "fact_text",
        forget="forget._forget_archived_facts: the archived JSON keeps no value, vector or metadata "
               "but provenance",
        export="archived_facts", retention="kept",
        bound="facts superseded more than 180 days ago, and the operator's archive of invalidated facts",
    ),
    MemoryStore(
        "entities", "state", "forget", "names_only",
        forget="an entity goes with its last fact (entity_retire); a name another fact still uses stays",
        export="entities", bound="one row per subject or object name",
    ),
    MemoryStore(
        "entities_fts", "state", "index", "derived_text",
        forget="the trigger drops a retired entity; forget.purge_search_index merges the index",
        export="", excluded="search index over entities, rebuilt from them",
        bound="as entities",
    ),
    MemoryStore(
        "entity_merges", "state", "no_turn_text", "ids_only",
        forget="nothing to forget: entity ids and the merge decision",
        export="entity_merges",
        bound="one row per merge candidate",
    ),
    MemoryStore(
        "entity_merges_archive", "state", "no_turn_text", "ids_only",
        forget="nothing to forget: entity ids and the merge decision",
        export="entity_merges",
        bound="one row per decided candidate",
    ),
    MemoryStore(
        "graph_associations", "state", "no_turn_text", "operator_text",
        forget="nothing a turn wrote: the operator's own groupings of entities",
        export="graph_groupings", bound="the operator's groupings",
    ),
    MemoryStore(
        "memory_forgotten", "state", "no_turn_text", "ids_only",
        forget="the ledger itself: chat keys, turn numbers and question hashes, never text",
        export="forgotten", bound="one row per forgotten turn per chat key",
    ),
    MemoryStore(
        "memory_mirror_outbox", "state", "no_turn_text", "ids_only",
        forget="nothing to forget: the kind and id of a row changed since the last mirror pass",
        export="", excluded="the Postgres mirror's change queue (state_backend.sync_state_mirror)",
        retention="until the mirror pass pushes the row (5 failed tries at most); emptied with no mirror",
        bound="rows changed since the last pass; while Postgres is away, every change since",
    ),
    MemoryStore(
        "memory_forget_applied", "state", "no_turn_text", "ids_only",
        forget="the forget journal's bookkeeping: which journal entries this copy of the database applied",
        export="", excluded="bookkeeping of the forget journal (memory_forget_journal.db)",
        bound="one row per journal entry",
    ),
    MemoryStore(
        "memory_reconcile_state", "state", "no_turn_text", "ids_only",
        forget="turn reconcile's cursor: chat ids and times, no text",
        export="", excluded="bookkeeping of turn reconcile (where it has read the chat store up to)",
        bound="one row",
    ),
    MemoryStore(
        "memory_profile", "state", "no_turn_text", "user_text",
        forget="nothing to forget: the user's own 'About me', which they edit",
        export="about_me", bound="one row per tenant, 2,000 characters",
    ),
    MemoryStore(
        "memory_summaries", "state", "forget", "derived_text",
        forget="topic_summaries.on_turns_forgotten: emptied, written again without the turn",
        export="weekly_summaries", retention="kept; a forgotten summary stays a tombstone",
        bound="one per topic per week",
    ),
    MemoryStore(
        "memory_summary_periods", "state", "no_turn_text", "ids_only",
        forget="nothing to forget: a week's status and counts",
        export="", excluded="the summary pipeline's per-week bookkeeping",
        bound="one row per tenant per week",
    ),
    MemoryStore(
        "memory_summary_sources", "state", "no_turn_text", "ids_only",
        forget="nothing to forget: summary and episode ids",
        export="weekly_summaries", bound="one row per summarized turn",
    ),
    MemoryStore(
        "procedural_dags", "state", "no_turn_text", "names_only",
        forget="nothing a turn wrote: tool names and argument NAMES (values were stripped "
               "2026-10-13, procedural.strip_argument_values)",
        export="procedures", bound="one row per tool and argument shape",
    ),
    # ── memory_ops.db ──────────────────────────────────────────────────────
    MemoryStore(
        "memory_audit_log", "ops", "forget", "fact_text",
        forget="forget._redact_audit: the event stays, the forgotten values become a hash",
        export="fact_changes",
        retention="kept", bound="one row per fact write",
    ),
    MemoryStore(
        "memory_task_queue", "ops", "forget", "turn_text",
        forget="forget._scrub_queue: a queued turn the user forgot loses its question and answer",
        export="", excluded="work in flight, not memory",
        retention="finished tasks purged after 7 days", bound="pending tasks plus 7 days of finished ones",
    ),
    MemoryStore(
        "commitments", "ops", "retention", "turn_text",
        forget="not reached: the safety layer's decision record (what was authorized, §20), "
               "kept for its retention like the chat keeps the conversation",
        export="", excluded="the commitment layer's decisions, not memory",
        retention="tiered commitment GC (§20)", bound="one row per gated act",
    ),
    MemoryStore(
        "commitment_events", "ops", "retention", "ids_only",
        forget="not reached: events of a commitment decision",
        export="", excluded="the commitment layer's decisions, not memory",
        retention="with their commitment", bound="events per commitment",
    ),
    MemoryStore(
        "unified_items", "ops", "retired", "retired",
        forget="retired 2026-10-13: dropped by schema_v2.ensure_ops_schema (it copied every fact "
               "and Knowledge chunk, had no delete path, and federated search served it)",
        export="", excluded="retired", retention="dropped", bound="none",
    ),
    # ── memory_forget_journal.db (memory/forget_journal.py) ────────────────
    MemoryStore(
        "forget_journal", "journal", "no_turn_text", "ids_only",
        forget="the journal itself: each forget, keep-out and summary forget, in order -- chat keys, "
               "turn numbers, question hashes and ids, never text",
        export="", excluded="the forget journal; the export's 'forgotten' section shows the ledger",
        retention="append-only", bound="one row per forget the user makes",
    ),
    MemoryStore(
        "forget_remote_pending", "journal", "no_turn_text", "ids_only",
        forget="remote vectors of forgotten memories still to delete, retried every pass",
        export="", excluded="work in flight", retention="until deleted",
        bound="memories forgotten while the remote index was unreachable",
    ),
    # ── tables only installs from before 2026-08 have ──────────────────────
    MemoryStore(
        "episodes_archive", "legacy", "forget", "turn_text",
        forget="forget._scrub_legacy_copies: by id, and by question under another id",
        export="", excluded="restored into 'episodes' by legacy_tables (a copy of what is there)",
        bound="329 rows on the one install that has it, never written",
    ),
    MemoryStore(
        "entities_archive", "legacy", "forget", "turn_text",
        forget="forget._scrub_legacy_copies: a 200-character truncation of a forgotten memory",
        export="", excluded="junk nodes and truncations of memories held in full",
        bound="326 rows on the one install that has it, never written",
    ),
)

#: Stores outside the two memory databases that hold memory.
EXTERNAL_STORES: tuple[MemoryStore, ...] = (
    MemoryStore(
        "forget journal (memory_forget_journal.db)", "external", "no_turn_text", "ids_only",
        forget="appended to first: each forget, keep-out and summary forget, replayed over a restored "
               "copy of the memory database (forget_journal.apply_pending)",
        export="", excluded="ids and question hashes; the export's 'forgotten' section shows the ledger",
    ),
    MemoryStore(
        "remote vector index", "external", "forget", "derived_text",
        forget="backend.delete for every forgotten memory and fact id (forget._delete_remote_vector)",
        export="", excluded="vectors, rebuilt from the memories",
    ),
    MemoryStore(
        "Postgres state mirror (kazma_episodes, kazma_beliefs)", "external", "forget", "turn_text",
        forget="state_backend.remirror_*: the tombstone is mirrored; the mirror sync repairs a miss",
        export="", excluded="a mirror of the local store",
    ),
    MemoryStore(
        "graph backend (Neo4j edges)", "external", "forget", "fact_text",
        forget="hygiene.invalidate_belief removes a forgotten fact's edge",
        export="", excluded="current facts as edges, rebuilt from them",
    ),
    MemoryStore(
        "time-travel snapshots (snapshots.db)", "external", "forget", "turn_text",
        forget="forget._forget_snapshots: forgetting a turn deletes the chat's steps from that turn "
               "on (each holds the conversation up to it), forgetting the chat all of them, under "
               "every key (time_travel.forget_turn_snapshots / forget_thread_snapshots, TT-05); a "
               "rewind drops the steps after the restored one (kazma_ui.thread_rewind). Not backed "
               "up or carried in a migration (store_registry), so no restore brings one back",
        export="", excluded="the chat's state, not memory",
        retention="time_travel.retention_days (30) and the per-chat and total budgets",
        bound="50 steps a chat, 2,000 and 512 MB in all (time_travel.DEFAULT_*)",
    ),
    MemoryStore(
        "chat store and checkpoints", "external", "retention", "turn_text",
        forget="not memory: forgetting a memory does not delete the conversation (§15I)",
        export="", excluded="the conversation itself",
    ),
)

#: Copies Forget cannot reach, said next to the Forget button and in the guide.
ERASURE_HORIZON: tuple[tuple[str, str], ...] = (
    ("native memory backups", "10 generations, every 6 hours"),
    ("universal backups", "7 generations"),
    ("offsite restic snapshots", "up to 12 monthly"),
    ("nightly memory exports (exports/)", "replaced every 6 hours"),
    ("the copy kept before an embedding rebuild (memory_state.db.pre_reembed)", "until the next rebuild"),
    ("the copies the operator's repair scripts keep before changing the database "
     "(memory_state.db.pre_entity_key, memory_state.db.pre_relabel)", "until the operator removes them"),
    ("space SQLite freed in the memory databases and their write-ahead logs", "until SQLite reuses it"),
)

#: Tables SQLite keeps for itself.
_SQLITE_INTERNAL = ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4")
#: An FTS5 table's shadow tables belong to it.
_FTS_SHADOW = ("_data", "_idx", "_config", "_docsize", "_content")


def _table_entry(name: str) -> MemoryStore | None:
    """The entry for a table of either memory database, an FTS shadow table
    under its index's entry; None when it is not declared."""
    if name in _SQLITE_INTERNAL:
        return MemoryStore(name, "state", "no_turn_text", "ids_only", forget="SQLite's own", export="",
                           excluded="SQLite's own")
    for entry in MEMORY_TABLES:
        if entry.name == name:
            return entry
    for suffix in _FTS_SHADOW:
        if name.endswith(suffix):
            return _table_entry(name[: -len(suffix)])
    return None


def exported_sections() -> set[str]:
    """Every section the memory export must have (M22)."""
    return {e.export for e in MEMORY_TABLES if e.export}
