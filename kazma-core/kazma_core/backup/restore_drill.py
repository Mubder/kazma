"""Prove a backup can be restored, without restoring it over anything.

A backup nobody has ever restored is a hypothesis. The audit asked for a
rehearsed restore; building one surfaced that Kazma has no restore path for
universal backups at all -- settings, memory and Postgres each have one, but
nothing reassembles the 954 MB folder of SQLite DBs, assets, .env and work
artifacts. There is currently nothing to rehearse.

So this is the half that can be done safely, and it is worth more than it
sounds: verify that what was written back is *readable*. It never writes
into the live data directory and never touches a live database.

It checks the things that actually go wrong with backups:

* every SQLite file passes ``PRAGMA integrity_check`` -- copied to scratch
  first, so the backup itself is only ever read, and because a torn WAL
  copy passes a file-exists check while failing to open
* the Postgres dump parses as an archive, via ``pg_restore --list``, which
  reads the table of contents and writes to no database. A dump truncated
  by a full disk keeps a valid PGDMP header and a broken TOC, and only
  this catches it
* ``.env`` is present -- it holds KAZMA_SECRET, and without it the
  backed-up encrypted vault is unrecoverable. A backup that restores
  everything except the key to read it is not a backup
* the manifest is present, parses, and records no failed databases

Exit code is non-zero when the backup fails, so this is usable as a
scheduled check rather than something a human must remember to run.
"""

from __future__ import annotations

import json
import logging
import shutil
import os
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DrillResult", "verify_backup", "run_drill", "drill_scheduler",
           "DRILL_INTERVAL_HOURS", "run_deep_drill", "deep_drill_is_due",
           "DEEP_DRILL_INTERVAL_HOURS"]

# pg_restore --list on a multi-GB archive reads only the TOC, but a busy or
# containerised host can still be slow. Generous on purpose: a false failure
# here would teach an operator to ignore the drill.
_PG_LIST_TIMEOUT_S = 300


@dataclass
class DrillResult:
    """What the drill found. ``ok`` is the whole verdict."""

    backup_dir: str
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.ok = False

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c["ok"]]

    def summary(self) -> str:
        bad = self.failures
        head = "PASS" if self.ok else "FAIL"
        return (
            f"{head}: {len(self.checks) - len(bad)}/{len(self.checks)} checks "
            f"passed for {Path(self.backup_dir).name or '(none)'}"
        )


def _norm(rel: str) -> str:
    """One spelling for a relative path, so Windows and POSIX agree."""
    return str(rel).replace("\\", "/").strip("/").lower()


