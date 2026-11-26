"""The lists ``tests/test_space_doors_gates.py`` holds to the source (Spaces
S4-01, step 1). ``scripts/space_doors_report.py`` reads them beside a report
from a running install.
"""

from __future__ import annotations

#: Every function that makes a connection or a pool to Postgres ->
#: ``door: <the door it serves, in the same file>`` (the door counts each
#: checkout), or ``own sign-in: <why>``: the install's own work on its own
#: connection (path 5 of the design), outside the doors in every step.
MADE: dict[str, str] = {
    "kazma-core/kazma_core/db/postgres_pool.py::PostgresPool.__init__": "door: PostgresPool.connection",
    "kazma-core/kazma_core/checkpoints_pg.py::open_postgres_checkpointer":
        "door: _reported_pool_class.ReportedCheckpointPool.connection",
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend._get_pool":
        "door: PostgresStateBackend._connect",
    "kazma-core/kazma_core/memory/state_backend.py::PostgresStateBackend._open_connection":
        "door: PostgresStateBackend._connect",
    "kazma-core/kazma_core/memory/backends.py::PgvectorBackend._connect": "door: PgvectorBackend._connect",
    "kazma-core/kazma_core/db/postgres_pool.py::refusal_reason":
        "own sign-in: one sign-in that reads why Postgres refuses the pool, also the outage prober's; "
        "it sends no statement",
    "kazma-cli/kazma_cli/setup.py::_kazma_signed_in":
        "own sign-in: setup asks the new server whether Kazma signed in there; the owner's command",
    "kazma-cli/kazma_cli/setup.py::_repoint_memory_backends":
        "own sign-in: setup reads memory's addresses from the settings table of the new server; "
        "the owner's command",
    "kazma-core/kazma_core/backup/pg_move.py::_target_state":
        "own sign-in: the move into the service reads what the target database already holds",
    "kazma-core/kazma_core/backup/pg_move.py::move_from_container":
        "own sign-in: the move into the service restores and counts every table, as the owner",
    "kazma-core/kazma_core/backup/restore_rehearsal.py::_admin":
        "own sign-in: the restore rehearsal makes and drops a scratch database of its own",
    "kazma-core/kazma_core/backup/restore_rehearsal.py::_verify":
        "own sign-in: the restore rehearsal counts the tables of its scratch database",
    "kazma-core/kazma_core/backup/wal_archive.py::archiver_status":
        "own sign-in: reads the server's WAL archiver state, no row of any table",
    "kazma-core/kazma_core/documents/backup.py::promoted_versions_from_postgres":
        "own sign-in: the document backup reads every space's promoted versions, as the owner",
    "kazma-core/kazma_core/documents/backup_pg.py::perform_pg_document_backup":
        "own sign-in: the document backup copies every space's document rows, as the owner",
    "kazma-core/kazma_core/documents/backup_pg.py::restore_pg_document_backup":
        "own sign-in: the document restore writes every space's document rows back, as the owner",
    "kazma-core/kazma_core/migration/exporter.py::_export_postgres_document_content":
        "own sign-in: a migration bundle carries every space's documents, as the owner",
    "kazma-core/kazma_core/migration/pg_bridge.py::_create_kazma_functions":
        "own sign-in: a restore makes Kazma's database functions before its tables",
    "kazma-core/kazma_core/postgres_upgrade.py::server_major":
        "own sign-in: the upgrade asks the server for its version, no row of any table",
    "kazma-core/kazma_core/postgres_upgrade.py::_snapshot":
        "own sign-in: the upgrade counts every table before and after, as the owner",
    "kazma-core/kazma_core/postgres_upgrade.py::copy_database":
        "own sign-in: the upgrade copies the whole database to the new server, as the owner",
    "kazma-core/kazma_core/runtime_ownership.py::_PostgresRuntimeLease.acquire":
        "own sign-in: the one writer's lease, an advisory lock held for the life of the process",
    "kazma-skills/kazma_skills/native/database_client/remote_reads.py::read_postgres":
        "own sign-in: the SQL tool's own address and login, a database the owner named; on Kazma's "
        "own database the lock's policies answer it (review L4)",
}

