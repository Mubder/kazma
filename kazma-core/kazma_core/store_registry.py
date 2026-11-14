"""Kazma's own stores, declared once — and how the model reads each back.

Born 2026-09-25. Asked to "list the remaining posts", the agent spent 67
tool calls and an operator-approved ``python_exec`` byte-dumping
``agent_artifacts.db``: it could SAVE drafts (``save_proposal``) but had no
tool to READ them, and every direct route to its own database is refused.
The SQL tool refused and offered nothing; ``file_read`` returned the raw
SQLite bytes; ``python_exec`` let it through because its list of store
names had never heard of ``agent_artifacts.db``.

That exposed one decision kept as five hand-written answers that had
drifted apart — "is this file one of Kazma's stores?":

- the SQL tool's refusal list (15 names),
- path policy's name list (derived from ``kazma_core.paths``, 12 names),
  behind the approval-card disclosure and the ``python_exec`` refusal,
- path policy's location rule (any database under the data dir),
- the migration exporter's list of files to carry (14 names),
- the migration importer's map of files to restore.

This module is the one answer, in three tables:

``STORES``
    Every database Kazma keeps: what it holds and whether it travels in a
    ``kazma migrate`` bundle. ``tests/test_store_registry.py`` fails when
    product code names a database file that is not declared here.

``DATA_DIR_ENTRIES``
    Everything else Kazma keeps directly in the data dir, and whether a
    bundle carries it (:func:`carried_in_bundle`). Anything undeclared there
    is the operator's own and travels.

``TOOL_WRITES``
    Every tool that can change anything: where its writes land and which
    tools (or which context feeder) give the model that data back. A write
    to a Kazma store MUST name a reader the model can call without an
    approval — no note excuses it. That rule is per WRITER, not per store:
    ``agent_artifacts.db`` always had a reader (the scratchpad is fed back
    into context every turn), and that is exactly why a per-store rule
    would have passed while the drafts half had none.

Every door the model might try — the SQL tools, the file tools,
``python_exec``, ``shell_exec``, the approval card — asks
:func:`is_kazma_store` and answers with :func:`store_refusal`, which names
the reader. A refusal that says only "no" is what sent the model digging.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CONTEXT_FEEDERS",
    "DATA_DIR_ENTRIES",
    "DYNAMIC_NAME_SITES",
    "MIGRATION_DISPOSITIONS",
    "NOT_BACKED_UP",
    "STORES",
    "STORE_FAMILIES",
    "TOOL_WRITES",
    "DataDirEntry",
    "Store",
    "Write",
    "bundle_family_patterns",
    "bundle_store_names",
    "carried_in_bundle",
    "distinctive_store_names",
    "is_kazma_secret_file",
    "is_kazma_store",
    "is_secret_file_name",
    "readers_for_store",
    "store_name_for",
    "store_refusal",
]

logger = logging.getLogger(__name__)

#: How a store crosses machines in a ``kazma migrate`` bundle.
MIGRATION_DISPOSITIONS = {
    "bundle": "copied into the bundle and restored on import",
    "settings": "carried inside the settings export (config.yaml + tables)",
    "documents": "carried with the document store export",
    "rebuilt": "not carried: rebuilt from other data or on demand",
    "machine": "not carried: only meaningful on this machine",
    "legacy": "not carried: nothing current writes it",
}


@dataclass(frozen=True)
class Store:
    """One of Kazma's databases."""

    holds: str
    migration: str
    #: Why, whenever migration is not "bundle".
    reason: str = ""
    #: Generic filename (``sessions.db``): recognised by LOCATION only, never
    #: by bare name — a user's own ``sessions.db`` must not trip anything.
    ambiguous_name: bool = False