def _source_table_counts(backup: Path) -> dict[str, int | None]:
    """What each database held AT THE SOURCE, per the backup's own manifest.

    Read from the manifest rather than the live data directory on purpose:
    a restore drill may run against an offsite copy on a host that has no
    live install at all, and the live file has moved on regardless. The
    manifest is the only record of what was true when the copy was taken.

    Missing key -> not recorded (older backup). Distinct from 0.
    """
    out: dict[str, int | None] = {}
    try:
        m = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        for item in (m.get("databases") or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path:
                continue
            if "source_tables" in item:
                val = item.get("source_tables")
                out[_norm(path)] = val if isinstance(val, int) else None
    except Exception:  # noqa: BLE001
        logger.debug("[drill] could not read source table counts", exc_info=True)
    return out


def _check_sqlite(
    path: Path,
    scratch: Path,
    res: DrillResult,
    source_tables: int | None = None,
) -> None:
    """Copy to scratch and integrity-check. Never opens the backup in place.

    *source_tables* is what the manifest recorded for this database at the
    moment it was copied. Without it, "no tables" is ambiguous: a truncated
    backup of a real database and a faithful copy of an empty file are
    byte-identical, and `integrity_check` passes on both.
    """
    name = path.name
    target = scratch / name
    try:
        shutil.copy2(path, target)
    except Exception as exc:  # noqa: BLE001
        res.add(f"sqlite:{name}", False, f"could not copy out: {exc}")
        return
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            verdict = (row[0] if row else "") or ""
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        res.add(f"sqlite:{name}", False, f"will not open: {exc}")
        return
    if verdict.lower() != "ok":
        res.add(f"sqlite:{name}", False, f"integrity_check: {verdict[:120]}")
        return
    if tables == 0:
        # `integrity_check` passes on an empty file: structurally perfect and
        # completely worthless. A backup that saved nothing must not read as a
        # backup that saved everything.
        #
        # But an empty SOURCE is a different fact, and this check could not
        # tell them apart. On the operator's install two 0-byte orphans
        # (kazma.db, ops.db - created months ago, never written to) failed
        # the drill every single night under the headline "a backup cannot be
        # restored". Nothing was at risk; there was nothing in them. An alert
        # that cries wolf daily trains you to ignore the night it is right.
        if source_tables == 0:
            res.add(
                f"sqlite:{name}", True,
                "empty at source - copied faithfully, nothing to lose",
            )
            return
        if source_tables is None:
            res.add(
                f"sqlite:{name}", False,
                "contains no tables, and the manifest does not record what "
                "the source held (backup predates the check) - verify by hand",
            )
            return
        res.add(
            f"sqlite:{name}", False,
            f"THE SOURCE HAD {source_tables} TABLES; the backup has none",
        )
        return
    res.add(f"sqlite:{name}", True, f"{tables} tables")


def _check_vault_opens(backup: Path, res: DrillResult) -> None:
    """Prove the backup's own ``.env`` key decrypts the backup's own vault.

    The highest-consequence check here, and one of the cheapest. Every other
    check can pass while this fails, and the result is a backup whose every
    secret — provider keys, connector tokens, OAuth credentials — is
    permanently unreadable. `.env` being *present* proved nothing: a stale or
    rotated ``KAZMA_VAULT_KEY`` sits in a file of exactly the right size.

    Reads only, in a subprocess-free way, and never touches the live vault:
    the key is loaded from the backup's `.env` and applied to a COPY of the
    backup's vault.db.
    """
    env_file = backup / ".env"
    vault_db = backup / "dbs" / "vault.db"
    if not env_file.is_file():
        return  # already reported by env:present
    if not vault_db.is_file():
        # An install with the vault disabled has no vault.db, and calling that
        # a broken backup is a false alarm. Only a vault that EXISTS and will
        # not open is a finding.
        return

    key = ""
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("﻿")
            if line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ").strip() == "KAZMA_VAULT_KEY":
                key = value.strip().strip("'\"")
    except Exception as exc:  # noqa: BLE001
        res.add("vault:key", False, f"could not read .env: {exc}")
        return

    if not key:
        res.add(
            "vault:key", False,
            "no KAZMA_VAULT_KEY in the backup's .env -- every encrypted secret "
            "in this backup is unrecoverable",
        )
        return

    import os
    import tempfile

    scratch = Path(tempfile.mkdtemp(prefix="kazma-vault-drill-"))
    previous = os.environ.get("KAZMA_VAULT_KEY")
    try:
        copy = scratch / "vault.db"
        shutil.copy2(vault_db, copy)
        # Bring the WAL sidecars along. A SQLite file copied without its -wal
        # loses every write that has not been checkpointed — the copy opens
        # cleanly and reports "no such table", which would read here as a
        # broken vault. Backups written through the online backup API have no
        # sidecar; one produced any other way might.
        for suffix in ("-wal", "-shm"):
            side = vault_db.with_name(vault_db.name + suffix)
            if side.is_file():
                shutil.copy2(side, copy.with_name(copy.name + suffix))
        os.environ["KAZMA_VAULT_KEY"] = key
        from kazma_core.security.vault import SecretVault

        # How many secret ROWS exist, read straight from SQL. This is the
        # question `list_secrets()` cannot answer: with the WRONG key it comes
        # back empty, which is indistinguishable from a vault that holds
        # nothing — so a stale key read as "no secrets stored" and PASSED.
        # Caught by the test for exactly that case.
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT count(*) FROM secrets").fetchone()[0]
        finally:
            con.close()

        if rows == 0:
            # Genuinely empty: an install with no secrets yet. Calling that a
            # broken backup would be a false alarm.
            res.add("vault:decrypt", True, "vault opens; no secrets stored")
            return

        vault = SecretVault(db_path=str(copy))
        # Decrypt each sample AS ITS OWN TENANT. `retrieve(name)` with no
        # argument resolves the ambient ContextVar, and the drill runs
        # context-less — so every tenant-scoped row came back None and was
        # counted as "did not open". On this install 34 of 67 rows are scoped
        # to `default`, so a sample of five could easily report "the backup's
        # key opens NOTHING" about a backup whose key is perfectly fine.
        # A false alarm on recoverability is worse than no drill: it sends the
        # operator to rebuild a vault that was never broken.
        sample = [
            (str(s.get("name") or ""), s.get("tenant"))
            for s in vault.list_secrets()
            if s.get("name")
        ][:5]
        opened = sum(
            1
            for name, tenant in sample
            if vault.retrieve(name, None if tenant in (None, "global") else str(tenant))
            is not None
        )
        names = [n for n, _ in sample]
        res.add(
            "vault:decrypt", opened > 0,
            f"{opened}/{min(5, len(names))} sampled secrets decrypt "
            f"({rows} stored)" if opened
            else f"the backup's key opens NOTHING -- {rows} stored secret(s) "
                 "are unrecoverable from this backup",
        )
    except Exception as exc:  # noqa: BLE001
        res.add("vault:decrypt", False, f"vault will not open: {exc}")
    finally:
        if previous is None:
            os.environ.pop("KAZMA_VAULT_KEY", None)
        else:
            os.environ["KAZMA_VAULT_KEY"] = previous
        shutil.rmtree(scratch, ignore_errors=True)


def _check_completeness(backup: Path, res: DrillResult) -> None:
    """Is everything the backup CLAIMED to save actually on disk?

    Checked against the backup's own manifest rather than the live data
    directory. A first version compared the two and flagged 238 "missing"
    databases — the 212 per-repo `code-index` stores among them — because the
    drill can be pointed at any backup, including one from another install or
    another day, and "what is on this machine right now" is simply not the
    question a backup can be asked.

    What the manifest CAN be held to is its own word: every database it says
    it saved must be there, and readable. The complementary failure — a store
    that was never attempted — is recorded by the backup itself and is already
    read out of the manifest as `manifest:databases`.
    """
    manifest = backup / "manifest.json"
    if not manifest.is_file():
        return  # already reported by manifest:present
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
        items = ((m.get("databases") or {}).get("items")) or []
    except Exception as exc:  # noqa: BLE001
        res.add("complete:databases", False, f"manifest unreadable: {exc}")
        return
    if not items:
        return

    dbs_root = backup / "dbs"
    missing = [
        str(it.get("path") or "")
        for it in items
        if not it.get("error") and not (dbs_root / str(it.get("path") or "")).is_file()
    ]
    res.add(
        "complete:databases", not missing,
        f"all {len(items)} recorded databases are present" if not missing
        else f"{len(missing)} database(s) the manifest claims are NOT on disk: "
             + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""),
    )


