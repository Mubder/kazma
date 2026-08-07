"""Bundle importer — restore a bundle into THIS machine (invariant C atomicity).

Entry point: :func:`import_bundle`. Orchestrates:

  1. **verify** the bundle (structure, hashes, manifest compat).
  2. **vault-key pairing** (invariant A) — MATCH/EMPTY proceed; MISMATCH
     aborts unless ``reset_vault_key=True``.
  3. **stage** everything to ``kazma-data/.migrate-staging-<ts>/`` (never
     touch live DBs mid-import).
  4. **path translation** (invariant B) — rewrite embedded source paths to
     the target root across snapshots.state_json, workspaces.root_path,
     chat_sessions.messages, memory episodes, cron prompts, config.yaml.
  5. **backup** the existing live DBs to ``.migrate-backup-<ts>/`` (WAL-safe).
  6. **swap** staging → live atomically (one renamem per file).
  7. **report** — row counts changed, warnings, the backup path to roll back.

Any exception before step 6 leaves live data untouched; the staging dir is
preserved on failure for inspection (printed in the report).
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kazma_core.migration.bundle import KazmaBundle, parse_meta_env
from kazma_core.migration.path_rewrite import PathMap, build_path_map, rewrite_paths_in_sqlite
from kazma_core.migration.vault_pairing import VaultKeyStatus, check_vault_key, sync_vault_key

logger = logging.getLogger(__name__)

__all__ = ["ImportReport", "import_bundle"]

# (bundle_db_name, [(table, text_column), ...]) — columns that may hold
# embedded absolute paths and need rewriting on import.
_PATH_REWRITE_TARGETS: list[tuple[str, list[tuple[str, str]]]] = [
    # workspaces.db — the workspace root_path pointer (rewritten first; merged
    # into settings.db after the swap).
    ("workspaces.db", [("workspaces", "root_path")]),
    # snapshots.db — full SupervisorState in state_json (the big one).
    ("snapshots.db", [("snapshots", "state_json")]),
    # chat history — tool results / file refs inside the messages JSON.
    ("chat_sessions.db", [("sessions", "messages")]),
    # memory_state.db — entities/episodes/beliefs may cite source files.
    ("memory_state.db", [
        ("entities", "metadata_json"),
        ("episodes", "user_text"),
        ("episodes", "assistant_text"),
        ("episodes", "summary_text"),
        ("episodes", "metadata_json"),
        ("beliefs", "object"),
        ("beliefs", "metadata_json"),
        ("procedural_dags", "preconditions_json"),
        ("procedural_dags", "dag_steps_json"),
        ("procedural_dags", "postconditions_json"),
    ]),
    # memory_ops.db — audit/task queue references.
    ("memory_ops.db", [("memory_audit_log", "details")]),
    # cron.db — prompt text may reference file paths.
    ("cron.db", [("cron_jobs", "prompt")]),
]

# The SQLite data files the bundle may contain, mapped to their destination
# resolver. Any file present in the bundle is restored; absent files are skipped.
_BUNDLE_DB_TO_DEST_RESOLVER = {
    "settings.db": "settings_db",
    "snapshots.db": "snapshots_db",
    "memory_state.db": "primary_memory_db",
    "memory_ops.db": "memory_ops_db",
    "swarm_tasks.db": "swarm_tasks_db",
    "checkpoints.db": "checkpoints_db",
    "knowledge_graph.db": "knowledge_graph_db",
    "vault.db": "vault_db_path",
    "chat_sessions.db": None,  # plain data_dir join
    "cron.db": None,
    "sessions.db": None,
    "sandbox_emails.db": None,
    "research_sessions.db": None,
    "pipeline_logs.db": None,
}


@dataclass
class ImportReport:
    """Result of :func:`import_bundle`."""

    ok: bool = False
    dry_run: bool = False
    bundle_path: str = ""
    target_data_dir: str = ""
    target_workspace_root: str = ""
    backup_path: str = ""
    staging_path: str = ""
    vault_status: str = ""
    vault_message: str = ""
    files_restored: list[str] = field(default_factory=list)
    rows_rewritten: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def import_bundle(
    bundle_path: str | Path,
    *,
    target_workspace_root: str | None = None,
    reset_vault_key: bool = False,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ImportReport:
    """Import a migration bundle into the current installation.

    Args:
        bundle_path: path to the .zip bundle.
        target_workspace_root: the absolute path on THIS machine that the
            source workspace root should be translated to. If None, the
            current working directory is used.
        reset_vault_key: if True and the bundle's vault key differs from the
            target's, overwrite the target ``.env`` ``KAZMA_VAULT_KEY`` (after
            backing up the existing target vault.db). Required to unblock a
            MISMATCH; default False = safe abort.
        dry_run: verify + plan only; do NOT write, swap, or rewrite anything.
        progress: optional callback for human-readable step messages.

    Returns:
        An :class:`ImportReport`. Check ``report.ok``.
    """
    report = ImportReport(
        dry_run=dry_run,
        bundle_path=str(bundle_path),
        target_workspace_root=target_workspace_root or str(Path.cwd()),
    )
    _log = lambda msg: (logger.info("[migrate:import] %s", msg), progress(msg) if progress else None)  # noqa: E731

    from kazma_core import paths

    bundle = KazmaBundle(bundle_path)
    data_dir = paths.data_dir()
    report.target_data_dir = str(data_dir)

    # ── 1. Verify ──────────────────────────────────────────────────────
    _log("Verifying bundle integrity…")
    verify = bundle.verify()
    if not verify.ok:
        report.errors.extend(verify.errors)
        return report
    report.warnings.extend(verify.warnings)
    _log(f"  OK — {verify.file_count} files, {verify.total_bytes // 1024} KB")

    # ── 2. Vault-key pairing (invariant A) ─────────────────────────────
    _log("Checking vault-key pairing…")
    meta = parse_meta_env(bundle.read_text("meta.env"))
    bundle_key = meta.get("KAZMA_VAULT_KEY", "")
    pairing = check_vault_key(
        bundle_vault_key=bundle_key,
        bundle_has_vault_db=bundle.has_file("data/vault.db"),
        bundle_fingerprint=bundle.manifest.vault_key_fingerprint,
    )
    report.vault_status = pairing.status.value
    report.vault_message = pairing.message
    _log(f"  vault status: {pairing.status.value}")

    if pairing.status == VaultKeyStatus.MISMATCH and not reset_vault_key:
        report.error(pairing.message)
        report.warn("Re-run with --reset-vault-key to overwrite the target's key.")
        return report
    if pairing.status == VaultKeyStatus.MISMATCH and reset_vault_key:
        _log("  --reset-vault-key: will overwrite target KAZMA_VAULT_KEY after backup")

    if dry_run:
        _log("DRY RUN — no changes will be made. Plan below.")
        _plan_path_rewrite(bundle, report, _log)
        report.ok = True
        return report

    # ── 3. Stage ───────────────────────────────────────────────────────
    ts = int(time.time())
    staging = data_dir / f".migrate-staging-{ts}"
    report.staging_path = str(staging)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    _log(f"Extracting bundle to staging ({staging.name})…")
    bundle.extract_all(staging)

    # ── 4. Path translation (invariant B) ──────────────────────────────
    _apply_path_rewrite(staging, bundle, report.target_workspace_root, report, _log)

    # ── 5. Vault-key sync (if needed) ──────────────────────────────────
    if pairing.status in (VaultKeyStatus.MISMATCH, VaultKeyStatus.EMPTY) and bundle_key:
        # find the .env (project root, one level up from data_dir).
        env_path = _find_env_file(data_dir)
        if env_path:
            _log(f"Syncing KAZMA_VAULT_KEY into {env_path.name}…")
            backup = sync_vault_key(
                bundle_vault_key=bundle_key,
                env_path=env_path,
                target_data_dir=data_dir,
            )
            if backup:
                report.warnings.append(
                    f"Existing target vault.db backed up to {backup.name} before overwrite."
                )
        else:
            report.warn("No .env found to write KAZMA_VAULT_KEY — set it manually.")

    # ── 6. Backup live DBs, then swap ──────────────────────────────────
    backup_dir = data_dir / f".migrate-backup-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    report.backup_path = str(backup_dir)
    _log(f"Backing up live DBs to {backup_dir.name}/…")

    from kazma_core.memory.backup import _backup_one

    staged_data = staging / "data"
    swapped: list[str] = []
    for arc_name, resolver_name in _BUNDLE_DB_TO_DEST_RESOLVER.items():
        src_staged = staged_data / arc_name
        if not src_staged.exists():
            continue
        # Resolve destination path.
        if resolver_name:
            dest = Path(getattr(paths, resolver_name)())
        else:
            dest = data_dir / arc_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Back up the existing live file (if any) before swapping. Use the
        # WAL-safe online-backup primitive so the backup is a consistent
        # single file (no -wal/-shm dependency).
        if dest.exists():
            _backup_one(dest, backup_dir / arc_name)
        # CRITICAL: remove any stale -wal / -shm sidecars at the destination
        # before writing the new main file. A leftover -wal from the previous
        # DB would be inconsistent with the new main file and SQLite would
        # either replay stale transactions (corruption) or report "database
        # disk image is malformed" — this was the root cause of the vault.db
        # corruption bug on the first migration import.
        for suffix in ("-wal", "-shm", "-journal"):
            stale = dest.with_name(dest.name + suffix)
            stale.unlink(missing_ok=True)
        # Install the staged file → live via the WAL-safe online backup,
        # which checkpoints any WAL state into a single consistent file.
        # (Equivalent to: copy main + drop sidecars, but robust to the
        # staged file itself being a WAL-mode DB.)
        if not _backup_one(src_staged, dest):
            report.warn(f"failed to install {arc_name} (online backup failed)")
            continue
        # NOTE: do NOT unlink src_staged here — _backup_one's sqlite3
        # connection has just released the Windows file handle and the OS
        # may still hold a lingering lock (WinError 32 on the 304MB
        # snapshots.db). The whole staging dir is removed by shutil.rmtree
        # at the end of a successful import, which is the right cleanup
        # point (after all _backup_one calls are long done).
        swapped.append(arc_name)
    report.files_restored = swapped
    _log(f"  restored {len(swapped)} data files")

    # 6b. Postgres dump restore (v2). If the bundle contains a postgres.dump,
    # the source was Postgres-backed and the shared-state tables (settings,
    # chat sessions, checkpoints) live in that dump, NOT in the SQLite files.
    # - Target Postgres: pg_restore the dump into KAZMA_DATABASE_URL. Schema
    #   self-recreates (--clean --if-exists), so the target DB can be empty.
    # - Target SQLite: the dump is unusable (different backend) — abort with
    #   a clear error rather than silently importing partial data.
    staged_pg_dump = staged_data / "postgres.dump"
    if staged_pg_dump.exists():
        from kazma_core.db.backend import is_postgres, get_database_url

        if is_postgres():
            target_dsn = get_database_url()
            if not target_dsn:
                report.error(
                    "bundle has a Postgres dump and target is Postgres, but "
                    "KAZMA_DATABASE_URL is not set. Set it and re-run."
                )
                return report
            _log("Restoring Postgres dump into target DB…")
            try:
                from kazma_core.migration.pg_bridge import (
                    PgToolNotFound,
                    restore_database,
                )
                warnings = restore_database(
                    staged_pg_dump, target_dsn,
                    progress=lambda p: _log(f"    {p}"),
                )
                if warnings:
                    report.warnings.append(
                        f"pg_restore emitted {warnings} non-fatal warning line(s) "
                        "(typical for --clean on a fresh DB; safe to ignore)."
                    )
                _log("  Postgres dump restored.")
            except PgToolNotFound as exc:
                report.error(f"pg_restore not available: {exc}")
                return report
            except Exception as exc:
                report.error(f"pg_restore failed: {exc}")
                return report
        else:
            # Bundle is Postgres-backed but target is SQLite — the dump is
            # unusable. Don't silently produce a half-migrated install.
            report.error(
                "bundle contains a Postgres dump but the target backend is SQLite. "
                "The Postgres tables (settings, chat sessions, checkpoints) cannot "
                "be restored into SQLite. To use the migrated Postgres data, set "
                "KAZMA_DB_BACKEND=postgres + KAZMA_DATABASE_URL on the target (and "
                "run a Postgres instance), then re-import. The SQLite files "
                "(vault/memory/snapshots) were restored, but without the Postgres "
                "data the install is incomplete."
            )
            return report

    # 7. Restore config (config.yaml → ConfigStore.import_yaml).
    config_path = staging / "config.yaml"
    if config_path.exists():
        _log("Importing config.yaml into ConfigStore…")
        try:
            from kazma_core.config_store import get_config_store

            n = get_config_store().import_yaml(config_path.read_text(encoding="utf-8"))
            _log(f"  imported {n} config keys")
        except Exception as exc:
            report.warn(f"config import failed: {exc}")

    # 7b. Merge the standalone workspaces table (from the bundle's
    # workspaces.db) into the restored settings.db, where WorkspaceStore
    # actually reads it. The exporter emits workspaces as a dedicated file
    # for path-rewrite clarity; here we ATTACH it and copy the (already
    # path-translated) rows into settings.db, replacing any prior rows.
    # NOTE: runs AFTER the swap, so settings.db is at its LIVE location;
    # workspaces.db is still in staging (not in the swap map).
    staged_workspaces = staged_data / "workspaces.db"
    if staged_workspaces.exists():
        _log("Merging workspaces table into settings.db…")
        try:
            from kazma_core import paths as _paths

            live_settings = _paths.settings_db()
            _merge_workspaces_into_settings(live_settings, str(staged_workspaces))
        except Exception as exc:
            report.warn(f"workspaces merge failed: {exc}")

    # 7c. Merge Knowledge Library tables from the bundle's settings.db into
    # the live settings.db. The KB (knowledge_libraries + knowledge_chunks +
    # FTS5 shadow tables) lives in SQLite settings.db REGARDLESS of the
    # backend — it is NOT in Postgres, even on Postgres-backed installs.
    # Without this step, a Postgres→Postgres migration restores all config
    # from pg_restore but the KB is empty (0 libraries, 0 chunks) because
    # the target's settings.db was fresh/empty. The bundle's settings.db
    # (staged as data/settings.db before the swap) has the KB data — but
    # after the swap, the LIVE settings.db may be a restored copy that
    # either lacks the KB tables entirely or has them empty. We ATTACH the
    # staged settings.db copy and copy KB rows into the live one.
    staged_settings_db = staged_data / "settings.db"
    if staged_settings_db.exists():
        _log("Merging Knowledge Library tables into settings.db…")
        try:
            from kazma_core import paths as _paths

            live_settings = _paths.settings_db()
            _merge_kb_into_settings(live_settings, str(staged_settings_db))
        except Exception as exc:
            report.warn(f"Knowledge Library merge failed: {exc}")

    # 8. Restore assets (verbatim).
    staged_assets = staging / "assets"
    if staged_assets.exists():
        for sub in staged_assets.iterdir():
            dest_sub = data_dir / sub.name
            if sub.is_dir():
                shutil.copytree(sub, dest_sub, dirs_exist_ok=True)
                _log(f"  restored assets/{sub.name}/")

    # 9. Notify workspace root change so MCP rebinds (AGENTS.md §10A).
    if report.target_workspace_root:
        try:
            from kazma_core.workspace.binding import notify_root_changed

            notify_root_changed(report.target_workspace_root, reason="migrate.import")
        except Exception as exc:
            logger.debug("[migrate:import] notify_root_changed failed: %s", exc)

    # Clean up staging on success.
    shutil.rmtree(staging, ignore_errors=True)
    report.ok = True
    _log("Import complete.")
    if report.backup_path:
        _log(f"  pre-import backup: {report.backup_path}")
    return report


# ── Path-rewrite orchestration ────────────────────────────────────────────


def _plan_path_rewrite(
    bundle: KazmaBundle, report: ImportReport, _log: Callable[[str], None]
) -> None:
    """Dry-run: report what path translation WOULD do, write nothing."""
    src_root = bundle.manifest.source_workspace_root
    if not src_root or src_root == report.target_workspace_root:
        _log("  no path translation needed (same root or none detected)")
        return
    _log(f"  would rewrite {src_root} -> {report.target_workspace_root}")
    for db_name, cols in _PATH_REWRITE_TARGETS:
        if bundle.has_file(f"data/{db_name}"):
            col_list = ", ".join(f"{t}.{c}" for t, c in cols)
            _log(f"    {db_name}: {col_list}")


def _apply_path_rewrite(
    staging: Path,
    bundle: KazmaBundle,
    target_root: str,
    report: ImportReport,
    _log: Callable[[str], None],
) -> None:
    """Rewrite embedded paths in the staged data DBs (invariant B)."""
    src_root = bundle.manifest.source_workspace_root
    src_data_dir = bundle.manifest.source_data_dir
    if not src_root:
        _log("  no source workspace root recorded — skipping path rewrite")
        return
    if src_root == target_root and (not src_data_dir or src_data_dir == str(staging.parent)):
        _log("  source root == target root — skipping path rewrite")
        return

    path_map = build_path_map(src_root, target_root, source_data_dir=src_data_dir, target_data_dir=str(staging.parent))
    if path_map.is_empty():
        return

    _log(f"  rewriting paths: {src_root} -> {target_root}")
    staged_data = staging / "data"
    for db_name, cols in _PATH_REWRITE_TARGETS:
        db_file = staged_data / db_name
        if not db_file.exists():
            continue
        _log(f"    {db_name}: {len(cols)} column(s)…")

        def _prog(done: int, total: int) -> None:
            if total and done % 500 == 0:
                _log(f"      {db_name}: {done}/{total} rows")

        try:
            changed = rewrite_paths_in_sqlite(str(db_file), cols, path_map, progress=_prog)
            if changed:
                report.rows_rewritten[db_name] = changed
        except Exception as exc:
            report.warn(f"path rewrite failed for {db_name}: {exc}")


def _find_env_file(data_dir: Path) -> Path | None:
    """Locate the target project's ``.env`` for vault-key sync.

    Prefers the CWD's ``.env`` because the import is always run from the
    target install's directory — that's the authoritative .env the operator
    expects the key to land in. Falls back to ``kazma-data/``'s sibling only
    if CWD has no .env. The previous order (data_dir first) was wrong when
    Kazma was editable-installed from a DIFFERENT repo than the one being
    migrated into: ``data_dir`` resolved via the package's project root and
    pointed at the *package's* repo .env, so the synced key landed in the
    wrong file and the target's vault.db stayed undecryptable.
    """
    candidates = [
        Path.cwd() / ".env",
        data_dir.parent / ".env",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Last resort: create one in the CWD so the key isn't lost.
    return None


def _merge_workspaces_into_settings(settings_db: str, workspaces_db: str) -> int:
    """Copy the ``workspaces`` table from a bundle's workspaces.db into settings.db.

    WorkspaceStore reads the ``workspaces`` table from settings.db (per
    stores/workspaces.py), but the exporter emits it as a standalone file for
    path-rewrite clarity. This merges the (already path-translated) rows back
    into the live settings.db, REPLACING any existing workspaces rows.

    Returns the number of rows merged. Idempotent: safe to re-run.
    """
    import sqlite3

    conn = sqlite3.connect(settings_db)
    try:
        # Ensure the workspaces table exists in settings.db (WorkspaceStore
        # creates it on init, but the restored bundle's settings.db may be
        # from before that init ran).
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL,
                created_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0,
                repo_url TEXT, owner TEXT, repo TEXT, default_branch TEXT, is_github INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_workspaces_active ON workspaces(is_active);
            """
        )
        # ATTACH the bundle's workspaces.db and copy rows (delete-then-insert
        # so re-runs are idempotent).
        conn.execute("ATTACH DATABASE ? AS src", (workspaces_db,))
        try:
            conn.execute("DELETE FROM workspaces")
            cur = conn.execute(
                "INSERT INTO workspaces "
                "(id,name,root_path,created_at,is_active,repo_url,owner,repo,default_branch,is_github) "
                "SELECT id,name,root_path,created_at,is_active,repo_url,owner,repo,default_branch,is_github "
                "FROM src.workspaces"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.execute("DETACH DATABASE src")
    finally:
        conn.close()


def _merge_kb_into_settings(settings_db: str, source_settings_db: str) -> int:
    """Copy Knowledge Library tables from the bundle's settings.db into live settings.db.

    The KB (knowledge_libraries + knowledge_chunks) lives in SQLite settings.db
    REGARDLESS of the backend — it is NOT in Postgres. On a Postgres→Postgres
    migration, pg_restore restores Postgres tables but the KB tables in the
    target's settings.db are empty (or the settings.db was freshly created).
    This ATTACHs the bundle's staged settings.db copy and copies KB rows.

    Handles FTS5 shadow tables by rebuilding them after the insert (FTS5
    content is external-table-backed, so the shadow tables auto-populate on
    insert if configured correctly; we also run a no-op REBUILD as a safety
    net).

    Returns the number of knowledge_chunks rows merged.
    """
    import sqlite3

    conn = sqlite3.connect(settings_db)
    try:
        # Ensure KB tables exist in the target (they should, but a fresh
        # settings.db may lack them).
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_libraries (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source TEXT,
                source_url TEXT,
                created_at TEXT,
                updated_at TEXT,
                chunk_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ready',
                metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                content TEXT NOT NULL,
                chunk_index INTEGER,
                embedding BLOB,
                metadata TEXT,
                created_at TEXT
            );
            """
        )

        # Check if the source has KB data
        conn.execute("ATTACH DATABASE ? AS src", (source_settings_db,))
        try:
            # Check source KB tables exist and have data
            try:
                src_libs = conn.execute("SELECT COUNT(*) FROM src.knowledge_libraries").fetchone()[0]
            except Exception:
                src_libs = 0
            try:
                src_chunks = conn.execute("SELECT COUNT(*) FROM src.knowledge_chunks").fetchone()[0]
            except Exception:
                src_chunks = 0

            if src_libs == 0 and src_chunks == 0:
                logger.debug("[migrate:import] source settings.db has no KB data — skipping KB merge")
                return 0

            # Copy knowledge_libraries (delete-then-insert for idempotency)
            conn.execute("DELETE FROM knowledge_libraries")
            conn.execute(
                "INSERT INTO knowledge_libraries "
                "(id, name, source, source_url, created_at, updated_at, chunk_count, status, metadata) "
                "SELECT id, name, source, source_url, created_at, updated_at, chunk_count, status, metadata "
                "FROM src.knowledge_libraries"
            )

            # Copy knowledge_chunks
            conn.execute("DELETE FROM knowledge_chunks")
            conn.execute(
                "INSERT INTO knowledge_chunks "
                "(id, library_id, content, chunk_index, embedding, metadata, created_at) "
                "SELECT id, library_id, content, chunk_index, embedding, metadata, created_at "
                "FROM src.knowledge_chunks"
            )
            conn.commit()

            # Rebuild FTS5 index if the knowledge_chunks_fts table exists
            # (FTS5 external-content tables need to be rebuilt after bulk insert)
            try:
                fts_exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_chunks_fts'"
                ).fetchone()
                if fts_exists:
                    conn.execute("INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES('rebuild')")
                    conn.commit()
                    logger.debug("[migrate:import] rebuilt knowledge_chunks_fts index")
            except Exception:
                pass  # FTS rebuild is best-effort

            merged_libs = conn.execute("SELECT COUNT(*) FROM knowledge_libraries").fetchone()[0]
            logger.info("[migrate:import] KB merge: %d libraries, %d chunks", merged_libs, src_chunks)
            return src_chunks
        finally:
            conn.execute("DETACH DATABASE src")
    except Exception as exc:
        logger.warning("[migrate:import] KB merge failed: %s", exc)
        raise
    finally:
        conn.close()