STORES: dict[str, Store] = {
    # ── the agent's own working state ──────────────────────────────────
    "agent_artifacts.db": Store(
        "saved drafts (save_proposal) and scratchpad findings", "bundle"),
    "task_ledgers.db": Store(
        "task ledgers: each chat's goal, plan steps and findings", "bundle"),
    "tool_effects.db": Store(
        "graph tool invocation receipts and interrupted effects", "bundle"),
    "memory_state.db": Store(
        "long-term memory: beliefs, entities and episodes", "bundle"),
    "memory_ops.db": Store(
        "the memory task queue and the memory audit log", "bundle"),
    "memory_forget_journal.db": Store(
        "every forget, keep-out and summary forget, in order: ids and hashes, never text", "bundle"),
    "knowledge_graph.db": Store("the swarm memory property graph", "bundle"),
    "checkpoints.db": Store("conversation state checkpoints", "bundle"),
    # Time Travel audit TT-05 (2026-10-14): not carried, not backed up
    # (NOT_BACKED_UP below). It was a bundle store and two thirds of a
    # universal backup by volume.
    "snapshots.db": Store(
        "time-travel snapshots of the agent's state", "machine",
        reason="a 30-day view of this machine's chats, each step a full plaintext copy of the "
               "agent's state (tool results and secrets included); a copy elsewhere would bring "
               "back chats deleted or forgotten since. The chats and checkpoints travel"),
    "time_travel_audit.db": Store(
        "the Time Travel audit trail: who restored, forked or cleared which chat (ids, no text)",
        "bundle"),
    # ── conversations and sessions ─────────────────────────────────────
    "chat_sessions.db": Store("chat transcripts", "bundle"),
    "chat_sessions_spool.db": Store(
        "chat saves the primary transcript store refused", "bundle"),
    "sessions.db": Store(
        "chat-platform session routing (chat ids)", "bundle", ambiguous_name=True),
    # ── scheduling, tasks, research ────────────────────────────────────
    "cron.db": Store(
        "scheduled tasks and reminders", "bundle", ambiguous_name=True),
    "swarm_tasks.db": Store("swarm tasks and their results", "bundle"),
    "pipeline_logs.db": Store("swarm pipeline logs", "bundle"),
    "research_sessions.db": Store("deep-research sessions", "bundle"),
    # ── X ──────────────────────────────────────────────────────────────
    "x_posts.db": Store(
        "the X post ledger: what was posted (duplicate and cap checks)", "bundle"),
    "x_scheduled.db": Store("scheduled X posts", "bundle"),
    "x_replies.db": Store("the X auto-reply queue", "bundle"),
    "x_datasets.db": Store("X human annotation collections, recorded outcomes and split provenance", "bundle"),
    "x_audit.db": Store("the X API audit log", "bundle"),
    "x_publications.db": Store("X publication operations, quota reservations, decisions and projection repairs", "bundle"),
    # ── control plane: decisions, secrets, permissions, evidence ───────
    "hitl_gates.db": Store(
        "approval decisions (the gate registry)", "bundle"),
    "run_incidents.db": Store(
        "stuck or failed runs: what the owner was told and what they decided", "bundle"),
    "vault.db": Store("encrypted secrets", "bundle"),
    "settings.db": Store(
        "settings, workspaces and bookmarks", "settings",
        reason="exported as config.yaml plus its workspace table",
        ambiguous_name=True),
    "knowledge.db": Store(
        "the Knowledge Library: libraries, chunks and their keyword index "
        "(in settings.db until 2026-10-14, Knowledge audit KB-E10)", "bundle"),
    "rbac.db": Store("roles and permissions", "bundle"),
    "audit.db": Store("the audit log", "bundle", ambiguous_name=True),
    "security_audit.db": Store(
        "the retired security audit trail", "legacy",
        reason="nothing ever wrote to it; removed 2026-10-02 (the security report reads "
               "the approval registry); a leftover file is not carried"),
    "disclosure.db": Store("security disclosure reports", "bundle"),
    "certifications.db": Store("skill certifications", "bundle"),
    "llm_calls.db": Store(
        "the model-call ledger: tokens, cost and latency", "bundle"),
    "answer_quality.db": Store(
        "bounded answer-quality flags and turn identity (administrator dashboard; no answer text)", "bundle"),
    # ── documents and mail ─────────────────────────────────────────────
    "documents.db": Store(
        "the document catalog", "documents",
        reason="exported with the document store (migration/exporter.py)",
        ambiguous_name=True),
    "documents-jobs-fallback.db": Store(
        "document jobs when Postgres is unavailable", "documents",
        reason="lives under the document store root, exported with it"),
    "sandbox_emails.db": Store(
        "the sandbox mailbox used when no email account is connected", "bundle"),
    # ── caches and per-machine state ───────────────────────────────────
    "semantic_cache.db": Store(
        "the retired semantic LLM response cache", "legacy",
        reason="removed 2026-09-30 (audit AUD-001: it replayed one turn's answer and "
               "tool calls for later requests); a leftover file is not carried"),
    "security_scan.db": Store(
        "the retired dependency scan history", "legacy",
        reason="nothing read it; the scanner keeps no history since 2026-10-02, a "
               "leftover file is not carried"),
    "file_checkpoints.db": Store(
        "IDE undo checkpoints of edited files", "machine",
        reason="undo history names this machine's absolute file paths"),
    "callbacks.db": Store(
        "chat-platform button callback tokens", "machine",
        reason="tokens expire within a day"),
    "chat_inbox.db": Store(
        "chat-app messages kept while Kazma ran read-only with its database away", "machine",
        reason="kept only until the next normal start answers them; the rows name this "
               "machine's chat-app addresses"),
    "chat_outbox.db": Store(
        "chat-app replies not yet taken by their platform (the chat outbox)", "machine",
        reason="a reply is kept at most two hours, then its text is emptied; the rows "
               "name this machine's chat-app addresses"),
    "registry.db": Store(
        "the skills hub registry (user level, outside the data dir)", "machine",
        reason="user-level hub state, not install data", ambiguous_name=True),
    "vector.db": Store(
        "the swarm memory sqlite-vec index", "rebuilt",
        reason="an index over memory; rebuilt from the memory stores",
        ambiguous_name=True),
    # In vector_memory/: Chroma names the file, Kazma starts the client
    # (memory/vector_store_global.py). Left out of backups for the same reason.
    "chroma.sqlite3": Store(
        "the Knowledge Library's meaning vectors (Chroma, in vector_memory/)", "rebuilt",
        reason="re-embedded from the Knowledge Library's chunks by the "
               "knowledge vector repair sweep",
        ambiguous_name=True),
    "workspaces.db": Store(
        "the workspaces table, as named inside a migration bundle", "settings",
        reason="a bundle-internal copy of settings.db's workspaces table"),
    # ── retired ────────────────────────────────────────────────────────
    "memory.db": Store(
        "the retired V1 FTS5 memory", "legacy",
        reason="read once by the V1→V2 backfill; nothing writes it",
        ambiguous_name=True),
    "kazma.db": Store(
        "an early storage file", "legacy", reason="created, never written"),
    "ops.db": Store(
        "an early operations file", "legacy", reason="created, never written",
        ambiguous_name=True),
    "config.db": Store(
        "an early config file (fallback path only)", "legacy",
        reason="only named on a fallback path when kazma_home is unknown",
        ambiguous_name=True),
    "chat_sessions_test.db": Store(
        "the transcript store of test mode", "machine",
        reason="test mode only; a real install never writes it"),
}

#: Databases the universal backup leaves out, and why
#: (``backup.universal.selectable_databases``). Their disposition above says
#: they do not travel either.
NOT_BACKED_UP: dict[str, str] = {
    "snapshots.db": (
        "Time Travel steps are a 30-day view of the chats; a backup would bring back the "
        "steps of a chat deleted or forgotten since (TT-05), and they were two thirds of a "
        "backup by volume"
    ),
}

#: Stores whose file names are built at runtime, by glob pattern (relative to
#: the data dir). Matched by :func:`store_name_for` like a declared name.
STORE_FAMILIES: dict[str, Store] = {
    "checkpoints_*.db": Store(
        "per-tenant conversation checkpoints (gateway)", "bundle"),
    "code-index/*.db": Store(
        "codebase search indexes, one per workspace", "rebuilt",
        reason="re-indexed from the workspace"),
    "chat_sessions_test_*.db": Store(
        "per-process transcript stores of test mode", "machine",
        reason="test mode only"),
}

