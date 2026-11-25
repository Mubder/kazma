"""Every ``row`` store of ``store_registry.SPACE_RULES``, made by its own code
(Spaces S4-06).

A space moved in from another install (``spaces.space_move``) lands in these
stores. On a fresh install most do not exist until their first use, and the
import never makes a table itself: a store's schema is its own code's, never
a bundle's. :func:`make_row_stores` makes each one that holds a space's rows,
in this install's data folder, the way the store makes itself; making a
store that is there changes nothing.

The stores no space's rows live in are not made here: the chat apps' inbox,
outbox and buttons (the owner's until their chats are bound to spaces), the
X post ledger and API record (no space column), the operator's X datasets,
and the vault and the settings (their own code reads and writes a space's
part: ``SecretVault``, ``ConfigStore``). ``tests/test_space_bundle_round_trip.py``
holds the list to the registry both ways.

Blocking (every store is SQLite here): call it off the event loop. Some
makers are asynchronous stores; they are run to completion on a loop of
their own, so never call this on a running loop's thread.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

__all__ = ["NOT_MADE_HERE", "make_row_stores"]

#: The ``row`` stores this module does not make, and why.
NOT_MADE_HERE: dict[str, str] = {
    "chat_sessions_spool.db": "made with the chat store it spools for (the chat sessions' manager)",
    "documents-jobs-fallback.db": "the document job queue's fallback, made only when its backend falls back",
    "x_posts.db": "the X post ledger: no space column, never part of a space's rows",
    "x_audit.db": "the X API's call record: no space column",
    "x_datasets.db": "the operator's X annotation collections: the install's",
    "callbacks.db": "the chat apps' button tokens: the owner's until stage 3 binds chats",
    "chat_inbox.db": "messages to the chat apps' bots: the owner's",
    "chat_outbox.db": "replies through the chat apps' bots: the owner's",
    "vault.db": "the vault's own code takes a space's secrets (SecretVault.import_space_sealed)",
    "settings.db": "the settings store's own code takes a space's settings",
}


def _close_after(store: Any, *, init: bool = False) -> None:
    async def _run() -> None:
        if init:
            await store.init()
        else:
            await store._ensure_db()
        await store.close()

    asyncio.run(_run())


def make_row_stores() -> list[str]:
    """Make every row store's tables in this install's data folder, each by
    its own code. Returns the stores made (or found made)."""
    from kazma_core import paths

    made: list[str] = []
    from kazma_core.memory import schema_v2

    for name, path in (("memory_state.db", paths.primary_memory_db()), ("memory_ops.db", paths.memory_ops_db())):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn:
            (schema_v2.ensure_primary_schema if name == "memory_state.db" else schema_v2.ensure_ops_schema)(conn)
            conn.commit()
        made.append(name)
    from kazma_core.safety.commitment.store import ensure_commitment_schema

    ensure_commitment_schema()
    from kazma_core.memory import forget_journal

    forget_journal._connect().close()
    made.append("memory_forget_journal.db")
    from kazma_core.agent.artifacts import get_artifact_store
    from kazma_core.agent.task_ledger import get_ledger_store

    get_artifact_store()
    get_ledger_store()
    made += ["agent_artifacts.db", "task_ledgers.db"]
    from kazma_core.agent.effect_journal import _EffectJournal

    _EffectJournal(paths.data_dir() / "tool_effects.db")
    made.append("tool_effects.db")
    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from langgraph.checkpoint.sqlite import SqliteSaver

    with closing(sqlite3.connect(paths.checkpoints_db(), check_same_thread=False)) as conn:
        SqliteSaver(conn, serde=kazma_checkpoint_serde()).setup()
    made.append("checkpoints.db")
    from kazma_core.time_travel import ensure_snapshots_table

    ensure_snapshots_table(paths.snapshots_db())
    made.append("snapshots.db")
    from kazma_core import time_travel_audit

    time_travel_audit._connect().close()
    made.append("time_travel_audit.db")
    from kazma_ui.session_manager import get_session_manager

    get_session_manager()
    made.append("chat_sessions.db")
    from kazma_gateway.stores.sqlite import SQLiteSessionStore

    _close_after(SQLiteSessionStore(str(paths.data_dir() / "sessions.db")))
    made.append("sessions.db")
    from kazma_core.cron.store import SQLiteCronStore

    _close_after(SQLiteCronStore(str(paths.data_dir() / "cron.db")), init=True)
    made.append("cron.db")
    from kazma_core.swarm.task_store import TaskStore

    TaskStore(paths.swarm_tasks_db())
    made.append("swarm_tasks.db")
    from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger

    get_pipeline_logger()
    made.append("pipeline_logs.db")
    from kazma_core.tools import research_session

    research_session._conn().close()
    made.append("research_sessions.db")
    from kazma_core.x_api.publication_store import get_publication_store
    from kazma_core.x_api.reply_store import get_reply_store
    from kazma_core.x_api.schedule import get_x_scheduled_store

    get_publication_store()
    get_reply_store()
    get_x_scheduled_store()
    made += ["x_publications.db", "x_replies.db", "x_scheduled.db"]
    from kazma_core.safety.hitl_gates import ensure_gate_schema

    ensure_gate_schema()
    made.append("hitl_gates.db")
    from kazma_core.runs.incidents import get_incident_store

    # Its table is made at its first use (the run supervisor's, on a running
    # install): made here, or a world of stores would miss it.
    get_incident_store()._connect().close()
    made.append("run_incidents.db")
    from kazma_core.observability import answer_quality, llm_ledger

    answer_quality._connect().close()
    llm_ledger._get_conn()
    made += ["answer_quality.db", "llm_calls.db"]
    from kazma_core.stores.knowledge import get_knowledge_store

    get_knowledge_store()
    made.append("knowledge.db")
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.documents.jobs import DocumentJobRepository
    from kazma_core.documents.repository import DocumentRepository

    root = Path(get_document_storage_root())
    root.mkdir(parents=True, exist_ok=True)
    # The SQLite job queue shares documents.db with the catalog, with its
    # append-only guards: made with it.
    catalog = DocumentRepository(root / "documents.db")
    try:
        jobs = DocumentJobRepository(catalog)
        # The audit and the intake admissions share the file: made by the
        # ingestion service at its start on a running install.
        from kazma_core.documents.admission import IntakeAdmissionStore
        from kazma_core.documents.audit import DocumentAuditStore

        DocumentAuditStore(catalog)
        IntakeAdmissionStore(jobs)
    finally:
        catalog.close()
    made.append("documents.db")
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

    from kazma_core.spaces.scope import OWNER_SPACE_ID

    SandboxBackend(space=OWNER_SPACE_ID)
    made.append("sandbox_emails.db")
    from kazma_core.spaces import activity, shared_files, threads

    threads.ensure_store()
    activity.ensure_store()
    shared_files.ensure_store()
    made += ["thread_scopes.db", "activity.db", shared_files.STORE]
    return made