def _run_pg_restore(
    prefix: list[str],
    dump: Path,
    args: list[str],
    **kw: Any,
) -> subprocess.CompletedProcess:
    """Run ``pg_restore`` over *dump*, by host path or by stdin as needed.

    ``resolve_pg_restore`` may hand back
    ``docker exec -i <container> pg_restore``. That binary runs INSIDE the
    container and cannot see a host path, so the archive has to arrive on
    stdin instead.

    Both drill tiers go through this function, because only one of them used
    to know. The TOC check learned it on its first live run and started
    piping; the deep data check was added a week later, kept
    ``[*prefix, "--file=-", str(dump)]``, and therefore never once verified a
    data section on a containerised Postgres. Nobody noticed because the deep
    tier fires every 168 hours: it failed on its FIRST scheduled run, seven
    days after it landed, with

        could not open input file "C:\\Users\\...\\pg_shared_*.dump"

    which reads like a missing or corrupt backup. The dump was 1.9 GB and
    perfectly healthy; only the drill was broken, which is the worst way for
    a backup check to fail — it cries wolf about the one thing you cannot
    afford to doubt.

    One function so the third caller cannot repeat it.
    """
    if prefix and "docker" in prefix[0].lower():
        with dump.open("rb") as fh:
            return subprocess.run([*prefix, *args], stdin=fh, **kw)
    return subprocess.run([*prefix, *args, str(dump)], **kw)