#: How long each store keeps what it holds (2026-11, FA-141): the rule that
#: removes rows and where it runs, or "kept:" and why nothing does. Four
#: append-only stores (approval gates, tool receipts, task ledgers, the
#: model-call ledger) and the swarm's pipeline log grew forever with no
#: declaration to notice it. ``tests/test_store_retention_declared.py``
#: requires an entry for every store and family; a sweep named here is one
#: of ``memory.worker_bootstrap._MAINTENANCE_SWEEPS`` (every 15 minutes).
RETENTION: dict[str, str] = {
    "agent_artifacts.db": "the artifact GC sweep: a per-chat cap and an age-out of spent draft sets",
    "task_ledgers.db": "the task ledger retention sweep: done and superseded ledgers after 90 days; "
                       "an active one is kept",
    "tool_effects.db": "the tool receipt retention sweep: completed receipts after 90 days; a receipt "
                       "of an unknown outcome (started) is kept",
    "memory_state.db": "kept: the owner's memory; archival, Forget and the summaries manage it (AGENTS 15)",
    "memory_ops.db": "the task queue purge (completed tasks after 7 days); dead letters until the "
                     "operator clears them; the audit log is kept (the trail of every change)",
    "memory_forget_journal.db": "kept: replayed over a restored older memory database, so it outlives "
                                "every backup",
    "knowledge_graph.db": "kept: written by nothing since the V1 to V2 move; read once by the backfill",
    "checkpoints.db": "the checkpoint retention sweep: 200 newest per chat, 10 once a chat is idle past "
                      "checkpoints.retention_days",
    "snapshots.db": "Time Travel's budgets on every capture: per step, turn, chat and in total",
    "time_travel_audit.db": "RETENTION_DAYS and MAX_ROWS, applied as rows are written",
    "chat_sessions.db": "kept: the owner's chats, until the owner deletes one",
    "chat_sessions_spool.db": "an entry leaves with its session's next accepted save",
    "sessions.db": "chat-platform routing; a session expires five minutes after its last message",
    "cron.db": "finished jobs purged by their finish time (the job retention), hourly",
    "swarm_tasks.db": "the swarm task retention sweep: swarm.task_retention_days (30; 0 keeps all)",
    "pipeline_logs.db": "the pipeline log retention sweep, with the swarm task retention's days",
    "research_sessions.db": "bounded: 256 runs and 128 MiB of research, 1024 rows and 32 MiB of chat "
                            "web searches",
    "x_posts.db": "kept: the duplicate and daily-cap checks read every post published",
    "x_scheduled.db": "kept: every scheduled post and its outcome (the daily and monthly caps count them)",
    "x_replies.db": "kept: the auto-reply queue, one row per mention answered",
    "x_datasets.db": "kept: the operator's annotation collections",
    "x_audit.db": "kept: the X API audit log (purge_x_audit removes by age on request)",
    "x_publications.db": "ended operations after 90 days, by the publication fire cycle (hourly)",
    "hitl_gates.db": "the gate retention sweep: decided gates after 90 days; a live one is kept",
    "run_incidents.db": "the run incident retention sweep",
    "chat_outbox.db": "the chat outbox retention sweep: settled rows (their text already emptied) "
                      "after 7 days",
    "chat_inbox.db": "the next normal start: a message handed on keeps no text, and rows a week "
                     "old are removed (read_only_mode.drain_inbox)",
    "vault.db": "kept: the install's secrets, until removed",
    "settings.db": "kept: the settings",
    "knowledge.db": "kept: the libraries, until the owner deletes one (a 1 GiB quota)",
    "rbac.db": "kept: roles and permissions",
    "audit.db": "kept: the audit log",
    "security_audit.db": "kept: retired, written by nothing",
    "disclosure.db": "kept: the security disclosure reports",
    "certifications.db": "kept: each certification carries its own validity",
    "llm_calls.db": "the model-call retention sweep: rows after 365 days (a chat's /cost reads a year)",
    "answer_quality.db": "30 days and 10,000 turns, applied as rows are written",
    "documents.db": "the document GC: tombstones, rejected and dead-letter rows by their retention days",
    "documents-jobs-fallback.db": "the document GC, with the document store",
    "sandbox_emails.db": "kept: the test mailbox",
    "semantic_cache.db": "kept: retired, written by nothing",
    "security_scan.db": "kept: retired, written by nothing",
    "file_checkpoints.db": "the 20 newest IDE checkpoints are kept, older ones removed as one is added",
    "callbacks.db": "button tokens expire after 24 hours, removed as one is added",
    "registry.db": "kept: the hub's user-level registry",
    "vector.db": "rebuilt from the memory stores",
    "chroma.sqlite3": "rebuilt from the Knowledge Library's chunks",
    "workspaces.db": "a bundle-internal copy of settings.db's table",
    "memory.db": "kept: retired, written by nothing",
    "kazma.db": "kept: retired, written by nothing",
    "ops.db": "kept: retired, written by nothing",
    "config.db": "kept: retired, written by nothing",
    "chat_sessions_test.db": "test mode only",
    "checkpoints_*.db": "the checkpoint retention sweep, as checkpoints.db",
    "code-index/*.db": "rebuilt from the workspace",
    "chat_sessions_test_*.db": "test mode only",
}


#: Product modules that build a database FILENAME at runtime (an f-string),
#: and the declared family or purpose each one produces. The literal-name
#: gate cannot read an f-string, so every such site is named here instead.
DYNAMIC_NAME_SITES: dict[str, str] = {
    "kazma-gateway/kazma_gateway/stores/checkpoint.py": "checkpoints_*.db",
    "kazma-core/kazma_core/code_index/store.py": "code-index/*.db",
    "kazma-ui/kazma_ui/session_manager.py": "chat_sessions_test_*.db",
    "kazma-core/kazma_core/paths.py": "chat_sessions_spool.db",
    "kazma-core/kazma_core/memory/backup.py": "backup copies (backups dir)",
    "kazma-core/kazma_core/migration/vault_pairing.py": "vault backup before a key reset",
}

@dataclass(frozen=True)
class DataDirEntry:
    """Something Kazma keeps directly in the data dir that is not a database."""

    holds: str
    migration: str
    #: Why, whenever migration is not "bundle".
    reason: str = ""
    #: Carried even by a lean bundle (``kazma migrate export --no-assets``):
    #: small state Kazma reads at boot, not a folder of files.
    state: bool = False
    #: Copied by the universal backup. False for a file the running server
    #: holds locked: the copy fails (a WARNING every backup) and restores
    #: nothing a server could use; and for one an older copy would make
    #: wrong (``outage_pages.json``: restored, it owes notes for pages long
    #: since followed by their recovery).
    backed_up: bool = True