#: Every function that checks a connection out of a door, by file: a pool's
#: ``connection()``, its ``execute`` or ``execute_one``, or a door or
#: pass-through of ``space_doors.DOOR_FUNCTIONS`` called in its own file.
#: Which door its statements go through is the report's to say, at run time;
#: the callers known to go through none today are :data:`KNOWN_OPEN`.
CHECKOUTS: dict[str, tuple[str, ...]] = {
    "kazma-core/kazma_core/checkpoint_retention.py": (
        "_prune_postgres_checkpoints",
    ),
    "kazma-core/kazma_core/config_store.py": (
        "ConfigStore._collect_prefixed_pg", "ConfigStore._db_get_raw", "ConfigStore._pg_get_raw",
        "ConfigStore._stored_raw", "ConfigStore._write_db_value", "ConfigStore.atomic_update",
        "ConfigStore.batch_set", "ConfigStore.delete", "ConfigStore.get_all", "ConfigStore.get_category",
        "ConfigStore.reconcile_from_yaml", "ConfigStore.reset_all", "ConfigStore.set", "ConfigStore.set_if_absent",
        "ConfigStore.snapshot",
    ),
    "kazma-core/kazma_core/db/pg_backup.py": (
        "verify_required_pg_tables",
    ),
    "kazma-core/kazma_core/db/postgres_pool.py": (
        "_ensure_core_schema",
    ),
    "kazma-core/kazma_core/documents/admission.py": (
        "IntakeAdmissionStore._transaction",
    ),
    "kazma-core/kazma_core/documents/audit.py": (
        "DocumentAuditStore._ensure_pg_guard", "DocumentAuditStore.count", "DocumentAuditStore.list_events",
        "DocumentAuditStore.prune_older_than", "DocumentAuditStore.record",
    ),
    "kazma-core/kazma_core/documents/ingestion.py": (
        "DocumentIngestionService._storage_bytes",
    ),
    "kazma-core/kazma_core/documents/jobs_pg.py": (
        "PostgresDocumentJobRepository._ensure_schema", "PostgresDocumentJobRepository.cancel_claimed",
        "PostgresDocumentJobRepository.claim_next", "PostgresDocumentJobRepository.document_job_ids",
        "PostgresDocumentJobRepository.enqueue", "PostgresDocumentJobRepository.events",
        "PostgresDocumentJobRepository.get", "PostgresDocumentJobRepository.get_by_idempotency_key",
        "PostgresDocumentJobRepository.hand_back", "PostgresDocumentJobRepository.interrupted_intake_jobs",
        "PostgresDocumentJobRepository.latest_jobs_by_document", "PostgresDocumentJobRepository.queue_stats",
        "PostgresDocumentJobRepository.recent_jobs", "PostgresDocumentJobRepository.record_failure",
        "PostgresDocumentJobRepository.recover_expired_leases", "PostgresDocumentJobRepository.release_leases",
        "PostgresDocumentJobRepository.renew_lease", "PostgresDocumentJobRepository.request_cancel",
        "PostgresDocumentJobRepository.tenant_load", "PostgresDocumentJobRepository.transition",
    ),
    "kazma-core/kazma_core/documents/repository_pg.py": (
        "PostgresDocumentRepository._ensure_schema", "PostgresDocumentRepository.active_index_entries",
        "PostgresDocumentRepository.assert_owner", "PostgresDocumentRepository.create_artifact",
        "PostgresDocumentRepository.create_document", "PostgresDocumentRepository.create_version",
        "PostgresDocumentRepository.discard_unpublished_intake", "PostgresDocumentRepository.gc_delete_blob_ids",
        "PostgresDocumentRepository.gc_is_live_reference", "PostgresDocumentRepository.gc_mark",
        "PostgresDocumentRepository.gc_old_unreferenced_blob_ids", "PostgresDocumentRepository.get_artifact",
        "PostgresDocumentRepository.get_blob", "PostgresDocumentRepository.get_document",
        "PostgresDocumentRepository.get_version", "PostgresDocumentRepository.grant_access",
        "PostgresDocumentRepository.has_access", "PostgresDocumentRepository.list_artifacts",
        "PostgresDocumentRepository.list_document_chunks", "PostgresDocumentRepository.list_documents",
        "PostgresDocumentRepository.list_documents_by_access", "PostgresDocumentRepository.list_indexed_libraries",
        "PostgresDocumentRepository.list_versions", "PostgresDocumentRepository.record_indexed_version",
        "PostgresDocumentRepository.register_blob", "PostgresDocumentRepository.revoke_member_access",
        "PostgresDocumentRepository.taken_out", "PostgresDocumentRepository.tenant_referenced_blob_bytes",
        "PostgresDocumentRepository.tenant_references_sha256", "PostgresDocumentRepository.tombstone_document",
        "PostgresDocumentRepository.tombstone_document_chunks",
    ),
    "kazma-core/kazma_core/documents/retention.py": (
        "DocumentGarbageCollector._sweep_idle_catalog",
    ),
    "kazma-core/kazma_core/memory/backends.py": (
        "PgvectorBackend._delete_vector", "PgvectorBackend._ensure_table", "PgvectorBackend._write_vector",
        "PgvectorBackend.probe", "PgvectorBackend.search", "PgvectorBackend.similarities",
    ),
    "kazma-core/kazma_core/memory/chat_history.py": (
        "_id_rows", "_search_postgres", "_texts_of", "checkpoint_message_versions", "conversations_for",
        "sessions_changed_since", "threads_containing",
    ),
    "kazma-core/kazma_core/memory/health.py": (
        "build_memory_health",
    ),
    "kazma-core/kazma_core/memory/state_backend.py": (
        "PostgresStateBackend._count", "PostgresStateBackend._delete_fact", "PostgresStateBackend._digest",
        "PostgresStateBackend._ensure", "PostgresStateBackend._write_belief", "PostgresStateBackend._write_episodes",
        "PostgresStateBackend.available", "PostgresStateBackend.episodes_between",
        "PostgresStateBackend.fetch_beliefs", "PostgresStateBackend.fetch_episodes",
        "PostgresStateBackend.mirror_belief_snapshot", "PostgresStateBackend.present_ids",
        "PostgresStateBackend.row_regions", "PostgresStateBackend.search_beliefs",
        "PostgresStateBackend.search_episodes",
    ),
    "kazma-core/kazma_core/security/service_exposure.py": (
        "_role_answer",
    ),
    "kazma-core/kazma_core/swarm/task_store.py": (
        "TaskStore._init_db", "TaskStore._upsert", "TaskStore.clear", "TaskStore.delete_task",
        "TaskStore.get_all_worker_metrics", "TaskStore.get_paused_tasks", "TaskStore.get_task",
        "TaskStore.get_worker_metrics", "TaskStore.list_tasks", "TaskStore.prune_tasks", "TaskStore.record_orphan",
        "TaskStore.record_worker_metric", "TaskStore.requeue_orphaned_running",
    ),
    "kazma-gateway/kazma_gateway/stores/checkpoint.py": (
        "CheckpointManager._list_checkpoints_postgres", "CheckpointManager._thread_ids",
        "CheckpointManager.adelete_all_threads",
    ),
    "kazma-ui/kazma_ui/health.py": (
        "check_database",
    ),
    "kazma-ui/kazma_ui/hitl_approval.py": (
        "_enumerate_thread_ids",
    ),
    "kazma-ui/kazma_ui/session_manager.py": (
        "SessionManager._delete_row", "SessionManager._evict_if_needed", "SessionManager._load_all_from_primary",
        "SessionManager._load_one_from_primary", "SessionManager._upsert_db", "SessionManager.another_chat_on",
        "SessionManager.clear", "SessionManager.get_by_thread_id", "SessionManager.space_erased",
        "SessionManager.thread_is_exclusive",
    ),
}

#: Kazma's own work known to reach the database through no door today, by
#: the function it is entered at -> (why, the step of S4-01 that gives it
#: one, the functions that check the connection out on its way). A finding
#: is known only when it passes through the entry AND its first caller is
#: one of those: a new store function reached from inside a known sweep is
#: new (review L5). Each was seen in report mode (an app start and stop, the
#: Postgres suites, the maintenance passes, 2026-11-26) or read by the review
#: ("V"). An entry leaves when its step lands; one is added only with the
#: step that ends it.
KNOWN_OPEN: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "kazma-ui/kazma_ui/session_manager.py::SessionManager._load_all_from_primary": (
        "every space's chats, read whole when the chat store starts, nothing bound (section 5: the install's)",
        "step 3, chats",
        ("kazma-ui/kazma_ui/session_manager.py::SessionManager._load_all_from_primary",)),
    "kazma-core/kazma_core/swarm/metrics.py::MetricsCollector._flush_metric": (
        "the workers' metrics table has no space column: the install's, outside the policies (L9)",
        "step 2, the rules",
        ("kazma-core/kazma_core/swarm/task_store.py::TaskStore.record_worker_metric",)),
}