def _check_pg_dump(dump: Path, res: DrillResult) -> None:
    """Parse the archive TOC. Reads only; writes to no database."""
    try:
        with dump.open("rb") as fh:
            head = fh.read(5)
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:header", False, f"unreadable: {exc}")
        return
    if head[:5] != b"PGDMP":
        res.add("postgres:header", False, "missing PGDMP magic")
        return
    res.add("postgres:header", True, f"{dump.stat().st_size // (1024 * 1024)} MB")

    try:
        from kazma_core.migration.pg_bridge import resolve_pg_restore

        prefix = list(resolve_pg_restore())
    except Exception as exc:  # noqa: BLE001
        # Not a failure: the dump's header is still verified above, and a
        # host without client tools must not fail a drill for lacking them.
        res.add("postgres:toc", True, f"pg_restore unavailable, header only ({exc})")
        return

    # Host path vs stdin is decided in one place now -- see _run_pg_restore.
    try:
        proc = _run_pg_restore(
            prefix, dump, ["--list"], capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=_PG_LIST_TIMEOUT_S, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:toc", False, f"pg_restore --list would not run: {exc}")
        return
    if proc.returncode != 0:
        res.add("postgres:toc", False,
                f"archive will not parse: {(proc.stderr or '')[:160]}")
        return
    entries = [ln for ln in proc.stdout.splitlines()
               if ln.strip() and not ln.startswith(";")]
    if not entries:
        res.add("postgres:toc", False, "archive parses but contains nothing")
        return
    res.add("postgres:toc", True, f"{len(entries)} archive entries")


def verify_backup(
    backup_dir: str | Path,
    *,
    scratch_dir: str | Path | None = None,
    pg_dump: str | Path | None = None,
) -> DrillResult:
    """Verify one backup directory is readable. Never writes to live data."""
    d = Path(backup_dir)
    res = DrillResult(backup_dir=str(d))
    if not d.is_dir():
        res.add("backup:exists", False, "not a directory")
        return res
    res.add("backup:exists", True)

    manifest = d / "manifest.json"
    if not manifest.is_file():
        res.add("manifest:present", False, "manifest.json missing")
    else:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            res.add("manifest:present", True, f"version {m.get('version')}")
            failed = int((m.get("databases") or {}).get("failed") or 0)
            res.add(
                "manifest:databases", failed == 0,
                f"{failed} database(s) failed at backup time" if failed
                else "no failures recorded",
            )
        except Exception as exc:  # noqa: BLE001
            res.add("manifest:present", False, f"will not parse: {exc}")

    has_env = (d / ".env").is_file()
    res.add(
        "env:present", has_env,
        "" if has_env else "no .env -- the encrypted vault could not be decrypted",
    )

    dbs = sorted((d / "dbs").rglob("*.db")) if (d / "dbs").is_dir() else []
    if not dbs:
        res.add("sqlite:any", False, "no SQLite databases found in dbs/")

    owns_scratch = scratch_dir is None
    scratch = Path(scratch_dir or tempfile.mkdtemp(prefix="kazma-drill-"))
    try:
        scratch.mkdir(parents=True, exist_ok=True)
        _src_tables = _source_table_counts(d)
        for db in dbs:
            try:
                _key = _norm(str(db.relative_to(d / "dbs")))
            except ValueError:
                _key = _norm(db.name)
            _check_sqlite(db, scratch, res, _src_tables.get(_key))
        _check_vault_opens(d, res)
        _check_completeness(d, res)
    finally:
        if owns_scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    if pg_dump is not None:
        _check_pg_dump(Path(pg_dump), res)
    return res


def _check_pg_data_section(dump: Path, res: DrillResult) -> None:
    """Stream the WHOLE archive through pg_restore, not just its table of
    contents.

    ``pg_restore --list`` reads the TOC, which in a custom-format dump sits at
    the front — so a file truncated mid-data lists perfectly and passes. This
    restores to a plain-SQL stream and throws the output away, which forces
    every data block to be read and decompressed. Corruption or truncation
    anywhere in the 1.9 GB fails here.

    No database is touched and none is needed: the risk of a restore rehearsal
    without any of the risk of restoring.
    """
    try:
        from kazma_core.migration.pg_bridge import resolve_pg_restore

        prefix = list(resolve_pg_restore())
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:data", True, f"pg_restore unavailable ({exc}); skipped")
        return

    started = time.time()
    try:
        with open(os.devnull, "wb") as sink:
            # Was [*prefix, "--file=-", str(dump)] -- a host path handed to a
            # pg_restore running inside the container. See _run_pg_restore.
            proc = _run_pg_restore(
                prefix, dump, ["--file=-"],
                stdout=sink,
                stderr=subprocess.PIPE,
                timeout=_DEEP_PG_TIMEOUT_S,
            )
    except subprocess.TimeoutExpired:
        res.add(
            "postgres:data", False,
            f"pg_restore did not finish within {_DEEP_PG_TIMEOUT_S}s",
        )
        return
    except Exception as exc:  # noqa: BLE001
        res.add("postgres:data", False, f"could not run pg_restore: {exc}")
        return

    elapsed = time.time() - started
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        res.add(
            "postgres:data", False,
            f"the archive does not read back: {err[:200]}",
        )
        return
    mb = dump.stat().st_size // (1024 * 1024)
    res.add("postgres:data", True, f"{mb} MB streamed in {elapsed:.0f}s")


def _is_locked_error(text: str | None) -> bool:
    """True when restic refused because the repository is locked.

    Matched on restic's own wording: "unable to create lock in backend:
    repository is already locked by PID ... on <host>".
    """
    t = (text or "").lower()
    return "already locked" in t or "unable to create lock" in t


def _check_restic_data(res: DrillResult) -> None:
    """Re-read and re-hash a slice of the restic packs.

    ``restic check`` alone verifies structure — it cannot see bit rot in the
    stored data, which is the failure that makes a repository look perfect and
    restore garbage. A subset each week eventually covers the whole repo at a
    cost measured in minutes.
    """
    try:
        from kazma_core.backup import restic_repo
    except Exception as exc:  # noqa: BLE001
        res.add("restic:data", True, f"restic layer unavailable ({exc}); skipped")
        return

    if not restic_repo.restic_available():
        res.add("restic:data", True, "restic is not installed; skipped")
        return
    password, _ = restic_repo.ensure_password()
    if not password:
        res.add(
            "restic:data", False,
            "no repository passphrase -- the snapshots cannot be verified OR "
            "restored",
        )
        return

    for scope, repo in restic_repo.repo_paths().items():
        if not repo:
            continue
        out = restic_repo.check(
            repo, password, read_data_subset=_DEEP_RESTIC_SUBSET
        )

        if not out.ok and _is_locked_error(out.error):
            # Two very different things produce this one message.
            #
            # A DEAD owner's lock is the dangerous one: restic_repo.unlock_stale
            # documents that a killed restic leaves an EXCLUSIVE lock and every
            # later backup then fails, "while the schedule keeps reporting that
            # it ran". Clearing it here fixes the backups, not just the drill.
            # `unlock` without --remove-all only removes locks restic itself
            # judges stale, so this is safe against a live repository.
            restic_repo.unlock_stale(repo, password)
            out = restic_repo.check(
                repo, password, read_data_subset=_DEEP_RESTIC_SUBSET
            )

        if not out.ok and _is_locked_error(out.error):
            # Still locked after the dead ones were cleared, so a LIVE backup
            # holds it. A backup in progress is health. Reporting it as "a
            # backup cannot be restored" is a false alarm of exactly the kind
            # this drill exists to avoid being -- observed 2026-09-21, when a
            # manual deep run collided with the scheduled pg snapshot and the
            # repository was provably fine seconds later.
            #
            # The cost of this branch is that a collision means this pass did
            # not verify anything, so the detail says so rather than claiming
            # a re-read that never happened. Two collisions in a row on a
            # weekly cadence would hide a real problem for a fortnight; if
            # that ever shows up in the logs, retry rather than skip.
            res.add(
                f"restic:{scope}", True,
                "a backup holds the repository lock; packs NOT re-read this "
                "pass (not a failure -- a running backup is health)",
            )
            continue

        res.add(
            f"restic:{scope}", bool(out.ok),
            f"{_DEEP_RESTIC_SUBSET} of packs re-read" if out.ok
            else (out.error or "check failed")[:200],
        )


def _check_offsite_object(backup: Path, res: DrillResult) -> None:
    """Read the offsite copy back.

    ``offsite.ok: true`` in a manifest records that an upload returned 200. A
    remote that accepts writes and stores nothing returns 200 too, and the
    local disk dying is the one scenario the offsite copy exists for — so the
    claim was never worth more than the moment it was made.

    HEADs the object and compares its length against the bytes the upload
    reported sending. A provider without ``stat_file`` is reported as
    unverifiable rather than assumed good.
    """
    manifest = backup / "manifest.json"
    if not manifest.is_file():
        return
    try:
        off = (json.loads(manifest.read_text(encoding="utf-8")).get("offsite")) or {}
    except Exception:  # noqa: BLE001
        return
    if off.get("skipped"):
        return  # deliberately disabled -- not a failure
    if not off.get("ok"):
        res.add("offsite:object", False, f"upload failed: {str(off.get('error'))[:160]}")
        return

    remote_name = f"{backup.name}.zip"
    try:
        from kazma_core.backup.cloud_sync import get_sync_provider

        provider = get_sync_provider()
    except Exception as exc:  # noqa: BLE001
        res.add("offsite:object", True, f"provider unavailable ({exc}); skipped")
        return
    if provider is None:
        res.add("offsite:object", True, "no cloud provider configured; skipped")
        return
    if not hasattr(provider, "stat_file"):
        res.add(
            "offsite:object", True,
            f"{type(provider).__name__} cannot read an object back; unverified",
        )
        return

    import asyncio

    try:
        stat = asyncio.run(provider.stat_file(remote_name))
    except RuntimeError:
        # Already inside a loop (the scheduler runs this in a thread, so this
        # is the unusual path). Nothing to verify from here.
        res.add("offsite:object", True, "not verifiable from a running loop; skipped")
        return
    except Exception as exc:  # noqa: BLE001
        res.add("offsite:object", False, f"could not read it back: {exc}")
        return

    if not stat.get("ok"):
        res.add(
            "offsite:object", False,
            f"{remote_name} is not readable offsite: {stat.get('error')}",
        )
        return

    expected = int(off.get("size") or 0)
    got = int(stat.get("size") or 0)
    if expected and got != expected:
        res.add(
            "offsite:object", False,
            f"offsite copy is {got} bytes, the upload sent {expected}",
        )
        return
    res.add(
        "offsite:object", True,
        f"{got // 1024 // 1024} MB present offsite"
        + ("" if expected else " (size not recorded at upload time)"),
    )


#: Weekly. The cheap checks run daily; these read gigabytes.
DEEP_DRILL_INTERVAL_HOURS = 168.0
_DEEP_LAST_RUN_KEY = "backup.restore_drill.last_deep_run"
_DEEP_PG_TIMEOUT_S = 3600
#: Each pass verifies a slice; twenty weeks covers the repository.
_DEEP_RESTIC_SUBSET = "5%"


def deep_drill_is_due(now: float | None = None) -> bool:
    """True when no DEEP drill has completed within its interval."""
    stamp = time.time() if now is None else now
    try:
        from kazma_core.config_store import get_config_store

        last = float(get_config_store().get(_DEEP_LAST_RUN_KEY, 0) or 0)
    except Exception:  # noqa: BLE001
        last = 0.0
    if last <= 0:
        return True
    return (stamp - last) >= DEEP_DRILL_INTERVAL_HOURS * 3600


def _record_deep_run(when: float) -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_DEEP_LAST_RUN_KEY, float(when), category="backup")
    except Exception:  # noqa: BLE001
        logger.debug("[restore-drill] could not record deep run time", exc_info=True)