#: Every entry product code puts directly in the data dir that is not a
#: database (those are ``STORES``), by name or glob pattern, and how it
#: crosses machines. Anything in the data dir that is neither declared here
#: nor a database is the operator's own -- or the agent's, written there
#: through its file tools (the live install's workspace IS its install
#: folder) -- and a bundle carries it with the files.
#:
#: Until 2026-10-01 a bundle carried five hand-listed folders: the swarm's
#: worker registry and templates, the default workspace and everything the
#: owner kept in the data dir stayed behind. ``tests/test_store_registry.py``
#: fails on an entry product code names that is not declared here.
DATA_DIR_ENTRIES: dict[str, DataDirEntry] = {
    ".runtime-state-id": DataDirEntry("the state volume's identity paired with its HA Postgres database", "bundle", state=True),
    ".runtime-writer.lock": DataDirEntry("the server process's local writer fence", "machine", reason="OS ownership is reacquired after restore; the inode is never copied", backed_up=False),
    "swarm_registry.json": DataDirEntry("the swarm's registered workers", "bundle", state=True),
    "swarm_templates.json": DataDirEntry(
        "the operator's own worker templates", "bundle", state=True),
    "attachments": DataDirEntry(
        "files sent in chats and files shared into a web chat", "bundle"),
    "documents": DataDirEntry("documents the agent generated or processed", "bundle"),
    "exports": DataDirEntry("exports the operator asked for", "bundle"),
    "problem_reports": DataDirEntry(
        "problem reports that could not be sent (kazma_core.support.problem_report)", "machine",
        reason="a report describes this machine's install at the moment it was made"),
    "history_import": DataDirEntry(
        "an export kept while it is imported into memory (kazma_ui.history_import_api)", "machine",
        reason="deleted when its import ends, and any left over after an hour"),
    "kazma-watch": DataDirEntry(
        "your own Kazma Watch's code and configuration (kazma observer setup; no secret)", "machine",
        reason="made again by the next kazma observer setup from this build's code"),
    "images": DataDirEntry("generated images and browser screenshots", "bundle"),
    "workspace": DataDirEntry("the default workspace: the files the agent works on", "bundle"),
    "document-store": DataDirEntry(
        "the document platform's content-addressed files", "documents",
        reason="carried with the document store export, beside documents.db"),
    "vector_memory": DataDirEntry(
        "the Knowledge Library's vectors", "rebuilt",
        reason="the knowledge vector repair sweep re-embeds every chunk"),
    "knowledge_provenance": DataDirEntry(
        "each Knowledge Library page's fetched text, by library: removed with its library",
        "bundle"),
    "canary": DataDirEntry(
        "the daily crawl check's private Knowledge store (web_acquire/canary.py)", "rebuilt",
        reason="emptied and filled again by every day's check; it holds one public page"),
    "code-index": DataDirEntry(
        "codebase search indexes", "rebuilt", reason="re-indexed from the workspace"),
    "models": DataDirEntry(
        "the neural OCR engine's models (documents.ocr.engine)", "rebuilt",
        reason="re-installed from their pinned sources with python scripts/ocr_models.py install; "
               "230 MB of downloads, not state"),
    "vuln_cache.json": DataDirEntry(
        "the retired cache of vulnerability lookups", "legacy",
        reason="answers were kept forever and hid new advisories; the scanner asks OSV "
               "each time since 2026-10-02"),
    "backups": DataDirEntry(
        "this machine's backups", "machine",
        reason="the target keeps its own; the source's history stays in its restic repository"),
    "settings_snapshot.yaml": DataDirEntry(
        "the settings' local copy for a start without the database (secrets as vault pointers)",
        "machine", reason="rewritten every 15 minutes from the settings themselves; a bundle "
                          "carries the settings"),
    "setup_record.json": DataDirEntry(
        "what kazma setup postgres installed: the database service and its WAL archive", "machine",
        reason="names this machine's service, ports and folders"),
    "install_id": DataDirEntry(
        "this install's identity", "machine",
        reason="two installs must never share one: shared-store peers and the "
               "memory mirror tell installs apart by it"),
    "outage_pages.json": DataDirEntry(
        "outage pages this process still owes a recovery note "
        "(observability/outage_pages.py)", "machine",
        reason="it names this install's own pages; on another machine, or restored from an "
               "older copy, it would announce a recovery of an outage nobody was told of",
        backed_up=False),
    "pg_tables_seen.json": DataDirEntry(
        "Kazma's tables this install has found in its Postgres", "machine",
        reason="it describes this install's own Postgres: the boot check tells a "
               "dropped table from one a newer build adds by it"),
    "kazma.log": DataDirEntry(
        "a log file", "machine", reason="logs stay with the machine that wrote them"),
    "restart.log": DataDirEntry(
        "the log of a restart started from Settings", "machine",
        reason="logs stay with the machine that wrote them"),
    "update-state.json": DataDirEntry(
        "the state of an update in progress", "machine",
        reason="it describes this machine's checkout, part way through an update"),
    # Not backed up (audit 2026-11-10): a staging folder an import or export
    # left behind held the bundle's meta.env -- the vault key in clear --
    # beside a vault.db, and the universal backup copied both into the plain
    # offsite copy. The universal backup matched only exact names here, so the
    # pattern kept nothing out (``kept_out_of_backups`` reads patterns).
    ".migrate-*": DataDirEntry(
        "the migration's own working folders", "machine",
        reason="this machine's staging and pre-import backups", backed_up=False),
    "*.tmp": DataDirEntry(
        "a file being written", "machine", reason="a half-written file is never read"),
    "agent_evolution.json": DataDirEntry(
        "the Soul, before it moved into the settings", "legacy",
        reason="read once into the settings, which the bundle carries"),
    "pending_evolution.json": DataDirEntry(
        "Soul changes waiting for review, before they moved into the settings", "legacy",
        reason="read once into the settings, which the bundle carries"),
    "*.migrated": DataDirEntry(
        "a legacy file already moved into a store", "legacy",
        reason="its content is in a store the bundle carries"),
    # The copies Kazma's own repairs take of the memory database before they
    # change it (FA-254). Each holds memory as it was then, forgotten turns
    # included: carried, a migration brought back what the owner had
    # forgotten since (live 2026-11: all three travelled, and an import
    # restored them). ``REPAIR_COPY_SUFFIXES`` is the list the gate reads.
    "*.pre_entity_key": DataDirEntry(
        "the memory database as it was before the entity-key migration", "machine",
        reason="a repair's undo copy: it holds memories forgotten since"),
    "*.pre_relabel": DataDirEntry(
        "the memory database as it was before the provenance relabel", "machine",
        reason="a repair's undo copy: it holds memories forgotten since"),
    "*.pre_reopen": DataDirEntry(
        "the memory database as it was before list entries were reopened", "machine",
        reason="a repair's undo copy: it holds memories forgotten since"),
    "*.pre_reembed": DataDirEntry(
        "the memory database as it was before an embedding rebuild", "machine",
        reason="a rebuild's undo copy: it holds memories forgotten since"),
}

#: The suffixes Kazma's repairs give the copy they take before changing a
#: database (``<db>.pre_<name>``). Every one is declared above;
#: ``tests/test_repair_copies_stay_home.py`` reads each suffix product code
#: and the repair scripts write and fails on one missing here.
REPAIR_COPY_SUFFIXES: tuple[str, ...] = (".pre_entity_key", ".pre_relabel", ".pre_reopen", ".pre_reembed")

#: The names a wrong read or write must never reach, wherever the file sits:
#: the record of what the operator approved, and the secrets.
_ALWAYS_PROTECTED = frozenset({"hitl_gates.db", "vault.db"})

