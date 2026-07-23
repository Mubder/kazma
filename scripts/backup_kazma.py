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
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


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

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in data_dir.rglob("*"):
            if path.is_file():
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

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"OK backup written: {zip_path} ({size_mb:.2f} MiB, {len(manifest['files'])} files)")
    print("Remember: store KAZMA_SECRET + KAZMA_VAULT_KEY separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