def run_deep_drill(pg_dump: str | Path | None = None) -> DrillResult:
    """The expensive half: prove the bytes read back, not just that they parse.

    Everything the daily drill does is necessary and none of it is sufficient.
    A dump whose TOC lists, a repository whose structure checks, and an S3
    object whose upload returned 200 can all be true of a backup that restores
    nothing. This reads the data.
    """
    res = DrillResult(backup_dir="(deep)")
    dump = Path(pg_dump) if pg_dump else _latest_pg_dump()
    if dump is None:
        try:
            from kazma_core.db.pg_backup import pg_backup_enabled

            if pg_backup_enabled():
                res.add(
                    "postgres:data", False,
                    "Postgres is the backend but no dump was found",
                )
        except Exception:  # noqa: BLE001
            logger.debug("[restore-drill] pg_backup_enabled check failed", exc_info=True)
    else:
        _check_pg_data_section(dump, res)
    _check_restic_data(res)

    try:
        from kazma_core.backup.universal import _universal_dir, latest_universal_backup

        latest = latest_universal_backup() or {}
        name = str(latest.get("dir") or latest.get("path") or "")
        if name:
            d = Path(name)
            if not d.is_dir():
                d = _universal_dir() / name
            if d.is_dir():
                _check_offsite_object(d, res)
    except Exception:  # noqa: BLE001
        logger.debug("[restore-drill] offsite check skipped", exc_info=True)
    return res