_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_DB_SIDECARS = ("-wal", "-shm", "-journal")

#: A copy of a database named after it: ``memory_state.db.pre_relabel``
#: (the memory repair scripts), ``.pre_reembed`` (a rebuild),
#: ``research_sessions.db.bak-<date>`` (an operator's copy).
_DB_COPY_NAME = re.compile(r"\.(?:db|sqlite3?)[.\-_~]")

#: The first 16 bytes of every SQLite database file.
_SQLITE_HEADER = b"SQLite format 3\x00"


# ── what reads what back ─────────────────────────────────────────────────

#: Data the model never has to fetch: fed into its context every turn.
#: name -> (module, attribute, what it is). The gate imports the attribute and
#: checks the prompt assembler (agent/graph_supervisor.py) uses it.
CONTEXT_FEEDERS: dict[str, tuple[str, str, str]] = {
    "scratchpad": (
        "kazma_core.agent.artifacts", "ArtifactStore.list_scratchpad",
        "scratchpad findings"),
    "task_ledger": (
        "kazma_core.agent.task_ledger", "format_ledger_block",
        "the task ledger"),
}


@dataclass(frozen=True)
class Write:
    """Where a tool's writes land, and how the model gets them back.

    ``targets``: store filenames from :data:`STORES` / :data:`STORE_FAMILIES`,
    or one of ``workspace`` (files, read with the file tools), ``host``
    (processes / the machine), ``none`` (nothing persists: the result IS
    the tool output), ``external:<system>``, ``user:<area>``.

    ``readers``: registered read-tier tool names, or ``context:<feeder>``,
    for THESE targets. Empty only with a ``note`` — and never when a target
    is a Kazma store. ``also`` holds further target groups with their own
    readers (a research run writes a session row AND report files; the
    file reader must not be offered as the way into the database).
    """

    targets: tuple[str, ...]
    readers: tuple[str, ...] = ()
    note: str = ""
    also: tuple[Write, ...] = ()

    def groups(self) -> tuple[Write, ...]:
        return (self, *self.also)


_MEMORY_READERS = ("memory_search", "memory_list_entities", "memory_list_beliefs")
_WORKSPACE_FILE = Write(("workspace",), ("file_read", "file_list"))
_OUTPUT_ONLY = "the result is the tool output; nothing is saved"

