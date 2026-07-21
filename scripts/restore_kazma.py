#!/usr/bin/env python3
"""Restore Kazma data from a backup zip (Phase 4.5).

Usage:
  # Stop Kazma first!
  python scripts/restore_kazma.py --archive backups/kazma-backup-....zip
  python scripts/restore_kazma.py --archive ... --data-dir ./kazma-data --force

Never restore while the server is writing SQLite WAL files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore Kazma data from backup zip")
    parser.add_argument("--archive", required=True, help="Path to kazma-backup-*.zip")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Target kazma-data directory (default: ./kazma-data)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing data-dir without prompt",
    )
    args = parser.parse_args()

    archive = Path(args.archive)
    if not archive.is_file():
        print(f"ERROR: archive not found: {archive}", file=sys.stderr)
        return 1

    root = Path.cwd()
    data_dir = Path(args.data_dir) if args.data_dir else root / "kazma-data"

    if data_dir.exists() and any(data_dir.iterdir()) and not args.force:
        print(
            f"ERROR: {data_dir} is not empty. Stop Kazma, then re-run with --force "
            "or move the old directory aside.",
            file=sys.stderr,
        )
        return 1

    staging = root / ".kazma-restore-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(staging)
        names = zf.namelist()

    manifest_path = staging / "MANIFEST.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            print(f"Manifest created_at={manifest.get('created_at')} files={len(manifest.get('files', []))}")
        except Exception as exc:
            print(f"WARN: could not parse MANIFEST.json: {exc}")

    src_data = staging / "kazma-data"
    if not src_data.is_dir():
        print("ERROR: archive missing kazma-data/", file=sys.stderr)
        return 1

    if data_dir.exists():
        backup_old = data_dir.with_name(data_dir.name + ".pre-restore")
        if backup_old.exists():
            shutil.rmtree(backup_old)
        data_dir.rename(backup_old)
        print(f"Moved existing data to {backup_old}")

    shutil.copytree(src_data, data_dir)
    print(f"OK restored data to {data_dir}")

    env_src = staging / ".env"
    if env_src.is_file():
        print("NOTE: archive includes .env — merge secrets manually into your live .env")

    shutil.rmtree(staging, ignore_errors=True)
    print("Next steps:")
    print("  1. Ensure KAZMA_SECRET and KAZMA_VAULT_KEY match the backup era")
    print("  2. Start Kazma and hit /health")
    print("  3. Verify chat history, settings, swarm tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