def _latest_pg_dump() -> Path | None:
    """The newest Postgres dump, or None if there genuinely is not one.

    This imported ``_dump_dir``, which does not exist in ``pg_backup`` -- the
    accessor is ``pg_backup_dir``. The bare ``except`` turned that
    AttributeError into None, ``run_drill`` passed ``pg_dump=None``, and
    ``_check_pg_dump`` was never called. So the drill verified 25 SQLite
    databases and silently skipped the 1.67 GB main database, reporting
    "29/29 checks passed" while never looking at it.

    The failure is worth naming: a broad except around an import turns a code
    defect into an absence, and an absence into a clean bill of health.
    """
    try:
        from kazma_core.db.pg_backup import pg_backup_dir

        dumps = sorted(
            (f for f in Path(pg_backup_dir()).glob("pg_shared_*.dump") if f.is_file()),
            key=lambda f: f.stat().st_mtime,
        )
        return dumps[-1] if dumps else None
    except Exception:  # noqa: BLE001
        logger.warning("[restore-drill] could not locate the Postgres dumps",
                       exc_info=True)
        return None


def run_drill(backup_dir: str | Path | None = None) -> DrillResult:
    """Verify the newest universal backup, or the one given."""
    if backup_dir is None:
        from kazma_core.backup.universal import latest_universal_backup

        latest = latest_universal_backup()
        if not latest:
            res = DrillResult(backup_dir="")
            res.add("backup:exists", False, "no universal backups found")
            return res
        backup_dir = latest.get("path") or latest.get("dir") or ""
        # list_universal_backups() reports "dir" as the directory NAME, not a
        # path, so this resolved to a bare timestamp and every drill failed at
        # its first check with "not a directory". Nothing caught it because
        # nothing ever ran the drill -- it was referenced only by the
        # resilience manifest, which documented it as a working mechanism.
        if backup_dir and not Path(backup_dir).is_dir():
            from kazma_core.backup.universal import _universal_dir

            resolved = _universal_dir() / str(backup_dir)
            if resolved.is_dir():
                backup_dir = resolved

    pg_dump = _latest_pg_dump()
    res = verify_backup(backup_dir, pg_dump=pg_dump)

    # A missing dump is only "nothing to check" on a SQLite install. Where
    # Postgres IS the backend, no dump means the main database is in no
    # backup at all -- which must fail loudly rather than pass by omission.
    if pg_dump is None:
        try:
            from kazma_core.db.pg_backup import pg_backup_enabled

            if pg_backup_enabled():
                res.add(
                    "postgres:dump", False,
                    "Postgres is the backend but no dump was found -- the main "
                    "database is not in this backup",
                )
        except Exception:  # noqa: BLE001
            logger.debug("[restore-drill] pg_backup_enabled check failed",
                         exc_info=True)
    return res