TOOL_WRITES: dict[str, Write] = {
    # ── the agent's own state ──────────────────────────────────────────
    "save_proposal": Write(("agent_artifacts.db",), ("list_proposals",)),
    "x_draft_posts": Write(("agent_artifacts.db",), ("list_proposals",)),
    "discard_proposal": Write(("agent_artifacts.db",), ("list_proposals",)),
    "update_scratchpad": Write(("agent_artifacts.db",), ("context:scratchpad",)),
    "task_ledger_update": Write(("task_ledgers.db",), ("context:task_ledger",)),
    "memory_store": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_link_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_admin": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_delete_entity": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_invalidate": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_forget_mentions": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_merge_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "memory_purge_empty_entities": Write(("memory_state.db",), _MEMORY_READERS),
    "knowledge_create_library": Write(
        ("knowledge.db",), ("knowledge_list_libraries", "knowledge_search")),
    "knowledge_ingest_url": Write(
        ("knowledge.db",), ("knowledge_list_libraries", "knowledge_search")),
    "knowledge_ingest_site": Write(
        ("knowledge.db",), ("knowledge_list_libraries", "knowledge_search", "research_readiness")),
    "config_save": Write(("settings.db",), ("config_read",)),
    "request_path_access": Write(("settings.db",), ("config_read",)),
    "vault_store": Write(("vault.db",), ("vault_list",)),
    "vault_delete": Write(("vault.db",), ("vault_list",)),
    "vault_retrieve": Write(
        ("none",), note="reads one secret behind an approval; writes nothing"),
    # ── scheduling and delegation ──────────────────────────────────────
    "schedule_task": Write(("cron.db",), ("list_scheduled",)),
    "edit_scheduled": Write(("cron.db",), ("list_scheduled",)),
    "cancel_scheduled": Write(("cron.db",), ("list_scheduled",)),
    "dispatch_swarm": Write(("swarm_tasks.db",), ("check_swarm_task",)),
    # A swarm worker's own tool: the handoff lands on its task's result.
    "handoff_to_worker": Write(("swarm_tasks.db",), ("check_swarm_task",)),
    "spawn_agent": Write(("none",), note="the sub-agent's answer is the tool result"),
    "spawn_agents": Write(("none",), note="the sub-agents' answers are the tool result"),
    "cancel_research_session": Write(("research_sessions.db",), ("research_session_status",)),
    "start_deep_research": Write(
        ("research_sessions.db",), ("list_research_papers",),
        also=(Write(("workspace",), ("file_read",)),)),
    "run_research_pipeline": Write(
        ("research_sessions.db",), ("list_research_papers",),
        also=(Write(("workspace",), ("file_read",)),)),
    "plan_research_queries": Write(("none",), note=_OUTPUT_ONLY),
    # ── X ──────────────────────────────────────────────────────────────
    # A publish also marks its saved draft used; that bookkeeping belongs to
    # the tool worker (mark_proposals_posted), and list_proposals shows it.
    "x_post": Write(("external:x", "x_posts.db", "x_publications.db"), ("x_status",)),
    "x_delete_post": Write(("external:x", "x_posts.db", "x_publications.db"), ("x_status",)),
    "x_download_media": Write(("workspace",), ("file_list",)),
    "download_image": Write(("workspace",), ("file_list", "analyze_image")),
    "x_schedule_post": Write(("x_scheduled.db", "x_publications.db"), ("x_list_scheduled",)),
    "x_cancel_scheduled_post": Write(("x_scheduled.db", "x_publications.db"), ("x_list_scheduled",)),
    # ── documents ──────────────────────────────────────────────────────
    "document_import": Write(
        ("documents.db",), ("document_status", "document_read", "document_search")),
    "document_index": Write(("documents.db",), ("document_search", "document_status")),
    "document_convert": Write(("documents.db", "workspace"), ("document_status", "document_read", "read_document", "file_list")),
    "document_redact": Write(("documents.db", "workspace"), ("document_read", "document_status", "read_document", "file_list")),
    "document_cancel": Write(("documents.db",), ("document_status",)),
    "convert_document": Write(("workspace",), ("read_document", "file_list")),
    "ocr_document": Write(("none",), note=_OUTPUT_ONLY),
    "generate_docx": Write(("workspace",), ("read_document", "file_list")),
    "generate_pdf": Write(("workspace",), ("read_document", "file_list")),
    "generate_xlsx": Write(("workspace",), ("read_document", "file_list")),
    "generate_pptx": Write(("workspace",), ("read_document", "file_list")),
    "generate_markdown_doc": _WORKSPACE_FILE,
    "pdf_merge": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_split": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_fill_form": Write(("workspace",), ("pdf_info", "read_document")),
    "pdf_redact": Write(("workspace",), ("pdf_info", "read_document")),
    # ── research files ─────────────────────────────────────────────────
    "read_url_to_file": Write(
        ("workspace",), ("list_research_chunks", "read_research_chunk", "file_read")),
    "digest_research_file": Write(("none",), note=_OUTPUT_ONLY),
    "summarize_research_file": Write(("none",), note=_OUTPUT_ONLY),
    "synthesize_from_digests": Write(("none",), note=_OUTPUT_ONLY),
    "export_session": Write(("none",), note=_OUTPUT_ONLY),
    # ── files, code, git ───────────────────────────────────────────────
    "file_write": _WORKSPACE_FILE,
    "file_append": _WORKSPACE_FILE,
    "file_apply_patch": _WORKSPACE_FILE,
    "file_apply_patch_set": _WORKSPACE_FILE,
    "file_delete": _WORKSPACE_FILE,
    "format_code": _WORKSPACE_FILE,
    "git_checkout": Write(("workspace",), ("git_status", "file_read")),
    "git_commit": Write(("workspace",), ("git_status",)),
    "git_merge": Write(("workspace",), ("git_status", "file_read")),
    "git_pull": Write(("workspace",), ("git_status", "file_read")),
    "git_push": Write(("workspace", "external:github"), ("git_status",)),
    "github_create_issue": Write(("external:github",), ("github_list_issues",)),
    "github_comment_issue": Write(("external:github",), ("github_list_issues",)),
    "github_create_pr": Write(("external:github",), ("github_list_issues",)),
    "github_merge_pr": Write(("external:github",), ("github_list_issues",)),
    # ── host execution ─────────────────────────────────────────────────
    "shell_exec": Write(("host",), note="output is the tool result; files it writes are read with file_read"),
    "python_exec": Write(("host",), note="output is the tool result; files it writes are read with file_read"),
    "run_unit_tests": Write(("host",), note="the test report is the tool result"),
    "computer_use": Write(("host",), note="acts on the desktop; screenshots come back in the tool result"),
    "install_python_packages": Write(("host",), ("check_environment",)),
    "install_npm_packages": Write(("host",), ("check_environment",)),
    "mcp_test_server": Write(("none",), note="probes a server; the verdict is the tool result"),
    # ── browser ────────────────────────────────────────────────────────
    "browser_navigate": Write(("external:web",), ("browser_extract_text",)),
    "browser_click": Write(("external:web",), ("browser_extract_text",)),
    "browser_fill_form": Write(("external:web",), ("browser_extract_text",)),
    "browser_eval_js": Write(("external:web",), ("browser_extract_text",)),
    "browser_screenshot": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    # ── media ──────────────────────────────────────────────────────────
    "generate_image": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    "generate_ui_mockup": Write(("workspace",), ("analyze_local_image", "analyze_image")),
    # ── calendar and email ─────────────────────────────────────────────
    "create_event": Write(("external:calendar",), ("list_events", "find_free_slots")),
    "update_event": Write(("external:calendar",), ("list_events",)),
    "delete_event": Write(("external:calendar",), ("list_events",)),
    "email_send": Write(("external:email", "sandbox_emails.db"), ("email_list", "email_get")),
    "email_delete": Write(("external:email", "sandbox_emails.db"), ("email_list",)),
    "email_categorize": Write(("external:email", "sandbox_emails.db"), ("email_list", "email_get")),
    # ── messages to people ─────────────────────────────────────────────
    "send_message": Write(("external:chat",), note="delivered to a chat; nothing to read back"),
    "dispatch_notification": Write(("external:chat",), note="delivered to the operator; nothing to read back"),
    "send_approval_request": Write(
        ("external:chat",),
        note="a question to the operator, not an approval card; the answer comes as their next chat message",
    ),
    "send_file": Write(("external:chat",), note="sends a copy; the file itself stays readable with file_read"),
    # ── skills ─────────────────────────────────────────────────────────
    "activate_skill": Write(("none",), note="returns the skill's instructions in the tool result"),
    "install_agent_skill": Write(("user:skills",), ("list_agent_skills",)),
    "uninstall_agent_skill": Write(("user:skills",), ("list_agent_skills",)),
}


# ── the predicate every door asks ────────────────────────────────────────


def _strip_sidecar(name: str) -> str:
    lowered = name.lower()
    for sidecar in _DB_SIDECARS:
        if lowered.endswith(sidecar):
            return lowered[: -len(sidecar)]
    return lowered


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ── Kazma's secret files ────────────────────────────────────────────────
#
# Not databases, but the same rule and the same doors. The install's .env
# holds the vault key, the database DSN and the provider keys; restic.pass
# opens every offsite backup; the broker token commands the document worker
# containers; a migration bundle's meta.env carries the vault key; the
# observer's pairing key (observer.key, resilience plan B1) signs the guard's
# heartbeats and reads the observer's view of this install. Until
# 2026-11-02 only databases were refused, and file_read and file_search
# (read tier, no card) returned the live install's .env and restic.pass
# (audit 2026-11, FA-001).

#: Secret files refused by name wherever they are.
SECRET_FILE_NAMES: frozenset[str] = frozenset(
    {"restic.pass", "document_broker.token", "meta.env", "observer.key", "postgres_superuser.pass"}
)

#: An env file with one of these endings is a template (``.env.example``),
#: never a secret.
_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def _is_env_file_name(lowered: str) -> bool:
    if lowered == ".env":
        return True
    return lowered.startswith(".env.") and not lowered.endswith(_ENV_TEMPLATE_SUFFIXES)


def is_secret_file_name(name: str) -> bool:
    """True when a bare file name is the name of one of Kazma's secret files.

    For checks that only have text (a shell token, a quoted string in
    code); :func:`is_kazma_secret_file` decides for a path.
    """
    lowered = Path(str(name or "")).name.strip().lower()
    return lowered in SECRET_FILE_NAMES or _is_env_file_name(lowered)


def _install_env_dirs() -> list[Path]:
    """Folders whose env files are Kazma's own: the home and the install."""
    dirs: list[Path] = []
    try:
        from kazma_core import paths as _paths
    except ImportError:
        return dirs
    for getter in ("user_home", "installed_project_root", "get_project_root"):
        fn = getattr(_paths, getter, None)
        if fn is None:
            continue
        try:
            found = fn()
        except (OSError, RuntimeError, ValueError):
            continue  # an unresolvable root is skipped, never fatal
        if found:
            try:
                dirs.append(Path(found).expanduser().resolve())
            except (OSError, RuntimeError, ValueError):
                continue
    return dirs


