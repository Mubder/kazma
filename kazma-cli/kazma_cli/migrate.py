"""``kazma migrate`` CLI — export / import / verify Kazma installation bundles.

Wraps the :mod:`kazma_core.migration` engine. The engine lives in kazma-core
so it's reusable from REST/UI later; this module is the user-facing CLI.

Usage::

    kazma migrate export [--out PATH] [--no-assets]
    kazma migrate verify BUNDLE [--no-hash]
    kazma migrate import BUNDLE [--workspace PATH] [--reset-vault-key] [--dry-run]

A bundle is a portable ``.zip`` capturing a whole Kazma install (config,
secrets, memory, chat history, snapshots, scheduled jobs, binary assets).
It enforces three invariants that a naive copy-paste breaks — see the
``kazma migrate help`` output and AGENTS.md §18:

  A. ``vault.db`` and ``KAZMA_VAULT_KEY`` travel as an atomic pair.
  B. Embedded absolute paths are rewritten to the target root across OSes.
  C. Import is atomic — staging → backup → swap; failure leaves live data intact.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["run", "print_help"]


def run(args: list[str]) -> None:
    """Dispatch ``kazma migrate <subcommand> [args...]``."""
    if not args or args[0] in ("--help", "-h", "help"):
        print_help()
        return

    sub = args[0]
    rest = args[1:]

    if sub == "export":
        _cmd_export(rest)
    elif sub == "verify":
        _cmd_verify(rest)
    elif sub == "import":
        _cmd_import(rest)
    else:
        print(f"Unknown migrate subcommand: {sub}")
        print_help()
        sys.exit(1)


# ── export ────────────────────────────────────────────────────────────────


def _cmd_export(args: list[str]) -> None:
    """``kazma migrate export [--out PATH] [--no-assets]``."""
    out_path = _flag_value(args, "--out", default=None)
    include_assets = "--no-assets" not in args

    if out_path is None:
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = f"kazma-bundle-{ts}.zip"

    print(f"\n  Exporting Kazma installation → {out_path}\n")

    def progress(msg: str) -> None:
        print(f"    {msg}")

    try:
        from kazma_core.migration import export_bundle

        written = export_bundle(out_path, include_assets=include_assets, progress=progress)
    except Exception as exc:
        print(f"\n  [ERROR] export failed: {exc}\n")
        logger.exception("[migrate:export]")
        sys.exit(1)

    size_mb = written.stat().st_size / (1024 * 1024)
    print(f"\n  ✅ Bundle written: {written} ({size_mb:.1f} MB)")
    print(f"     Move this file to the target machine, then run:")
    print(f"       kazma migrate verify {written.name}")
    print(f"       kazma migrate import {written.name} --workspace <target-kazma-path>\n")


# ── verify ────────────────────────────────────────────────────────────────


def _cmd_verify(args: list[str]) -> None:
    """``kazma migrate verify BUNDLE [--no-hash]``."""
    bundle_path = _positional(args, index=0)
    if not bundle_path:
        print("\n  [ERROR] usage: kazma migrate verify BUNDLE [--no-hash]\n")
        sys.exit(1)
    check_hashes = "--no-hash" not in args

    from kazma_core.migration import KazmaBundle

    try:
        bundle = KazmaBundle(bundle_path)
    except FileNotFoundError:
        print(f"\n  [ERROR] bundle not found: {bundle_path}\n")
        sys.exit(1)
    except ValueError as exc:
        print(f"\n  [ERROR] {exc}\n")
        sys.exit(1)

    print(f"\n  Verifying {Path(bundle_path).name}…\n")
    report = bundle.verify(check_hashes=check_hashes)

    m = bundle.manifest
    print(f"    bundle version : {m.bundle_version}")
    print(f"    source OS      : {m.source_os}")
    print(f"    source host    : {m.source_hostname}")
    print(f"    source backend : {m.source_backend}")
    print(f"    created        : {m.created_at}")
    print(f"    vault key      : {m.vault_key_fingerprint or '(none)'}")
    print(f"    source root    : {m.source_workspace_root or '(none)'}")
    print(f"    files          : {report.file_count}  ({report.total_bytes // 1024} KB)")

    if report.table_counts:
        print(f"\n    table row counts:")
        for db_name in sorted(report.table_counts):
            counts = report.table_counts[db_name]
            if isinstance(counts, dict):
                summary = ", ".join(f"{t}={n}" for t, n in list(counts.items())[:4])
                if len(counts) > 4:
                    summary += f", … ({len(counts)} tables)"
                print(f"      {db_name}: {summary}")

    if report.warnings:
        print("\n    ⚠ warnings:")
        for w in report.warnings:
            print(f"      - {w}")

    if report.errors:
        print("\n  ❌ INVALID bundle:")
        for e in report.errors:
            print(f"      - {e}")
        print()
        sys.exit(1)

    print("\n  ✅ Bundle is valid.\n")


# ── import ────────────────────────────────────────────────────────────────


def _cmd_import(args: list[str]) -> None:
    """``kazma migrate import BUNDLE [--workspace PATH] [--reset-vault-key] [--dry-run]``."""
    bundle_path = _positional(args, index=0)
    if not bundle_path:
        print("\n  [ERROR] usage: kazma migrate import BUNDLE [--workspace PATH] [--reset-vault-key] [--dry-run]\n")
        sys.exit(1)

    workspace = _flag_value(args, "--workspace", default=None)
    reset_vault_key = "--reset-vault-key" in args
    dry_run = "--dry-run" in args

    if not workspace and not dry_run:
        workspace = str(Path.cwd())
        print(f"\n  (no --workspace given; using current directory: {workspace})")

    print(f"\n  Importing {Path(bundle_path).name}…")
    if dry_run:
        print("  [DRY RUN — no changes will be made]")
    print()

    def progress(msg: str) -> None:
        print(f"    {msg}")

    from kazma_core.migration import import_bundle

    try:
        report = import_bundle(
            bundle_path,
            target_workspace_root=workspace,
            reset_vault_key=reset_vault_key,
            dry_run=dry_run,
            progress=progress,
        )
    except Exception as exc:
        print(f"\n  [ERROR] import failed: {exc}\n")
        logger.exception("[migrate:import]")
        sys.exit(1)

    print()
    if report.vault_status:
        icon = {"match": "✅", "empty": "🔑", "mismatch": "⚠️", "no_vault": "∅"}.get(report.vault_status, "?")
        print(f"  {icon} vault: {report.vault_status} — {report.vault_message}")

    if report.rows_rewritten:
        print(f"\n  paths rewritten:")
        for db, n in report.rows_rewritten.items():
            print(f"    {db}: {n} row(s)")

    if report.files_restored:
        print(f"\n  files restored:")
        for f in report.files_restored:
            print(f"    {f}")

    if report.warnings:
        print(f"\n  ⚠ warnings:")
        for w in report.warnings:
            print(f"    - {w}")

    if report.errors:
        print(f"\n  ❌ import aborted:")
        for e in report.errors:
            print(f"    - {e}")
        if report.staging_path:
            print(f"\n  staging dir preserved for inspection: {report.staging_path}")
        print()
        sys.exit(1)

    if dry_run:
        print("\n  ✅ Dry run complete — re-run without --dry-run to apply.\n")
    else:
        print("\n  ✅ Import complete.")
        if report.backup_path:
            print(f"     pre-import backup: {report.backup_path}")
            print(f"     (to roll back: copy the .db files from there back over the live ones)")
        print()


# ── arg helpers ───────────────────────────────────────────────────────────


def _flag_value(args: list[str], flag: str, *, default: str | None) -> str | None:
    """Extract the value of ``--flag value`` from args, or default."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def _positional(args: list[str], *, index: int) -> str | None:
    """Return the Nth positional arg (skipping flags + their values)."""
    flags_taking_value = {"--out", "--workspace"}
    pos = 0
    i = 0
    while i < len(args):
        a = args[i]
        if a in flags_taking_value:
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if pos == index:
            return a
        pos += 1
        i += 1
    return None


