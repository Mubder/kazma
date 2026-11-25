#!/usr/bin/env python3
"""Backup Kazma data directory for disaster recovery (Phase 4.5).

Usage:
  python scripts/backup_kazma.py
  python scripts/backup_kazma.py --dest D:\\backups\\kazma
  python scripts/backup_kazma.py --data-dir G:\\GitHubRepos\\kazma\\kazma-data

Creates a timestamped zip under dest (default: ./backups/) including:
  - kazma-data/ (SQLite DBs, vectors metadata, settings, vault)
  - .env.example note only (never auto-includes live .env secrets)

You must back up KAZMA_SECRET and KAZMA_VAULT_KEY out-of-band (password manager).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def _private_zip(path: Path) -> bool:
    """Make *path* an empty file only this account can read (the archive
    holds the vault, and with --include-env its key). The restriction is
    kazma_core/security/owner_only.py, loaded by path: this script stays
    standard library only, like the guard. True when it holds."""
    import importlib.util

    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    source = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core" / "security" / "owner_only.py"
    spec = importlib.util.spec_from_file_location("kazma_backup_owner_only", source)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    sys.modules["kazma_backup_owner_only"] = module  # registered before it runs
    spec.loader.exec_module(module)
    return bool(module.restrict(path))


def _own_files(folder: Path) -> list[Path]:
    """Every file of *folder*, no link and no junction followed below it: the one walk
    (kazma_core/workspace/real_paths.py, standard library only), loaded by
    path as above."""
    import importlib.util

    source = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core" / "workspace" / "real_paths.py"
    spec = importlib.util.spec_from_file_location("kazma_backup_real_paths", source)
    if spec is None or spec.loader is None:
        raise OSError(f"{source} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules["kazma_backup_real_paths"] = module
    spec.loader.exec_module(module)
    return list(module.own_files(folder, linked_at_top=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup Kazma data for DR")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to kazma-data (default: ./kazma-data)",
    )
    parser.add_argument(
        "--dest",
        default="backups",
        help="Directory to write backup zip (default: ./backups)",
    )
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Also copy .env into the archive (contains secrets — use carefully)",
    )
    args = parser.parse_args()

    root = Path.cwd()
    data_dir = Path(args.data_dir) if args.data_dir else root / "kazma-data"
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    zip_path = dest_dir / f"kazma-backup-{stamp}.zip"

    # Checkpoint WAL files so SQLite copies are consistent-ish
    for db in data_dir.glob("*.db"):
        try:
            import sqlite3

            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception as exc:
            print(f"WARN: checkpoint {db.name}: {exc}")

    manifest = {
        "created_at": stamp,
        "data_dir": str(data_dir.resolve()),
        "files": [],
        "notes": [
            "Restore with: python scripts/restore_kazma.py --archive <this.zip>",
            "Also restore KAZMA_SECRET and KAZMA_VAULT_KEY from your secret store.",
            "Stop Kazma before restore to avoid SQLite lock corruption.",
        ],
    }

    partial = zip_path.with_name(f".{zip_path.name}.partial")
    if not _private_zip(partial):
        partial.unlink(missing_ok=True)
        print(f"ERROR: {dest_dir} does not let this account make a file only it can read; "
              "the archive (it holds the vault) was not written", file=sys.stderr)
        return 1
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _own_files(data_dir):
            arc = Path("kazma-data") / path.relative_to(data_dir)
            zf.write(path, arcname=str(arc).replace("\\", "/"))
            manifest["files"].append(str(arc).replace("\\", "/"))
        if args.include_env:
            env = root / ".env"
            if env.is_file():
                zf.write(env, arcname=".env")
                manifest["files"].append(".env")
                manifest["notes"].append("Archive contains .env — treat as secret!")
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    os.replace(partial, zip_path)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"OK backup written: {zip_path} ({size_mb:.2f} MiB, {len(manifest['files'])} files)")
    print("Remember: store KAZMA_SECRET + KAZMA_VAULT_KEY separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