#: Daily. The weekly cadence was not the problem -- never running was. A
#: restore that has silently stopped being possible should be found in a day,
#: and these checks are cheap enough to afford it.
DRILL_INTERVAL_HOURS = 24.0

#: Where the last completed run is remembered, so the cadence survives a
#: restart.
_LAST_RUN_KEY = "backup.restore_drill.last_run"

#: A short settle after boot, then run if one is due. Not zero -- a drill
#: firing during startup competes with the work an operator is waiting on.
_DRILL_FIRST_DELAY_SECONDS = 300.0


def _last_drill_run() -> float:
    """Epoch seconds of the last completed drill, or 0.0."""
    try:
        from kazma_core.config_store import get_config_store

        return float(get_config_store().get(_LAST_RUN_KEY, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _record_drill_run(when: float) -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_LAST_RUN_KEY, float(when), category="backup")
    except Exception:  # noqa: BLE001
        logger.debug("[restore-drill] could not record run time", exc_info=True)


def drill_is_due(now: float | None = None) -> bool:
    """True when no drill has completed within the interval.

    The cadence is measured from the LAST RUN, not from process start. It used
    to sleep a full interval before its first run, so on a host restarting more
    often than the interval it never fired at all -- 34 scheduler starts and
    zero results across three days of live logs, while the module's own
    docstring warned that a mechanism nobody runs asserts a property nobody has
    measured.
    """
    stamp = time.time() if now is None else now
    last = _last_drill_run()
    if last <= 0:
        return True
    return (stamp - last) >= DRILL_INTERVAL_HOURS * 3600


async def drill_scheduler() -> None:
    """Run the drill daily, counting from the last run rather than from boot.

    This module could verify a backup from the day it was written. Nothing
    called it: its only non-test reference was the resilience manifest, which
    listed it as a mechanism that protects the system. So the manifest
    asserted a property that was never once measured -- the same shape as the
    hardcoded ``{"ok": True}`` Postgres entry, and as the offsite remote that
    reported healthy while refusing every write.

    Waits a few minutes after boot rather than a full interval: long enough
    that a drill does not compete with the work an operator is waiting on,
    short enough that a host restarting daily still gets one.
    """
    import asyncio

    first = True
    while True:
        try:
            if first:
                await asyncio.sleep(_DRILL_FIRST_DELAY_SECONDS)
                first = False
            else:
                await asyncio.sleep(DRILL_INTERVAL_HOURS * 3600)
            # The weekly deep tier first: if it is due, the daily checks it
            # supersedes run anyway below.
            if deep_drill_is_due():
                deep = await asyncio.to_thread(run_deep_drill)
                _record_deep_run(time.time())
                if deep.ok:
                    logger.info("[restore-drill] deep: %s", deep.summary())
                else:
                    logger.error("[restore-drill] deep: %s", deep.summary())
                    _alert_failure(deep)

            if not drill_is_due():
                continue
            res = await asyncio.to_thread(run_drill)
            _record_drill_run(time.time())
            if res.ok:
                # Logged on success on purpose: a mechanism that speaks only
                # when it breaks cannot be told from one that never runs.
                logger.info("[restore-drill] %s", res.summary())
            else:
                logger.error("[restore-drill] %s", res.summary())
                _alert_failure(res)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed drill must not
            # kill the cadence; the next one still fires.
            logger.warning("[restore-drill] scheduler iteration failed: %s", exc)
            await asyncio.sleep(300)


def _alert_failure(res: DrillResult) -> None:
    """Tell the operator the backup cannot be read back. Never raises."""
    try:
        from kazma_core.observability.ops_alerts import alert

        failed = ", ".join(
            f"{c['check']}" + (f" ({c['detail']})" if c["detail"] else "")
            for c in res.failures[:4]
        )
        alert(
            "backup.restore_drill_failed",
            "A backup cannot be restored -- the drill failed.",
            f"{res.summary()}. Failed: {failed}. The data is being written; "
            "what is in doubt is whether it can be read back.",
            severity="critical",
        )
    except Exception:  # noqa: BLE001
        logger.debug("[restore-drill] could not raise the alert", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Verify a Kazma backup is readable.")
    ap.add_argument("--backup", default=None,
                    help="backup directory (default: the newest one)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_drill(args.backup)
    for c in res.checks:
        mark = "ok  " if c["ok"] else "FAIL"
        detail = f" -- {c['detail']}" if c["detail"] else ""
        print(f"  [{mark}] {c['check']}{detail}")
    print(res.summary())
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
