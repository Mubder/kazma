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
from typing import Any


def _private_zip(path: Path) -> bool:
    """Make *path* an empty file only this account can read (the archive
    holds the vault, and with --include-env its key). The restriction is
    kazma_core/security/owner_only.py, loaded by path: this script stays
    standard library only, like the guard. True when it holds."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return bool(_load("security", "owner_only.py").restrict(path))


def _load(*parts: str) -> Any:
    """A module of kazma_core that is standard library only, by its path."""
    import importlib.util

    source = Path(__file__).resolve().parents[1].joinpath("kazma-core", "kazma_core", *parts)
    name = f"kazma_backup_{source.stem}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise OSError(f"{source} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _own_files(folder: Path) -> tuple[list[Path], list[Path]]:
    """Every file of *folder*, and every link left out. A link directly in it
    is the install's own layout; below, none is followed: the one walk
    (kazma_core/workspace/real_paths.py, kazma_core/backup/data_files.py)."""
    real_paths = _load("workspace", "real_paths.py")
    data_files = _load("backup", "data_files.py")
    folders: list[Path] = []
    files = list(real_paths.own_files(folder, linked_at_top=True, folders_seen=folders))
    at_top = data_files.linked_files_at_top(folder, real_paths=real_paths)
    left = [link for link in at_top if not link.is_file()]
    left += data_files.links_below(folders, top=folder, real_paths=real_paths)
    return sorted([*files, *(link for link in at_top if link.is_file())]), left


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
    away: list[str] = []
    for db in sorted(data_dir.glob("*.db")):
        if db.is_symlink() and not db.exists():
            # Its disk is away. Opened, SQLite would make a new, empty
            # database where the link leads, and the archive would hold that.
            away.append(db.name)
            print(f"ERROR: {db.name} is a link that leads nowhere (is its disk away?): "
                  "this database is not in the archive", file=sys.stderr)
            continue
        try:
            import sqlite3

            # Never made: the database is there, or the open fails.
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=rw", uri=True)
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
        files, left_out = _own_files(data_dir)
        for path in files:
            arc = Path("kazma-data") / path.relative_to(data_dir)
            zf.write(path, arcname=str(arc).replace("\\", "/"))
            manifest["files"].append(str(arc).replace("\\", "/"))
        manifest["databases_missing"] = away
        manifest["links_left_out"] = [link.relative_to(data_dir).as_posix() for link in left_out]
        for link in manifest["links_left_out"]:
            print(f"WARN: a link is left out, and what it leads to is not in the archive: {link}", file=sys.stderr)
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
    if away:
        print(f"ERROR: the backup is incomplete: {', '.join(away)} could not be read", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