def is_kazma_secret_file(path: str | Path) -> bool:
    """True if *path* is one of Kazma's own secret files.

    By NAME, anywhere: ``restic.pass``, ``document_broker.token`` and a
    bundle's ``meta.env``. The file ``KAZMA_ENV_FILE`` names. An env file
    (``.env``, ``.env.local``; never ``.env.example``) directly in Kazma's
    home, the install folder or the project root, or at any depth under the
    data dir except the coding sandbox (backups keep copies of ``.env``).
    A ``.env`` in the user's own project is theirs and stays readable.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    name = resolved.name.lower()
    if name in SECRET_FILE_NAMES:
        return True
    explicit = (os.environ.get("KAZMA_ENV_FILE") or "").strip()
    if explicit:
        try:
            if resolved == Path(explicit).expanduser().resolve():
                return True
        except (OSError, RuntimeError, ValueError):
            pass  # an unresolvable KAZMA_ENV_FILE names no file
    if not _is_env_file_name(name):
        return False
    if resolved.parent in _install_env_dirs():
        return True
    try:
        from kazma_core.paths import data_dir

        root = data_dir().resolve()
    except (ImportError, OSError, RuntimeError):
        return False
    return _under(resolved, root) and not _under(resolved, root / "workspace")


def install_secret_files() -> list[Path]:
    """Every secret file of this install that exists, resolved, once each.

    For the security report, which checks who else can read them (2026-11:
    the live ``.env``, ``restic.pass`` and ``vault.db`` were readable by a
    group another program had granted the install folder; the report looked
    at ``restic.pass`` only). The env files directly in Kazma's home, the
    install folder and the project root (never a template), the file
    ``KAZMA_ENV_FILE`` names, every :data:`SECRET_FILE_NAMES` file in those
    folders and in the data dir, the broker token the guard hands Kazma
    (``KAZMA_DOCUMENT_BROKER_TOKEN_FILE``) and the vault. The restic
    passphrase can live elsewhere (``restic_repo.password_file``); the
    report adds it.
    """
    found: dict[Path, None] = {}

    def _add(candidate: Path) -> None:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return
        if resolved.is_file():
            found.setdefault(resolved, None)

    folders = list(_install_env_dirs())
    try:
        from kazma_core import paths as _paths

        folders.append(_paths.data_dir())
        _add(Path(_paths.vault_db_path()))
    except (ImportError, OSError, RuntimeError):
        pass  # no data dir: the env folders are still checked
    for folder in folders:
        try:
            children = list(Path(folder).iterdir())
        except OSError:
            continue  # a folder that cannot be listed holds nothing to check
        for child in children:
            if is_secret_file_name(child.name):
                _add(child)
    for variable in ("KAZMA_ENV_FILE", "KAZMA_DOCUMENT_BROKER_TOKEN_FILE"):
        named = (os.environ.get(variable) or "").strip()
        if named:
            _add(Path(named))
    return sorted(found)


def kazma_paths_inside(root: str | Path) -> tuple[list[Path], list[Path]]:
    """Kazma's own folders and secret files inside *root*, for a sandbox to hide.

    A container that mounts a workspace sees everything in it. When the
    workspace holds the install -- the live install's workspace IS the
    install folder -- that is the data dir, Kazma's home (``restic.pass``,
    the broker token, the logs) and the install's ``.env``: the 2026-11
    functional audit read the live ``.env`` from ``python_exec``'s container
    at ``/workspace/.env``, on a card whose code never spelled the name.

    Returns ``(folders, files)``, resolved, each existing and strictly inside
    *root*: the data dir and Kazma's home (a folder inside another is left
    out, the outer one hides it), and each file directly in *root* or in an
    install env folder inside it that :func:`is_kazma_store` claims (an env
    file of the install, a secret file by name, ``vault.db``), plus the file
    ``KAZMA_ENV_FILE`` names. Symbolic links are left alone: a container
    cannot follow one out of its mount.
    """
    try:
        base = Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return [], []
    found: list[Path] = []
    try:
        from kazma_core import paths as _paths
    except ImportError:
        _paths = None
    for getter in ("data_dir", "user_home"):
        fn = getattr(_paths, getter, None) if _paths is not None else None
        if fn is None:
            continue
        try:
            folder = Path(fn()).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if folder != base and _under(folder, base) and folder.is_dir():
            found.append(folder)
    folders = [
        f for f in dict.fromkeys(found) if not any(o != f and _under(f, o) for o in found)
    ]

    def _hidden_by_a_folder(p: Path) -> bool:
        return any(p == f or _under(p, f) for f in folders)

    files: list[Path] = []
    scan = [base] + [d for d in _install_env_dirs() if d != base and _under(d, base)]
    for folder in dict.fromkeys(scan):
        if _hidden_by_a_folder(folder):
            continue
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
            except OSError:
                continue
            if is_kazma_store(entry):
                files.append(entry.resolve())
    explicit = (os.environ.get("KAZMA_ENV_FILE") or "").strip()
    if explicit:
        try:
            named = Path(explicit).expanduser().resolve()
            if _under(named, base) and named.is_file() and not _hidden_by_a_folder(named):
                files.append(named)
        except (OSError, RuntimeError, ValueError):
            pass  # an unresolvable KAZMA_ENV_FILE names no file
    return folders, list(dict.fromkeys(files))


def is_kazma_store(path: str | Path) -> bool:
    """True if *path* is one of Kazma's databases (or its WAL/SHM sidecar),
    or one of its secret files (:func:`is_kazma_secret_file`).

    By LOCATION: any SQLite file under the data dir, except the default
    coding sandbox (``data_dir()/workspace``), whose databases are the
    user's. Matched by suffix rather than by name, so a store added later is
    covered on the day it is added; a copy named after a database
    (``memory_state.db.pre_relabel``) and any file that starts with the
    SQLite header count too. By NAME, anywhere: the gate registry and
    the vault — a stray copy of either is still the crown jewels.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if is_kazma_secret_file(resolved):
        return True
    name = _strip_sidecar(resolved.name)
    if name in _ALWAYS_PROTECTED:
        return True
    try:
        from kazma_core.paths import data_dir

        root = data_dir().resolve()
    except (ImportError, OSError, RuntimeError):
        # Cannot classify; the callers' own rules still apply.
        return False
    if not _under(resolved, root) or _under(resolved, root / "workspace"):
        return False
    return (
        name.endswith(_DB_SUFFIXES)
        or bool(_DB_COPY_NAME.search(name))
        or _has_sqlite_header(resolved)
    )


