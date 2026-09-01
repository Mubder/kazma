"""Universal backup — back up EVERYTHING in kazma-data.

The one-shot backup that "never leaves anything behind": every SQLite DB
(WAL-safe via the Online Backup API) and every asset/file/dir. Postgres
shared-state dumps are **not** inlined here — they run as the separate
``native_pg_backup`` handler (same 24h loop / Backup UI). The manifest
records PG as handled-by-native_pg_backup. Triggered by the 24h auto
loop (``native_backup``) and manually from the Backup UI / API.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["perform_universal_backup", "list_universal_backups", "latest_universal_backup"]

# Directories/patterns to EXCLUDE from the backup (never copy these).
_EXCLUDE_DIRS = frozenset({"backups", "__pycache__", ".git", "node_modules", ".tmp", "cache", "lo-profile"})
_EXCLUDE_SUFFIXES = (".pyc", "-wal", "-shm", "-journal", ".tmp")
_DEFAULT_RETENTION = 7

# Root files that must be in every backup regardless of how an operator
# has configured backups.extra_paths. Setting extra_paths REPLACES the
# default list, so anyone who set it to name their own folder silently
# dropped these -- a config change should not be able to quietly remove
# the file that makes a restore usable.
_ALWAYS_ROOT: tuple[str, ...] = ("kazma.yaml",)

# Live progress for the UI (phase + detail). Updated by perform_universal_backup,
# read by GET /api/backup/status.
_backup_progress: dict[str, Any] = {"phase": "idle", "detail": "", "error": ""}
# Serializes the concurrent-backup guard (check + phase flip must be atomic
# across the 24h loop thread and a manual UI trigger).
_backup_lock = threading.Lock()
# A run stuck in a mid phase longer than this is treated as crashed (process
# kill / hung thread) so the cadence and the manual button can never be bricked
# by a stale progress flag. Shared by the in-process lock and the API gate.
_STALE_AFTER_SECONDS = 1800


def backup_progress_is_stale(progress: dict[str, Any] | None = None) -> bool:
    """True when progress shows a mid phase older than _STALE_AFTER_SECONDS.

    Used by both ``perform_universal_backup``'s lock and the ``/api/backup/now``
    gate so they agree a hung run is treated as crashed instead of blocking
    every future backup forever (incident 2026-08-16: the manual button kept
    returning "A backup is already running" long after the run had died).
    """
    p = progress if progress is not None else _backup_progress
    phase = p.get("phase")
    started_ts = p.get("started_ts")
    return (
        phase not in ("idle", "done", "error")
        and isinstance(started_ts, (int, float))
        and time.time() - started_ts > _STALE_AFTER_SECONDS
    )


def get_backup_progress() -> dict[str, Any]:
    """Return the current backup progress state (for the UI status poll)."""
    return dict(_backup_progress)


def _set_progress(phase: str, **kwargs: Any) -> None:
    # Swap in a fresh dict (rebinding is atomic) — clear()+update() let a
    # concurrent GET /api/backup/status observe a half-cleared dict.
    global _backup_progress
    _backup_progress = {"phase": phase, **kwargs}


def _data_dir() -> Path:
    from kazma_core.paths import data_dir

    return Path(data_dir())


def _universal_dir() -> Path:
    return _data_dir() / "backups" / "universal"


# ── Backup-audit gaps (2026-08-15): .env, root work artifacts, offsite ──────
# The universal sweep only scanned kazma-data/, which left three things
# unprotected: the install-root .env (KAZMA_SECRET / vault key / DSN — with
# it lost, the backed-up encrypted vault is unrecoverable), agent-generated
# deliverables at the workspace root (research/ etc.), and any offsite copy
# (a single disk failure took data + all backup generations at once).


def _offsite_config() -> dict[str, Any]:
    """Live-read offsite sync config (mirrors pg_backup's reader; never raises).

    Keys: ``backups.offsite.provider`` (google_drive|onedrive|webdav|s3),
    ``backups.offsite.enabled``, and legacy ``backups.offsite.rclone_remote``
    (kept for backward compatibility — if only rclone_remote is set, rclone
    is used as before).
    """
    cfg: dict[str, Any] = {"enabled": False, "provider": "", "rclone_remote": ""}
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        cfg["provider"] = str(store.get("backups.offsite.provider") or "").strip()
        cfg["rclone_remote"] = str(store.get("backups.offsite.rclone_remote") or "").strip()
        enabled = store.get("backups.offsite.enabled")
        cfg["enabled"] = (
            bool(enabled)
            if enabled is not None
            else bool(cfg["provider"] or cfg["rclone_remote"])
        )
    except Exception:
        logger.debug("[universal-backup] offsite config read failed", exc_info=True)
    return cfg


def _pg_dump_stale_hours() -> float:
    """Stale threshold = backup cadence + slack (audit M-14).

    Derived live from ``worker_bootstrap._BACKUP_EXPORT_INTERVAL_HOURS`` so a
    6h loop cannot keep reporting PG "ok" for a day-old dump.
    """
    try:
        from kazma_core.memory.worker_bootstrap import _BACKUP_EXPORT_INTERVAL_HOURS

        return float(_BACKUP_EXPORT_INTERVAL_HOURS) + 2.0
    except Exception:
        return 8.0


#: Kept as a name the tests/docs already grep. Value is derived; do not
#: hard-code 26h again.
_PG_DUMP_STALE_HOURS = _pg_dump_stale_hours()


def _pg_dump_state() -> dict[str, Any]:
    """What the Postgres dump ACTUALLY looks like right now.

    This used to be the literal ``{"ok": True, "note": "handled by
    native_pg_backup task"}``. It was written when the redundant second
    ``pg_dump`` was removed from this function, and it is true only in the
    sense that some other task is responsible -- it says nothing about
    whether that task has ever run, or when.

    So every manifest reported the main database as healthy, including the
    ones written while the newest dump was a day old. A backup manifest that
    cannot report a missing Postgres dump is exactly the class of mechanism
    this file's own history is full of: it speaks only when it succeeds.
    """
    state: dict[str, Any] = {"ok": False, "note": "handled by native_pg_backup task"}
    try:
        from kazma_core.db.pg_backup import pg_backup_enabled

        if not pg_backup_enabled():
            # SQLite installs have no Postgres to dump; that is not a failure.
            return {"ok": True, "skipped": "postgres backend not in use"}
    except Exception:  # noqa: BLE001
        logger.debug("[universal-backup] pg_backup_enabled check failed", exc_info=True)

    try:
        pg_dir = _data_dir() / "backups" / "pg"
        dumps = sorted(
            (f for f in pg_dir.glob("pg_shared_*.dump") if f.is_file()),
            key=lambda f: f.stat().st_mtime,
        )
        if not dumps:
            state["error"] = "no Postgres dump exists yet"
            return state

        newest = dumps[-1]
        stat = newest.stat()
        age_h = max(0.0, (time.time() - stat.st_mtime) / 3600.0)
        stale_h = _pg_dump_stale_hours()
        state.update({
            "ok": age_h <= stale_h,
            "dump": newest.name,
            "size": stat.st_size,
            "age_hours": round(age_h, 2),
            "generations": len(dumps),
        })
        if not state["ok"]:
            state["error"] = (
                f"newest Postgres dump is {age_h:.1f}h old (stale past "
                f"{stale_h:.0f}h) -- the main database is not "
                "being dumped on schedule"
            )
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"could not inspect the Postgres dumps: {str(exc)[:200]}"
    return state


def _copy_root_artifacts(dest: Path) -> dict[str, Any]:
    """Copy the install-root .env + configured root work artifacts into *dest*.

    ``backups.extra_paths`` (ConfigStore, comma-separated, default
    ``research``) names workspace-root dirs/files to include — agent
    deliverables that live outside kazma-data/ and outside git. The .env is
    copied verbatim: it is the recovery key for the encrypted vault inside
    settings.db, so an encrypted copy keyed BY it would be circular. Protect
    the offsite copy instead (a cloud provider via cloud_sync).
    """
    result: dict[str, Any] = {"env": None, "artifacts": []}
    root = _data_dir().parent
    try:
        env_path = root / ".env"
        if env_path.is_file():
            target = dest / ".env"
            shutil.copy2(env_path, target)
            result["env"] = {"path": ".env", "size": target.stat().st_size}
        else:
            result["env"] = {"path": ".env", "missing": True}
    except Exception:
        logger.warning("[universal-backup] .env copy failed", exc_info=True)
        result["env"] = {"path": ".env", "error": True}

    # kazma.yaml is the MCP server registry, connector wiring and model
    # routing -- the difference between a restored install that runs and one
    # that boots with no tools and no connectors. It sits at the install root
    # rather than in kazma-data, so the sweep never saw it. Configured
    # extra_paths REPLACE this default, so _ALWAYS_ROOT below re-adds it.
    extra: list[str] = ["research", *_ALWAYS_ROOT]
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get("backups.extra_paths")
        if isinstance(raw, str) and raw.strip():
            extra = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, (list, tuple)) and raw:
            extra = [str(p).strip() for p in raw if str(p).strip()]
        for must in _ALWAYS_ROOT:
            if must not in extra:
                extra.append(must)
    except Exception:
        logger.debug("[universal-backup] extra_paths read failed", exc_info=True)

    for name in extra:
        try:
            src = root / name
            if not src.exists():
                continue
            target = dest / name
            if src.is_dir():
                count = _robust_copytree(src, target)
                result["artifacts"].append({"path": name, "files": count})
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                result["artifacts"].append({"path": name, "size": target.stat().st_size})
        except Exception:
            logger.warning("[universal-backup] root artifact %r failed", name, exc_info=True)
            result["artifacts"].append({"path": name, "error": True})
    return result


def _zip_backup_dir(dest: Path) -> Path:
    """Compress the finished backup directory into a single .zip archive.

    One file instead of a folder tree: the cloud copy becomes atomic (a
    partial upload can't masquerade as a complete backup), uploads are a
    handful of API calls instead of hundreds, and SQLite DBs compress 5-10x.
    """
    import zipfile

    files = [f for f in dest.rglob("*") if f.is_file()]
    if not files:
        raise RuntimeError("backup directory is empty — nothing to archive")
    zip_path = dest.parent / f"{dest.name}.zip"
    tmp_path = dest.parent / f".{dest.name}.zip.tmp"
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in files:
                zf.write(f, f.relative_to(dest).as_posix())
        tmp_path.replace(zip_path)
        return zip_path
    finally:
        # Never leave a half-written archive behind (e.g. crash mid-zip).
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _offsite_sync(dest: Path) -> dict[str, Any]:
    """Upload the finished backup to the configured cloud provider (fail-open).

    The ONLY protection against disk death. The backup directory is zipped
    into ONE archive (manifest included) and uploaded as a single file via the
    native cloud_sync providers (Google Drive / OneDrive / WebDAV / S3).
    Falls back to the legacy rclone folder copy if only ``rclone_remote`` is
    set. Never raises — any error logs and skips.
    """
    import asyncio

    cfg = _offsite_config()
    if not cfg["enabled"]:
        return {"skipped": "offsite sync disabled"}

    async def _rclone(reason: str = "") -> dict[str, Any]:
        """Copy the backup out with rclone. The independent second path.

        rclone carries its OWN OAuth credential, which is the entire point:
        the native google_drive provider borrows the Gmail refresh token, so
        one revoked grant takes out mail AND every offsite backup at once.
        A fallback that shares the failing credential is not a fallback.
        """
        if not cfg["rclone_remote"]:
            return {"skipped": "no offsite provider configured"} if not reason else {
                "ok": False, "error": reason,
                "note": "no rclone remote configured to fall back to",
            }
        if shutil.which("rclone") is None:
            return {"skipped": "rclone not found on PATH (configure a native provider instead)"} if not reason else {
                "ok": False, "error": reason, "note": "rclone not on PATH",
            }
        import subprocess

        remote = f"{cfg['rclone_remote'].rstrip('/')}/{dest.name}"
        proc = await asyncio.to_thread(
            subprocess.run,
            ["rclone", "copy", str(dest), remote, "--transfers", "4"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=1800,
        )
        if proc.returncode == 0:
            logger.info("[universal-backup] offsite sync complete via rclone: %s", remote)
            out: dict[str, Any] = {"ok": True, "remote": remote, "via": "rclone"}
            if reason:
                out["fallback_used"] = True
                out["primary_error"] = reason[:300]
            return out
        err = (proc.stderr or "")[:300]
        logger.warning("[universal-backup] rclone offsite sync failed: %s", err)
        return {"ok": False, "remote": remote, "via": "rclone",
                "error": f"{reason} | rclone: {err}" if reason else err}

    async def _run() -> dict[str, Any]:
        # Native providers take priority
        if cfg["provider"]:
            try:
                from kazma_core.backup.cloud_sync import get_sync_provider

                provider = get_sync_provider()
                if provider is None:
                    return {"skipped": f"unknown provider: {cfg['provider']}"}
                try:
                    zip_path = _zip_backup_dir(dest)
                except Exception as exc:
                    logger.warning("[universal-backup] zipping backup failed: %s", exc)
                    return {"ok": False, "error": f"zip failed: {exc}"}
                try:
                    result = await provider.upload_file(zip_path, zip_path.name)
                finally:
                    # The archive is a transient upload artifact — the local
                    # backup dir stays authoritative.
                    try:
                        zip_path.unlink()
                    except OSError:
                        pass
                if result.get("ok"):
                    logger.info(
                        "[universal-backup] offsite sync complete: %s",
                        result.get("remote"),
                    )
                    result.setdefault("via", cfg["provider"])
                    return result
                logger.warning(
                    "[universal-backup] offsite sync failed: %s — trying rclone",
                    result.get("error"),
                )
                return await _rclone(str(result.get("error") or "native provider failed"))
            except Exception as exc:
                logger.warning(
                    "[universal-backup] native offsite sync error: %s — trying rclone", exc
                )
                return await _rclone(str(exc))

        return await _rclone()

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=1900)
        return asyncio.run(_run())
    except Exception as exc:
        logger.warning("[universal-backup] offsite sync error: %s", exc)
        return {"ok": False, "error": str(exc)}


def _should_exclude(name: str, is_dir: bool = False) -> bool:
    if name in _EXCLUDE_DIRS:
        return True
    return any(name.endswith(suf) for suf in _EXCLUDE_SUFFIXES)


def _rmtree_force(path: Path) -> None:
    """Force-remove a directory tree on Windows (handles read-only + locked files).

    shutil.rmtree on Windows fails with WinError 145 ("directory is not empty")
    when files have read-only attributes or handles are briefly open. This
    clears the read-only bit on each file before removal.
    """
    import os
    import stat

    def _on_error(func: Any, fpath: str, exc_info: Any) -> None:
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            # Last resort: try once more after a tiny sleep (handle release).
            import time
            time.sleep(0.1)
            try:
                os.chmod(fpath, stat.S_IWRITE)
                func(fpath)
            except Exception:
                pass  # leave it; not worth crashing the delete

    shutil.rmtree(path, onerror=_on_error)


def _backup_one_db(src: Path, dest: Path) -> bool:
    """WAL-safe copy of a single SQLite database via the Online Backup API."""
    try:
        with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
            s.backup(d, pages=100, sleep=0.01)
        return True
    except Exception as exc:
        logger.warning("[universal-backup] DB copy failed %s: %s", src.name, exc)
        return False


def _robust_copytree(src: Path, dest: Path) -> int:
    """Copy a directory tree file-by-file, skipping files that vanish mid-copy.

    LibreOffice conversion runs leave ephemeral cache files (.bin under
    lo-profile/cache/) that can disappear between the directory walk and the
    copy. shutil.copytree aborts the ENTIRE directory on one missing file;
    this walker skips individual failures so the real data (content-addressed
    blobs) always lands. Returns the number of files successfully copied.
    """
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in sorted(src.iterdir()):
        if _should_exclude(item.name):
            continue
        target = dest / item.name
        try:
            if item.is_symlink():
                continue  # don't follow symlinks
            if item.is_dir():
                count += _robust_copytree(item, target)
            elif item.is_file():
                shutil.copy2(item, target)
                count += 1
        except (FileNotFoundError, OSError) as exc:
            logger.debug("[universal-backup] skipped ephemeral file %s: %s", item.name, exc)
        except Exception as exc:
            logger.debug("[universal-backup] copy error %s: %s", item.name, exc)
    return count


def _copy_assets(src: Path, dest: Path) -> list[dict[str, Any]]:
    """Copy all non-DB files and directories (recursive) excluding backups."""
    copied: list[dict[str, Any]] = []
    for item in sorted(src.iterdir()):
        if _should_exclude(item.name, is_dir=item.is_dir()):
            continue
        # Skip *.db files — they're handled by _backup_dbs.
        if item.is_file() and item.suffix == ".db":
            continue
        rel = item.name
        target = dest / rel
        try:
            if item.is_dir():
                count = _robust_copytree(item, target)
                copied.append({"path": rel, "type": "dir", "files": count})
            else:
                shutil.copy2(item, target)
                copied.append({"path": rel, "type": "file", "size": item.stat().st_size})
        except Exception as exc:
            logger.warning("[universal-backup] asset copy failed %s: %s", rel, exc)
            copied.append({"path": rel, "type": "error", "error": str(exc)[:200]})
    return copied


def _backup_postgres(dest_dir: Path) -> dict[str, Any] | None:
    """Delegate to pg_backup for a Postgres dump, then copy into the universal folder."""
    try:
        from kazma_core.db.pg_backup import perform_pg_backup, pg_backup_enabled

        if not pg_backup_enabled():
            return None
        dump_path = perform_pg_backup()
        if dump_path is None:
            return {"ok": False, "error": "pg_dump produced no file"}
        pg_dest = dest_dir / "postgres.dump"
        shutil.copy2(dump_path, pg_dest)
        return {"ok": True, "path": "postgres.dump", "size": pg_dest.stat().st_size}
    except Exception as exc:
        logger.warning("[universal-backup] Postgres dump failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


# A zip takes seconds to minutes. Anything still .tmp an hour later belongs
# to a process that is not coming back.
_TMP_ORPHAN_AGE_S = 3600


def _sweep_orphaned_tmp(root: Path) -> int:
    """Delete .zip.tmp archives left behind by a killed process.

    _zip_backup_dir cleans up in a finally, which covers an exception but
    NOT a kill -- and the Postgres path had the identical hole, where four
    interrupted dumps had quietly accumulated 3.83 GB because retention
    only ever matched finished files. Same shape, same fix, before this one
    grows too.
    """
    removed = 0
    cutoff = time.time() - _TMP_ORPHAN_AGE_S
    try:
        for tmp in root.glob(".*.zip.tmp"):
            try:
                if tmp.stat().st_mtime < cutoff:
                    size = tmp.stat().st_size
                    tmp.unlink()
                    removed += 1
                    logger.warning(
                        "[universal-backup] swept orphaned archive %s (%.0f MB)",
                        tmp.name, size / (1024 * 1024),
                    )
            except OSError:
                logger.debug("[universal-backup] could not sweep %s", tmp.name)
    except Exception:  # noqa: BLE001
        logger.debug("[universal-backup] temp sweep failed", exc_info=True)
    return removed


def _prune(retention: int) -> int:
    """Delete oldest universal backups beyond retention. Returns count deleted."""
    base = _universal_dir()
    backups = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    deleted = 0
    for stale in backups[retention:]:
        try:
            if stale.is_dir():
                # _rmtree_force handles Windows read-only/locked files; plain
                # shutil.rmtree fails with WinError 145 and silently skipped
                # every prune on Windows.
                _rmtree_force(stale)
            else:
                # .zip archives from archive_universal_backup count against
                # retention too (previously immortal — rmtree raised
                # NotADirectoryError on them and the UI delete refused files).
                stale.unlink(missing_ok=True)
            deleted += 1
        except Exception:
            logger.debug("[universal-backup] could not prune %s", stale.name, exc_info=True)
    deleted += _sweep_orphaned_tmp(_universal_dir())
    return deleted


def _read_retention() -> int:
    """Live-read the backup retention (env override → ``backups.retention``).

    Mirrors pg_backup's reader: ``KAZMA_BACKUP_RETENTION`` wins, then the
    ConfigStore key, then the default of 7. Clamped to >= 1, never raises.
    """
    try:
        env = (os.environ.get("KAZMA_BACKUP_RETENTION") or "").strip()
        if env:
            return max(1, int(env))
        from kazma_core.config_store import get_config_store

        val = get_config_store().get("backups.retention")
        if val is not None:
            return max(1, int(val))
    except Exception:
        logger.debug("[universal-backup] retention read failed", exc_info=True)
    return _DEFAULT_RETENTION


# Offsite failure is a slow-moving condition, not an incident: it stays
# broken until a human re-authorises. Six hours between reminders is often
# enough to be impossible to miss and rare enough not to be tuned out.
_OFFSITE_ALERT_COOLDOWN_S = 6 * 3600


def _snapshot_to_restic(dest: Path) -> dict[str, Any] | None:
    """Snapshot the finished backup into the restic repositories.

    Runs AFTER the manifest is written, so the snapshot contains a complete,
    self-describing backup rather than a directory mid-assembly.

    Additive on purpose. The existing local generations and offsite zip keep
    running untouched until a restore has been rehearsed twice -- a migration
    is precisely when you want the old copies, and cutting over before the
    new path has proven itself is how a backup rewrite loses data.

    Never raises: the backup has already succeeded by this point.
    """
    try:
        from kazma_core.backup.restic_repo import (
            backup as restic_backup,
        )
        from kazma_core.backup.restic_repo import (
            ensure_password,
            repo_paths,
            restic_available,
        )

        if not restic_available():
            return None
        password, _ = ensure_password()
        if not password:
            from kazma_core.backup.restic_repo import alert_missing_password

            alert_missing_password("universal backup")
            return {"skipped": "no restic passphrase"}

        out: dict[str, Any] = {}
        for name, repo in repo_paths().items():
            if not repo:
                continue
            res = restic_backup(repo, password, [str(dest)],
                                tags=["kazma", "universal"])
            out[name] = res.as_dict()
            if res.ok:
                # Success was silent, so the firing ledger read "restic
                # snapshot: never" while snapshots were being taken every
                # night. A mechanism that only speaks when it breaks cannot
                # be told apart from one that is not running at all.
                logger.info("[universal-backup] restic %s snapshot ok: %s",
                            name, res.detail.get("snapshot_id") or "(no id)")
            else:
                logger.warning("[universal-backup] restic %s failed: %s",
                               name, res.error[:200])
        return out or None
    except Exception:  # noqa: BLE001 -- must never fail a completed backup
        logger.warning("[universal-backup] restic snapshot failed", exc_info=True)
        return {"ok": False, "error": "restic snapshot raised"}


def _alert_on_backup_gaps(offsite: dict[str, Any], db_fail: int) -> None:
    """Tell the operator when a backup silently stopped protecting them.

    Live, 2026-08-28: 29 of 29 universal backups had
    ``offsite.ok == False`` -- "Token has been expired or revoked" -- going
    back over a day. Every backup was local-only, so one disk failure would
    have taken the data AND all 29 generations with it. _offsite_sync's own
    docstring calls itself "The ONLY protection against disk death".

    Nothing said so. The failure was recorded faithfully in a JSON file
    nobody reads, which is the exact shape of the silent failures the
    alerting layer was built for -- the backup path simply predated it.

    Fail-open, like everything else here: an alerting problem must never
    turn a completed backup into a failed one.
    """
    try:
        from kazma_core.observability.ops_alerts import alert

        if db_fail:
            alert(
                "backup.databases_failed",
                f"{db_fail} database(s) failed to back up.",
                "The local backup is incomplete. Check disk space and file locks.",
                severity="critical",
            )
        if offsite.get("skipped"):
            return  # deliberately disabled -- not a failure
        if offsite.get("ok") and offsite.get("fallback_used"):
            # Offsite protection is intact, so this is not critical -- but a
            # silently-degraded primary is how you end up with one path left
            # and no idea. Warn, and say which half is broken.
            alert(
                "backup.offsite_degraded",
                "Offsite backups are running on the FALLBACK path only.",
                f"The primary provider failed: "
                f"{str(offsite.get('primary_error') or '')[:200]} "
                f"rclone succeeded, so backups are still going offsite. "
                "Fix the primary before the fallback is the only thing left.",
                severity="warn",
                cooldown_s=_OFFSITE_ALERT_COOLDOWN_S,
            )
            return
        if not offsite.get("ok"):
            alert(
                "backup.offsite_failed",
                "Backups are LOCAL ONLY -- the offsite copy is failing.",
                f"{str(offsite.get('error') or 'unknown error')[:300]} "
                "A single disk failure would take the data and every backup "
                "generation with it. Re-authorise the cloud provider in "
                "Settings to restore offsite protection.",
                severity="critical",
                cooldown_s=_OFFSITE_ALERT_COOLDOWN_S,
            )
    except Exception:  # noqa: BLE001 -- alerting must never fail a backup
        logger.warning("[universal-backup] backup-gap alert failed", exc_info=True)


def perform_universal_backup(
    *, retention: int | None = None, trigger: str = "auto"
) -> dict[str, Any]:
    """Back up EVERYTHING: all kazma-data SQLite DBs + assets + Postgres dump.

    Creates ``kazma-data/backups/universal/<timestamp>/`` containing:
    - ``dbs/`` — all ``*.db`` files (WAL-safe via sqlite3.backup API)
    - ``assets/`` — all non-DB files/dirs (attachments, document-store,
      workspace, exports, etc.)
    - ``postgres.dump`` — Postgres shared-state tables (when configured)
    - ``manifest.json`` — itemised listing with sizes

    ``retention`` defaults to the live ``backups.retention`` config
    (env ``KAZMA_BACKUP_RETENTION``), falling back to 7. Returns a summary
    dict. Never raises — the 24h loop depends on this.
    """
    started = time.time()
    ts = int(time.time())
    data = _data_dir()
    dest = _universal_dir() / str(ts)

    # Guard against concurrent universal backups — the 24h auto loop and a
    # manual "Back Up Now" click can fire within seconds of each other.
    # Lock held across check + phase flip: the old check-then-act let both
    # threads observe "idle" and run two full concurrent copies.
    with _backup_lock:
        _phase = _backup_progress.get("phase")
        _started_ts = _backup_progress.get("started_ts")
        
        # Clean up incomplete backups from interrupted runs (server restart while
        # a backup was in progress). Any dir without a manifest.json is incomplete.
        # Must be INSIDE the lock to prevent race with concurrent backup runs.
        try:
            base = _universal_dir()
            if base.is_dir():
                for old in base.iterdir():
                    if old.is_dir() and old != dest and not (old / "manifest.json").is_file():
                        logger.info("[universal-backup] cleaning incomplete dir %s", old.name)
                        _rmtree_force(old)
        except Exception:
            logger.debug("[universal-backup] incomplete cleanup failed", exc_info=True)
        # A run that died mid-backup (process kill) leaves a mid phase
        # forever; anything older than 30 min is treated as crashed so the
        # cadence can never brick.
        _crashed = backup_progress_is_stale(_backup_progress)
        if _phase not in ("idle", "done", "error") and not _crashed:
            logger.info(
                "[universal-backup] already running (phase=%s) — skipping",
                _phase,
            )
            return {
                "ok": False,
                "error": "A universal backup is already running",
                "phase": _phase,
            }
        if _crashed:
            logger.warning(
                "[universal-backup] previous run (phase=%s, started %.0fs ago) never "
                "completed — treating as crashed and starting a new backup",
                _phase,
                time.time() - _started_ts,
            )
        _set_progress("databases", detail="Preparing backup…", started_ts=time.time())

    (dest / "dbs").mkdir(parents=True, exist_ok=True)
    (dest / "assets").mkdir(parents=True, exist_ok=True)

    # 1. All SQLite databases (WAL-safe).
    _set_progress("databases", detail="Copying databases…", total=0, done=0)
    db_results: list[dict[str, Any]] = []
    db_files = sorted(data.rglob("*.db"))
    # Exclude any .db inside backups/ or excluded dirs.
    db_files = [f for f in db_files if not any(p in _EXCLUDE_DIRS for p in f.parts)]
    db_ok = db_fail = 0
    for i, db in enumerate(db_files):
        rel = db.relative_to(data)
        _set_progress("databases", detail=f"DB {i+1}/{len(db_files)}: {rel.name}",
                       total=len(db_files), done=i)
        db_dest = dest / "dbs" / rel
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        if _backup_one_db(db, db_dest):
            db_ok += 1
            db_results.append({"path": str(rel), "size": db_dest.stat().st_size})
        else:
            db_fail += 1
            db_results.append({"path": str(rel), "error": "backup failed"})
    _set_progress("databases", detail=f"Databases done ({db_ok} ok, {db_fail} failed)",
                   total=len(db_files), done=len(db_files))

    # 2. All non-DB assets (attachments, document-store, workspace, etc.).
    _set_progress("assets", detail="Copying assets (attachments, document-store, workspace)…")
    asset_results = _copy_assets(data, dest / "assets")
    _set_progress("assets", detail=f"Assets done ({len(asset_results)} groups)")

    # 2.5 Install-root .env + root work artifacts (backup-audit gap #1/#3):
    # .env holds the vault key — without it the backed-up encrypted vault is
    # unrecoverable; research/ & co. are deliverables no other sweep covers.
    _set_progress("assets", detail="Copying .env + root artifacts…")
    root_artifacts = _copy_root_artifacts(dest)

    # 3. Postgres dump — SKIP (the native_pg_backup task already dumps PG
    # separately into backups/pg/. Running it here was redundant (two pg_dump
    # calls) and added 3-5s of I/O. The PG dump is listed in the manifest as
    # "handled by native_pg_backup" for documentation.
    # 3b. Graph memory. Neo4j lives in a Docker volume, and nothing here
    # walks Docker volumes -- so 323 nodes of graph memory sat in no backup
    # at all until 2026-08-29. Self-disabling when Neo4j is not the
    # configured graph backend.
    _set_progress("graph", detail="Exporting graph memory…")
    try:
        from kazma_core.backup.neo4j_backup import export_graph

        graph_result = export_graph(dest).as_dict()
    except Exception as exc:  # noqa: BLE001 -- a graph failure must not lose the rest
        logger.warning("[universal-backup] graph export failed", exc_info=True)
        graph_result = {"ok": False, "error": str(exc)[:300]}

    _set_progress("manifest", detail="Writing manifest…")
    pg_result = _pg_dump_state()

    # 4. Offsite sync (backup-audit gap #2): the finished backup is zipped
    # into ONE archive and uploaded as a single file. Fail-open — the local
    # backup stays authoritative. The manifest is written first (with a
    # placeholder offsite entry) so the archive contains it; the local
    # manifest is then rewritten with the real cloud result, which the UI
    # reads to badge backups as ☁ Cloud.
    _set_progress("offsite", detail="Zipping + uploading to cloud…" if _offsite_config()["enabled"] else "Skipping offsite…")
    elapsed = round(time.time() - started, 1)
    manifest: dict[str, Any] = {
        "timestamp": ts,
        "version": 1,
        "trigger": trigger,
        "elapsed_seconds": elapsed,
        "databases": {"ok": db_ok, "failed": db_fail, "items": db_results},
        "assets": asset_results,
        "root_artifacts": root_artifacts,
        "postgres": pg_result,
        "graph": graph_result,
        "offsite": {"status": "pending", "note": "cloud upload in flight at archive time"},
    }
    manifest_path = dest / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    offsite = _offsite_sync(dest)
    # Final elapsed includes the cloud upload; the zip's embedded manifest
    # keeps the local-build elapsed it was written with.
    elapsed = round(time.time() - started, 1)
    manifest["elapsed_seconds"] = elapsed
    manifest["offsite"] = offsite
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    restic_result = _snapshot_to_restic(dest)
    if restic_result:
        manifest["restic"] = restic_result
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _alert_on_backup_gaps(offsite, db_fail)

    # 6. Prune old backups (live-configured retention, env override, >= 1).
    keep = max(1, retention if retention is not None else _read_retention())
    pruned = _prune(keep)

    total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    size_mb = round(total_size / (1024 * 1024), 1)
    summary = {
        "ok": True,
        "backup_dir": str(dest),
        "timestamp": ts,
        "trigger": trigger,
        "databases_ok": db_ok,
        "databases_failed": db_fail,
        "assets_copied": len(asset_results),
        "postgres": pg_result.get("ok") if pg_result else None,
        "total_size_mb": size_mb,
        "elapsed_seconds": elapsed,
        "pruned": pruned,
        "env_backed_up": bool(root_artifacts.get("env") and not root_artifacts["env"].get("error")),
        "root_artifacts": [a.get("path") for a in root_artifacts.get("artifacts", [])],
        "offsite": offsite,
    }
    logger.info(
        "[universal-backup] complete: %d DBs, %d asset groups, %.1f MB, %.1fs, pruned %d",
        db_ok, len(asset_results), size_mb, elapsed, pruned,
    )
    _set_progress("done", detail=f"Complete: {db_ok} DBs, {size_mb} MB, {elapsed}s",
                   result=summary)
    return summary


def list_universal_backups() -> list[dict[str, Any]]:
    """List all universal backups (newest first) for the UI."""
    base = _universal_dir()
    if not base.is_dir():
        return []
    backups: list[dict[str, Any]] = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        # Always set defaults so the UI never shows "undefined".
        entry: dict[str, Any] = {
            "dir": d.name,
            "timestamp": int(d.name) if d.name.isdigit() else 0,
            "size_mb": round(
                sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024), 1
            ),
            "databases_ok": 0,
            "databases_failed": 0,
            "assets": 0,
            "postgres": False,
            "elapsed": 0,
            "incomplete": not manifest_path.is_file(),
            "trigger": "auto",
            "archived": (d.parent / f"{d.name}.zip").is_file(),
        }
        if manifest_path.is_file():
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry["databases_ok"] = m.get("databases", {}).get("ok", 0)
                entry["databases_failed"] = m.get("databases", {}).get("failed", 0)
                entry["assets"] = len(m.get("assets", []))
                entry["postgres"] = (m.get("postgres") or {}).get("ok", False)
                entry["elapsed"] = m.get("elapsed_seconds", 0)
                entry["trigger"] = m.get("trigger", "auto")
                # Cloud sync status from the manifest (written after the sync).
                offsite = m.get("offsite") or {}
                entry["cloud_synced"] = bool(offsite.get("ok"))
                entry["cloud_remote"] = offsite.get("remote", "")
                if not entry["cloud_synced"] and offsite.get("skipped"):
                    entry["cloud_skipped_reason"] = str(offsite.get("skipped", ""))[:80]
            except Exception:
                pass
        backups.append(entry)
    return backups


def latest_universal_backup() -> dict[str, Any] | None:
    """Return the newest universal backup entry, or None."""
    items = list_universal_backups()
    return items[0] if items else None


def delete_universal_backup(dir_name: str) -> dict[str, Any]:
    """Delete a universal backup by its directory name (timestamp)."""
    import re
    # Only allow deleting inside backups/universal/ — reject path traversal.
    if not re.match(r"^\d+$", dir_name):
        return {"ok": False, "error": "Invalid backup name (must be a timestamp)"}
    target = _universal_dir() / dir_name
    try:
        if target.is_dir():
            _rmtree_force(target)
        elif target.is_file():
            # .zip archive from archive_universal_backup — deletable too
            # (previously immortal: the is_dir() check refused them).
            target.unlink(missing_ok=True)
        else:
            return {"ok": False, "error": f"Backup '{dir_name}' not found"}
        logger.info("[universal-backup] deleted %s", dir_name)
        return {"ok": True, "deleted": dir_name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def archive_universal_backup(dir_name: str) -> dict[str, Any]:
    """Archive a universal backup into a single .zip for download/transfer."""
    import re
    import zipfile
    if not re.match(r"^\d+$", dir_name):
        return {"ok": False, "error": "Invalid backup name (must be a timestamp)"}
    src = _universal_dir() / dir_name
    if not src.is_dir():
        return {"ok": False, "error": f"Backup '{dir_name}' not found"}
    zip_path = _universal_dir() / f"{dir_name}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in src.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(src.parent))
        size_mb = round(zip_path.stat().st_size / (1024 * 1024), 1)
        logger.info("[universal-backup] archived %s → %s (%.1f MB)", dir_name, zip_path.name, size_mb)
        return {"ok": True, "archive": str(zip_path), "size_mb": size_mb}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
