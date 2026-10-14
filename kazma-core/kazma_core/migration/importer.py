"""Bundle importer — restore a bundle into THIS machine (invariant C atomicity).

Entry point: :func:`import_bundle`. Orchestrates:

  1. **verify** the bundle (structure, hashes, manifest compat).
  2. **vault-key pairing** (invariant A) — MATCH/EMPTY proceed; MISMATCH
     aborts unless ``reset_vault_key=True``.
  3. **stage** everything to ``kazma-data/.migrate-staging-<ts>/`` (never
     touch live DBs mid-import).
  4. **path translation** (invariant B) — rewrite embedded source paths to
     the target root across workspaces.root_path, chat_sessions.messages,
     memory episodes, cron prompts, config.yaml.
  5. **backup** the existing live DBs to ``.migrate-backup-<ts>/`` (WAL-safe).
  6. **preflight** Postgres-bundle vs SQLite-target (abort before live writes).
     Then **pg_restore** (if dump present), then **swap** SQLite atomically.
  7. **files** — the data dir's other entries (``assets/``), each asked
     ``store_registry.carried_in_bundle`` again; a file the target had and
     the bundle replaces is kept in ``.migrate-backup-<ts>/files/``.
  8. **report** — row counts changed, warnings, the backup path to roll back.

Any exception before the SQLite swap leaves live SQLite untouched; a failed
swap rolls ``.migrate-backup-<ts>`` back. Staging is preserved on failure.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.migration.bundle import KazmaBundle, parse_meta_env
from kazma_core.migration.exporter import _DATA_DIR_DBS as _EXPORTED_DATA_DIR_DBS
from kazma_core.migration.path_rewrite import build_path_map, rewrite_paths_in_sqlite
from kazma_core.migration.vault_pairing import VaultKeyStatus, check_vault_key, sync_vault_key

logger = logging.getLogger(__name__)

__all__ = ["ImportReport", "import_bundle"]

# (bundle_db_name, [(table, text_column), ...]) — columns that may hold
# embedded absolute paths and need rewriting on import.
_PATH_REWRITE_TARGETS: list[tuple[str, list[tuple[str, str]]]] = [
    # workspaces.db — the workspace root_path pointer (rewritten first; merged
    # into settings.db after the swap).
    ("workspaces.db", [("workspaces", "root_path")]),
    # snapshots.db is not carried (store_registry, Time Travel audit TT-05);
    # an older bundle's copy is not restored.
    # chat history — tool results / file refs inside the messages JSON.
    ("chat_sessions.db", [("sessions", "messages")]),
    ("chat_sessions_spool.db", [("spool", "payload")]),
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
    ("memory_ops.db", [
        ("memory_audit_log", "reason"),
        ("memory_audit_log", "state_before_json"),
        ("memory_audit_log", "state_after_json"),
    ]),
    # cron.db — prompt text may reference file paths.
    ("cron.db", [("cron_jobs", "prompt")]),
    # agent_artifacts.db — scratchpad findings and drafts can cite files.
    ("agent_artifacts.db", [("agent_artifacts", "value")]),
    # task_ledgers.db — goals, steps and findings name files being worked on.
    ("task_ledgers.db", [
        ("task_ledgers", "goal"),
        ("task_ledgers", "next_action"),
        ("task_ledgers", "steps"),
        ("task_ledgers", "findings"),
    ]),
]

# The SQLite data files the bundle may contain, mapped to their destination
# resolver. Any file present in the bundle is restored (swapped onto live);
# absent files are skipped.
#
# NOTE: ``settings.db`` is intentionally ABSENT from this map. A bundle in
# the older layout carries a KB-only ``settings.db`` (the Knowledge Library
# moved to its own ``knowledge.db`` on 2026-10-14, KB-E10). It must NEVER be
# swapped onto the live settings.db (that would clobber the target's config
# + the already-merged workspaces table): ``_stage_legacy_knowledge`` builds
# ``knowledge.db`` from it in staging, through the store's own schema.
#
# The plain data_dir joins come from the exporter's list, which comes from
# ``kazma_core.store_registry`` — one list, so a store the exporter carries
# is a store the importer restores. (Both used to be hand-kept, and both
# lacked agent_artifacts.db, x_scheduled.db, hitl_gates.db and five more.)
_BUNDLE_DB_TO_DEST_RESOLVER: dict[str, str | None] = {
    "memory_state.db": "primary_memory_db",
    "memory_ops.db": "memory_ops_db",
    "swarm_tasks.db": "swarm_tasks_db",
    "checkpoints.db": "checkpoints_db",
    "knowledge_graph.db": "knowledge_graph_db",
    "vault.db": "vault_db_path",
    **{name: None for name in _EXPORTED_DATA_DIR_DBS},  # plain data_dir join
}


def _dest_map_for(staged_data: Path) -> dict[str, str | None]:
    """The static map plus declared families present in this bundle.

    Family members (``checkpoints_<tenant>.db``) are named at runtime, so
    the bundle — not a list — says which exist.
    """
    from kazma_core.store_registry import bundle_family_patterns

    dest = dict(_BUNDLE_DB_TO_DEST_RESOLVER)
    for pattern in bundle_family_patterns():
        for staged in staged_data.glob(pattern):
            dest.setdefault(staged.name, None)
    return dest


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


def _live_server_detected() -> tuple[bool, str]:
    """Detect a running Kazma server before swapping live DBs (audit M-P6).

    Primary signal: a fresh ``system.heartbeat.epoch`` in ConfigStore (the
    app stamps it every 60s; <120s old = live process sharing THIS data
    dir — exactly the process whose WAL frames a swap would discard).

    Optional signal (``KAZMA_MIGRATE_CHECK_PORT=1``): something listening
    on KAZMA_PORT/PORT (default 9090). Off by default because port probes
    are environment-wide (a dev server on 9090 would block imports of
    unrelated test data dirs).

    Returns ``(detected, why)``.
    """
    try:
        from kazma_core.config_store import get_config_store

        hb = get_config_store().get("system.heartbeat.epoch", None)
        if hb is not None:
            try:
                import time as _time

                if _time.time() - float(hb) < 120:
                    return True, "fresh heartbeat (system.heartbeat.epoch <120s old)"
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    import os as _os

    if (_os.environ.get("KAZMA_MIGRATE_CHECK_PORT") or "").strip().lower() in (
        "1", "true", "on", "yes",
    ):
        import socket as _socket

        try:
            port = int(_os.environ.get("KAZMA_PORT") or _os.environ.get("PORT") or 9090)
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return True, f"a process is listening on 127.0.0.1:{port}"
        except Exception:
            pass
    return False, ""


def _guard_restored_x(swapped: list[str], report: ImportReport) -> bool:
    # Restoring a publishing database never restores unattended authority.
    if any(name in swapped for name in ("x_publications.db", "x_scheduled.db", "x_replies.db")):
        try:
            from kazma_core.x_api.restore import pause_restored_publishing

            pause_restored_publishing()
            report.warn("X publishing is paused after restore. Verify the connected account and resume in X Studio; old queues remain held.")
        except Exception as exc:
            report.error(f"X publishing restore guard failed: {exc}")
            return False

    return True


def import_bundle(
    bundle_path: str | Path,
    *,
    target_workspace_root: str | None = None,
    reset_vault_key: bool = False,
    dry_run: bool = False,
    force: bool = False,
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

    # ── 0. Live-server gate (audit M-P6) ───────────────────────────────
    # Swapping DB files under a live process discards WAL frames and fails
    # on Windows (os.replace over open handles). Detect a running server
    # and abort unless the operator explicitly passes force.
    if not dry_run:
        live, why = _live_server_detected()
        if live and not force:
            report.error(
                f"Refusing import: a live Kazma server was detected ({why}). "
                "Stop the server (or kazma_guard) first, or pass --force to "
                "proceed at your own risk — uncheckpointed WAL frames will "
                "be discarded."
            )
            return report
        if live and force:
            report.warnings.append(
                f"Live server detected ({why}) — proceeding anyway (--force); "
                "uncheckpointed WAL frames may be discarded."
            )

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

    # Internal inconsistency (tampered meta.env) is a HARD abort that
    # --reset-vault-key MUST NOT bypass — otherwise an attacker-crafted bundle
    # whose meta.env key disagrees with its manifest fingerprint could
    # overwrite the target's legitimate KAZMA_VAULT_KEY via sync_vault_key
    # and swap in the attacker's vault.db. (audit finding)
    if pairing.status == VaultKeyStatus.INCONSISTENT:
        report.error(pairing.message)
        report.error(
            "Refusing import: bundle is internally inconsistent. "
            "--reset-vault-key cannot override a tamper signal."
        )
        return report

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
            # No .env to persist KAZMA_VAULT_KEY into. If we continued, the
            # bundle's vault.db would be swapped in (step 6) but its matching
            # key would be written NOWHERE → every encrypted secret becomes
            # undecryptable on next boot (the #1 silent-breakage mode invariant
            # A exists to prevent), while the import still reported ok=True.
            # Treat as fatal: the operator must provide a .env first.
            report.error(
                "No .env found to write KAZMA_VAULT_KEY. The bundle's vault.db "
                "cannot decrypt without its matching key — create a .env (or set "
                "KAZMA_VAULT_KEY in the environment) and re-run."
            )
            return report

    # ── 6. Preflight PG bundle, backup live DBs, pg_restore, then SQLite swap
    # (audit C-2). SQLite-target + postgres.dump used to abort AFTER live
    # SQLite files were already replaced.
    from kazma_core.memory.backup import _backup_one

    staged_data = staging / "data"
    if not _stage_legacy_knowledge(staged_data, report, _log):
        return report
    staged_pg_dump = staged_data / "postgres.dump"
    if staged_pg_dump.exists():
        from kazma_core.db.backend import get_database_url, is_postgres

        if not is_postgres():
            report.error(
                "bundle contains a Postgres dump but the target backend is SQLite. "
                "The Postgres tables (settings, chat sessions, checkpoints) cannot "
                "be restored into SQLite. Set KAZMA_DB_BACKEND=postgres + "
                "KAZMA_DATABASE_URL on the target and re-import. Live SQLite "
                "files were not modified."
            )
            return report
        if not get_database_url():
            report.error(
                "bundle has a Postgres dump and target is Postgres, but "
                "KAZMA_DATABASE_URL is not set. Set it and re-run. Live "
                "SQLite files were not modified."
            )
            return report

    backup_dir = data_dir / f".migrate-backup-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    report.backup_path = str(backup_dir)
    _log(f"Backing up live DBs to {backup_dir.name}/…")

    for arc_name, resolver_name in _dest_map_for(staged_data).items():
        src_staged = staged_data / arc_name
        if not src_staged.exists():
            continue
        dest = _dest_for_bundle_db(arc_name, resolver_name, paths, data_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            _backup_one(dest, backup_dir / arc_name)

    if staged_pg_dump.exists():
        from kazma_core.db.backend import get_database_url
        from kazma_core.migration.pg_bridge import PgToolNotFound, restore_database

        target_dsn = get_database_url()
        _log("Restoring Postgres dump into target DB (before SQLite swap)…")
        try:
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

    swapped: list[str] = []
    install_failures: list[str] = []
    for arc_name, resolver_name in _dest_map_for(staged_data).items():
        src_staged = staged_data / arc_name
        if not src_staged.exists():
            continue
        dest = _dest_for_bundle_db(arc_name, resolver_name, paths, data_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            stale = dest.with_name(dest.name + suffix)
            stale.unlink(missing_ok=True)
        swap_tmp = dest.with_name(dest.name + ".swap-tmp")
        swap_tmp.unlink(missing_ok=True)
        if not _backup_one(src_staged, swap_tmp):
            report.error(f"failed to install {arc_name} (online backup failed)")
            install_failures.append(arc_name)
            swap_tmp.unlink(missing_ok=True)
            continue
        try:
            os.replace(swap_tmp, dest)
        except OSError as exc:
            report.error(f"failed to swap {arc_name} into place: {exc}")
            install_failures.append(arc_name)
            swap_tmp.unlink(missing_ok=True)
            continue
        swapped.append(arc_name)
    report.files_restored = swapped
    if install_failures:
        _log(
            f"  {len(install_failures)} DB(s) failed to install — rolling back "
            + ", ".join(install_failures)
        )
        _rollback_sqlite_swap(backup_dir, swapped, paths, data_dir)
        report.files_restored = []
        return report
    _log(f"  restored {len(swapped)} data files")

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

    if not _guard_restored_x(swapped, report):
        _log("  X restore guard failed — rolling back installed SQLite files")
        _rollback_sqlite_swap(backup_dir, swapped, paths, data_dir)
        report.files_restored = []
        return report

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

    # 7c. The Knowledge Library travels as its own store, knowledge.db, and
    # was installed with the other databases in step 6 -- staged, verified and
    # swapped like them (Knowledge audit KB-E01/KB-E10). A bundle in the older layout
    # had it staged into knowledge.db through the store's own schema in
    # step 6 (``_stage_legacy_knowledge``). The merge that ran here wrote the
    # live settings.db AFTER the swap, from importer-local DDL whose columns
    # the store does not have: it failed, and on a fresh target it left
    # tables every later KnowledgeStore() refused to open.

    # 8. Restore the data dir's other entries (assets/), verbatim. Each is
    #    asked again whether a bundle carries it, so a bundle cannot plant
    #    an install identity or a database; a file it replaces is kept in
    #    the pre-import backup. Only folders were restored before, so the
    #    swarm's worker registry could not have come back even if exported.
    file_failures = _restore_data_dir_files(
        staging / "assets", data_dir, backup_dir / "files", report, _log
    )

    # 8b. Restore the document store — documents.db + the content-addressed
    # tree — into the target's document-store root (resolved AFTER config
    # import so a custom storage_root is honored). documents.db carries no
    # embedded paths, so it needs no rewrite.
    file_failures += _restore_documents_reported(staging, staged_data, report, _log)

    # 9. Notify workspace root change so MCP rebinds (AGENTS.md §10A).
    if report.target_workspace_root:
        try:
            from kazma_core.workspace.binding import notify_root_changed

            notify_root_changed(report.target_workspace_root, reason="migrate.import")
        except Exception as exc:
            logger.debug("[migrate:import] notify_root_changed failed: %s", exc)

    # Clean up staging on success. A file that could not be restored is still
    # there, and the operator needs it.
    if file_failures:
        _log(f"  the files that could not be restored stay in {staging}")
    else:
        shutil.rmtree(staging, ignore_errors=True)
    # Only succeed if every DB swapped cleanly. A partial install (some DBs
    # swapped, one failed) must not report ok=True — the operator would believe
    # the migration succeeded while live data is stale/missing (audit finding).
    report.ok = not install_failures and not file_failures
    if install_failures:
        _log(f"  {len(install_failures)} DB(s) failed to install: "
             + ", ".join(install_failures))
    _log("Import complete.")
    if report.backup_path:
        _log(f"  pre-import backup: {report.backup_path}")
    return report


def _restore_data_dir_files(
    src_root: Path,
    data_dir: Path,
    backup_root: Path,
    report: ImportReport,
    log: Callable[[str], object],
) -> list[str]:
    """Put the bundle's data-dir entries (``assets/``) into *data_dir*.

    Folders merge into the target's. A file replaces the target's copy only
    when the two differ, and the target's copy is first kept under
    *backup_root*, inside the pre-import backup, so an import loses nothing
    of the target's. An entry this install keeps for itself -- its identity,
    its backups, a database -- is refused: every name the bundle holds is
    asked :func:`carried_in_bundle` again, the exporter's question. Returns
    what could not be restored.
    """
    import filecmp

    from kazma_core.store_registry import carried_in_bundle

    if not src_root.is_dir():
        return []
    failures: list[str] = []
    replaced = 0
    try:
        items = sorted(src_root.iterdir())
    except OSError as exc:
        items = []
        failures.append(f"assets/ ({exc.strerror or exc})")
    for item in items:
        label = item.name + ("/" if item.is_dir() else "")
        if item.is_symlink() or not carried_in_bundle(item.name):
            report.warn(f"not restored: {label} (this install keeps its own)")
            continue
        try:
            sources = [item] if item.is_file() else sorted(
                p for p in item.rglob("*") if p.is_file() and not p.is_symlink()
            )
        except OSError as exc:
            failures.append(f"{label} ({exc.strerror or exc})")
            continue
        for src in sources:
            rel = src.relative_to(src_root)
            dest = data_dir / rel
            try:
                if dest.exists() and not dest.is_file():
                    failures.append(f"{rel.as_posix()} (a folder of that name is here)")
                    continue
                if dest.is_file():
                    if filecmp.cmp(src, dest, shallow=False):
                        continue
                    keep = backup_root / rel
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, keep)
                    replaced += 1
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError as exc:
                failures.append(f"{rel.as_posix()} ({exc.strerror or exc})")
        report.files_restored.append(label)
        log(f"  restored {label}")
    if replaced:
        log(f"  {replaced} file(s) this install had are kept in {backup_root}")
    if failures:
        report.error(
            f"{len(failures)} file(s) could not be restored: " + ", ".join(failures[:10])
        )
    return failures


def _dest_for_bundle_db(
    arc_name: str,
    resolver_name: str | None,
    paths: Any,
    data_dir: Path,
) -> Path:
    if resolver_name:
        return Path(getattr(paths, resolver_name)())
    return data_dir / arc_name


def _rollback_sqlite_swap(
    backup_dir: Path,
    swapped: list[str],
    paths: Any,
    data_dir: Path,
) -> None:
    """Restore live SQLite files from the pre-swap backup (audit C-2)."""
    from kazma_core.memory.backup import _backup_one

    for arc_name in swapped:
        resolver_name = _BUNDLE_DB_TO_DEST_RESOLVER.get(arc_name)
        dest = _dest_for_bundle_db(arc_name, resolver_name, paths, data_dir)
        bak = backup_dir / arc_name
        if bak.exists():
            try:
                _backup_one(bak, dest)
            except Exception:
                logger.warning(
                    "[migrate:import] rollback of %s failed", arc_name, exc_info=True
                )
            continue
        # File did not exist live before the swap — drop the new one.
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            logger.debug("[migrate:import] could not unlink rolled-back %s", dest)


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


def _restore_documents_reported(
    staging: Path, staged_data: Path, report: Any, _log: Any
) -> list[str]:
    """Restore the document store; what could not be restored (or ``[]``).

    A document store the bundle carries and the import could not put back is
    a failed import (DOC-A8, 2026-10-13): it was a warning, the import said
    ok, and the target ran with its documents' files missing.
    """
    try:
        _restore_document_store(staging, staged_data, report, _log)
    except Exception as exc:  # noqa: BLE001 - reported as the import's failure
        failure = f"document store restore failed: {exc}"
        report.error(failure)
        _log(f"  ✗ {failure}")
        return ["document store"]
    return []


def _restore_document_store(
    staging: Path, staged_data: Path, report: Any, _log: Any
) -> None:
    """Restore documents.db + the content-addressed tree into the target store.

    Backs up the live documents.db first (WAL-safe), installs the staged
    snapshot, then copies the staged content tree verbatim into the target's
    ``document-store`` root. Content is content-addressed (immutable, no
    embedded paths), so the copy is idempotent and needs no rewrite.
    """
    staged_db = staged_data / "documents.db"
    staged_tree = staging / "document-store"
    if not staged_db.exists() and not staged_tree.exists():
        return
    from kazma_core.documents.backup import UNSUPPORTED_SHAPE, installed_document_backends
    from kazma_core.documents.config import get_document_storage_root
    from kazma_core.memory.backup import _backup_one

    # The target's catalog decides what goes where (DOC-A7): on Postgres it
    # came with data/postgres.dump and only the content files are put back.
    metadata, jobs = installed_document_backends()
    if metadata == "postgres" and jobs != "postgres":
        raise RuntimeError(UNSUPPORTED_SHAPE)
    target_root = Path(get_document_storage_root())
    target_root.mkdir(parents=True, exist_ok=True)

    if staged_db.exists() and metadata == "postgres":
        _log("  documents.db not installed: the target's document catalog is on Postgres")
    elif staged_db.exists():
        # Back up any existing live documents.db before overwriting.
        live_db = target_root / "documents.db"
        if live_db.exists():
            backup_dir = Path(report.backup_path) if report.backup_path else target_root
            try:
                _backup_one(live_db, backup_dir / "documents.db")
            except Exception:  # noqa: BLE001
                pass
        for suffix in ("-wal", "-shm", "-journal"):
            stale = live_db.with_name(live_db.name + suffix)
            stale.unlink(missing_ok=True)
        _backup_one(staged_db, live_db)
        _log("  restored documents.db")

    if staged_tree.exists():
        count = 0
        for src in staged_tree.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(staged_tree)
            dst = target_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
                count += 1
        _log(f"  restored {count} document content file(s)")


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


def _stage_legacy_knowledge(staged_data: Path, report: ImportReport, log: Callable[[str], object]) -> bool:
    """Give a bundle in the older layout its ``knowledge.db``, in staging.

    A bundle written before 2026-10-14 carried the Knowledge Library as a
    KB-only ``data/settings.db`` (the tables, copied as they were). This
    builds ``data/knowledge.db`` from it through the store's OWN schema --
    :class:`~kazma_core.stores.knowledge.KnowledgeStore` creates the tables,
    rows are copied by explicit column names and the keyword index is
    rebuilt from the chunks (Knowledge audit KB-E01) -- so it is verified,
    backed up and swapped in with the other databases. No Knowledge DDL
    lives here. A copy that fails fails the import before anything live
    changed. True to go on.
    """
    legacy = staged_data / "settings.db"
    target = staged_data / "knowledge.db"
    if not legacy.exists() or target.exists():
        return True
    try:
        from kazma_core.stores.knowledge import KnowledgeStore, copy_legacy_knowledge

        store = KnowledgeStore(db_path=str(target))
        try:
            with store._lock:  # noqa: SLF001 -- the store's own connection, once
                moved = copy_legacy_knowledge(store._get_conn(), str(legacy))  # noqa: SLF001
            if not moved:
                raise RuntimeError("the bundle's Knowledge tables could not be read")
            store.rebuild_fts(reason="migration import")
        finally:
            store.close()
    except Exception as exc:
        target.unlink(missing_ok=True)
        report.error(f"the bundle's Knowledge Library could not be staged: {exc}. "
                     "Live files were not modified.")
        return False
    log("  staged the bundle's Knowledge Library (older layout) into knowledge.db")
    return True