def _has_sqlite_header(path: Path) -> bool:
    """True when *path* is an existing file that starts as a SQLite database.

    A copy of a store is the store whatever it is called: on the live install
    ``memory_state.db.pre_relabel`` held the whole memory database, and the
    suffix rule let file_read (read tier, no card) print it (audit 2026-11).
    """
    try:
        if not path.is_file():
            return False
        with path.open("rb") as fh:
            return fh.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def store_name_for(path_or_name: str | Path) -> str | None:
    """The declared store (or family pattern) a path or filename belongs to."""
    raw = str(path_or_name or "")
    name = _strip_sidecar(Path(raw).name)
    if name in STORES:
        return name
    rel = raw.replace("\\", "/")
    try:
        from kazma_core.paths import data_dir

        rel = Path(raw).expanduser().resolve().relative_to(
            data_dir().resolve()
        ).as_posix()
    except (ImportError, OSError, RuntimeError, ValueError):
        pass  # not under the data dir: match by name only
    for pattern in STORE_FAMILIES:
        if "/" in pattern:
            if fnmatch.fnmatch(_strip_sidecar(rel), pattern):
                return pattern
        elif fnmatch.fnmatch(name, pattern):
            return pattern
    return None


def distinctive_store_names() -> frozenset[str]:
    """Store filenames specific enough to match as a bare substring.

    For checks that only have text (a command, a code snippet, an approval
    card). Generic names (``sessions.db``, ``audit.db``) are left to the
    location rule so a user's own file of that name is never flagged. Paths
    helpers are still consulted, so a store resolved through
    ``kazma_core.paths`` is covered even under a renamed file.
    """
    names = {n for n, s in STORES.items() if not s.ambiguous_name}
    names.update(_ALWAYS_PROTECTED)
    try:
        from kazma_core import paths as _paths

        for helper in (
            "vault_db_path", "checkpoints_db", "settings_db", "knowledge_db", "snapshots_db",
            "swarm_tasks_db", "audit_db", "rbac_db", "hub_registry_db",
            "primary_memory_db", "memory_ops_db", "memory_forget_journal_db", "knowledge_graph_db",
        ):
            fn = getattr(_paths, helper, None)
            if fn is None:
                continue
            try:
                names.add(Path(str(fn())).name.lower())
            except (OSError, RuntimeError, ValueError, TypeError):
                continue  # an unresolvable helper is skipped, never fatal
    except ImportError:
        pass
    return frozenset(n for n in names if n)


def readers_for_store(name: str) -> tuple[str, ...]:
    """Tools / context feeders that give the model this store's data back."""
    key = store_name_for(name) or _strip_sidecar(Path(str(name)).name)
    seen: list[str] = []
    for write in TOOL_WRITES.values():
        for group in write.groups():
            if key in group.targets:
                for reader in group.readers:
                    if reader not in seen:
                        seen.append(reader)
    return tuple(seen)


def store_refusal(path_or_name: str | Path, *, door: str) -> str:
    """Why *door* will not open this store, and what to use instead.

    ``door`` is how the model tried ("the SQL tools", "file tools",
    "python_exec", "shell_exec"). The reader is named because a bare "no"
    is what sent the model digging through every other door.
    """
    raw = Path(str(path_or_name)).name or str(path_or_name)
    if is_secret_file_name(raw) or is_kazma_secret_file(path_or_name):
        return (
            f"{raw} holds Kazma's own secrets (keys, passwords, tokens); "
            f"{door} may not open it. Nothing in it is meant for you to read; "
            "tell the user what you need instead of looking for another way in."
        )
    key = store_name_for(path_or_name)
    store = STORES.get(key or "") or STORE_FAMILIES.get(key or "")
    what = f" ({store.holds})" if store else ""
    readers = readers_for_store(key or raw)
    tools = [r for r in readers if not r.startswith("context:")]
    fed = [
        CONTEXT_FEEDERS[r.split(":", 1)[1]][2]
        for r in readers
        if r.startswith("context:") and r.split(":", 1)[1] in CONTEXT_FEEDERS
    ]
    parts = [f"{raw} is one of Kazma's own stores{what}; {door} may not open it."]
    if tools:
        parts.append("Read it with " + ", ".join(tools) + ".")
    if fed:
        parts.append(
            "Already in your context every turn: " + ", ".join(fed) + "."
        )
    if not tools and not fed:
        parts.append(
            "Nothing in it is meant for you to read; tell the user what you "
            "need instead of looking for another way in."
        )
    return " ".join(parts)


def bundle_store_names() -> tuple[str, ...]:
    """Declared stores a migration bundle carries, sorted."""
    return tuple(sorted(n for n, s in STORES.items() if s.migration == "bundle"))


def bundle_family_patterns() -> tuple[str, ...]:
    """Store families (top-level globs) a migration bundle carries."""
    return tuple(
        sorted(p for p, s in STORE_FAMILIES.items() if s.migration == "bundle" and "/" not in p)
    )


def _data_dir_entry(name: str) -> DataDirEntry | None:
    """The declaration of a data-dir entry by its name, or None: the operator's own."""
    if name in DATA_DIR_ENTRIES:
        return DATA_DIR_ENTRIES[name]
    for pattern, entry in DATA_DIR_ENTRIES.items():
        if any(ch in pattern for ch in "*?[") and fnmatch.fnmatchcase(name, pattern):
            return entry
    return None


def kept_out_of_backups(name: str) -> bool:
    """Does the universal backup leave this entry of the data dir out?

    By name or by pattern (``.migrate-*``), the one answer for the asset copy
    and the database walk: an entry declared ``backed_up=False``.
    """
    entry = _data_dir_entry(name)
    return entry is not None and not entry.backed_up


def carried_in_bundle(name: str, *, lean: bool = False) -> bool:
    """Does a ``kazma migrate`` bundle carry this entry of the data dir?

    The one answer for the exporter, which asks it of every entry, and the
    importer, which asks it again of every entry a bundle holds, so a bundle
    cannot plant an install identity or a database through its files.
    Databases are never carried here: the declared stores travel through
    :func:`bundle_store_names`. A declared entry travels when its migration
    is "bundle" -- in a *lean* bundle only if it is state; anything
    undeclared is the operator's own and travels with the files.
    """
    if not name or name in (".", ".."):
        return False
    if _strip_sidecar(name).endswith(_DB_SUFFIXES):
        return False
    entry = _data_dir_entry(name)
    if entry is None:
        return not lean
    if entry.migration != "bundle":
        return False
    return entry.state or not lean