def print_help() -> None:
    print(
        """
  kazma migrate — cross-machine installation migration

  Move a full Kazma install (config, secrets, memory, chat history,
  snapshots, scheduled jobs, assets) from one machine to another without
  the silent breakage of a naive copy-paste (undecryptable vault, dead
  /home/... paths, missing data).

  Three invariants enforced (see AGENTS.md §18):
    A. vault.db + KAZMA_VAULT_KEY travel as an atomic pair
    B. embedded absolute paths are rewritten to the target root
    C. import is atomic — staging → backup → swap; failure leaves data intact

  Commands:
    export [--out PATH] [--no-assets]
        Create a portable .zip bundle of the current installation.

    verify BUNDLE [--no-hash]
        Check bundle integrity (structure, hashes, manifest) without importing.

    import BUNDLE [--workspace PATH] [--reset-vault-key] [--dry-run]
        Restore a bundle into THIS installation. Use --dry-run first to
        preview the path-translation plan and vault-key check.

  Scope: bundles carry BOTH the SQLite files (vault/memory/snapshots —
  always SQLite even under a Postgres backend) AND, when the source is
  Postgres, a pg_dump of the shared-state tables (settings/chat/checkpoints).
  Import restores whichever the target backend expects: a Postgres target
  pg_restores the dump; a SQLite target uses the SQLite files. A Postgres
  bundle into a SQLite target aborts with a clear error (rather than
  silently losing the Postgres data). pg_dump/pg_restore are found on PATH,
  then via `docker exec ${KAZMA_DB_CONTAINER:-kazma-db}`.
"""
    )
