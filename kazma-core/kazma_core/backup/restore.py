"""Rebuild a Kazma install from a backup.

The half that never existed. Verification and ``restic restore`` could get
your bytes back; nothing turned them into a working install, so recovery
meant sequencing five steps from memory at the worst possible moment.

Two things here are not obvious and are the reason this is a module rather
than a paragraph in a runbook.

**"Latest" is a lie.** ``restic restore latest`` picks the newest snapshot
by the time the SNAPSHOT was taken, which is not the newest DATA. On this
install, 22 legacy generations were bulk-ingested hours after the recent
ones, so they carry the most recent snapshot timestamps and the oldest
content. Restoring ``latest`` yields a generation predating kazma.yaml and
the graph export -- and it looks like a clean success. Selection here is by
GENERATION, parsed out of the backed-up path, never by snapshot time.

**A restore is run when something has already gone wrong**, often against
the wrong target by accident. Every destructive step is opt-in: files go to
a fresh directory, and loading Postgres or Neo4j over live data requires
saying so explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "RestorePoint",
    "list_restore_points",
    "select_point",
    "restore_files",
    "RestoreResult",
]

# backups/universal/<10-digit epoch>
_GEN_RE = re.compile(r"universal[\\/](\d{10})")
# backups/pg/pg_shared_<epoch>.dump
_PG_RE = re.compile(r"pg_shared_(\d{10})\.dump")


@dataclass
class RestorePoint:
    """One recoverable moment: a universal generation and its nearest dump."""

    generation: int
    snapshot_id: str
    taken_at: str
    repo: str
    pg_snapshot_id: str = ""
    pg_generation: int = 0

    @property
    def label(self) -> str:
        import datetime

        when = datetime.datetime.fromtimestamp(self.generation)
        return f"{self.generation} ({when:%Y-%m-%d %H:%M})"


@dataclass
class RestoreResult:
    ok: bool = False
    target: str = ""
    generation: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"step": name, "ok": bool(ok), "detail": detail})
        if not ok:
            self.ok = False

    def summary(self) -> str:
        bad = [s for s in self.steps if not s["ok"]]
        head = "RESTORED" if self.ok else "FAILED"
        return (f"{head}: {len(self.steps) - len(bad)}/{len(self.steps)} steps, "
                f"generation {self.generation} -> {self.target}")


class _SnapshotListError(RuntimeError):
    """restic could not list the repository's snapshots.

    Distinct from an empty list (audit 2026-10-26 AUD-044): a wrong
    passphrase, a missing repository or a restic that failed used to read as
    "no restore points", at the one moment someone needs them.
    """


def _snapshots(repo: str, password: str) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = repo
    env["RESTIC_PASSWORD"] = password
    try:
        # --retry-lock: a prune or check holding the repository exclusively
        # is waited for, never read as "no snapshots" (restic_repo._run).
        proc = subprocess.run(
            ["restic", "--retry-lock", "10m", "snapshots", "--json"], env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=900,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _SnapshotListError(f"restic could not be run: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:300] or f"exit code {proc.returncode}"
        raise _SnapshotListError(f"restic could not list snapshots: {detail}")
    try:
        snaps = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        raise _SnapshotListError(f"restic returned an unreadable snapshot list: {exc}") from exc
    if not isinstance(snaps, list):
        raise _SnapshotListError("restic returned an unreadable snapshot list")
    return snaps


def list_restore_points(repo: str, password: str) -> list[RestorePoint]:
    """Every recoverable generation, oldest first, each paired with a dump.

    The pairing takes the newest Postgres dump at or before the generation.
    A dump taken AFTER the file backup describes a database that has moved
    on from the files it would sit beside -- close enough to look right and
    wrong in exactly the way that is hard to notice later.
    """
    snaps = _snapshots(repo, password)
    gens: dict[int, RestorePoint] = {}
    pg: list[tuple[int, str]] = []

    for s in snaps:
        for p in s.get("paths", []):
            m = _GEN_RE.search(p)
            if m:
                g = int(m.group(1))
                # Keep the newest snapshot for a generation: bulk re-ingest
                # can produce more than one for identical content.
                prev = gens.get(g)
                if prev is None or s["time"] > prev.taken_at:
                    gens[g] = RestorePoint(
                        generation=g, snapshot_id=s["short_id"],
                        taken_at=s["time"][:19], repo=repo,
                    )
                continue
            mp = _PG_RE.search(p)
            if mp:
                pg.append((int(mp.group(1)), s["short_id"]))

    pg.sort()
    for point in gens.values():
        candidates = [x for x in pg if x[0] <= point.generation]
        chosen = candidates[-1] if candidates else (pg[0] if pg else None)
        if chosen:
            point.pg_generation, point.pg_snapshot_id = chosen
    return [gens[g] for g in sorted(gens)]


def select_point(points: list[RestorePoint],
                 generation: int | None = None) -> RestorePoint | None:
    """Newest by GENERATION, or the one asked for.

    Never ``latest``: see the module docstring. On this install the newest
    snapshot by time holds the oldest data.
    """
    if not points:
        return None
    if generation is None:
        return points[-1]
    for p in points:
        if p.generation == generation:
            return p
    return None


def _restic_restore(repo: str, password: str, snapshot: str,
                    target: Path) -> tuple[bool, str]:
    from kazma_core.backup.restic_repo import restore as _restore

    res = _restore(repo, password, str(target), snapshot=snapshot)
    return res.ok, (res.error or "")


def _find_backup_root(tree: Path) -> Path | None:
    """Locate the generation directory inside a restic-restored tree.

    restic rebuilds the source's absolute path under the target, so the
    backup sits several directories down and the depth differs per host.
    """
    for manifest in tree.rglob("manifest.json"):
        if (manifest.parent / "dbs").is_dir():
            return manifest.parent
    return None


def restore_files(
    repo: str,
    password: str,
    target: str | Path,
    *,
    generation: int | None = None,
    allow_nonempty: bool = False,
) -> RestoreResult:
    """Restore one generation into *target*, laid out like an install.

    Produces ``<target>/.env``, ``<target>/kazma.yaml``,
    ``<target>/kazma-data/`` and the Postgres dump alongside, which is the
    shape a fresh install expects. Nothing outside *target* is touched, and
    the databases are not loaded -- those are separate, explicit steps.
    """
    res = RestoreResult(target=str(target), ok=True)
    dest = Path(target)
    if dest.exists() and any(dest.iterdir()) and not allow_nonempty:
        res.ok = False
        res.error = (
            f"{dest} is not empty. A restore mixes two states into one that "
            "looks plausible and is neither -- point at a fresh directory."
        )
        return res

    try:
        points = list_restore_points(repo, password)
    except _SnapshotListError as exc:
        res.ok = False
        res.error = str(exc)
        return res
    if not points:
        res.ok = False
        res.error = "no restorable generations found in the repository"
        return res

    point = select_point(points, generation)
    if point is None:
        res.ok = False
        res.error = f"generation {generation} is not in this repository"
        return res
    res.generation = point.generation
    res.add("select", True, f"generation {point.label} snapshot {point.snapshot_id}")

    staging = dest / ".restic-staging"
    ok, err = _restic_restore(repo, password, point.snapshot_id, staging)
    res.add("restic restore", ok, err[:200] if err else point.snapshot_id)
    if not ok:
        return res

    root = _find_backup_root(staging)
    if root is None:
        res.add("locate backup", False, "no manifest.json with a dbs/ dir")
        return res
    res.add("locate backup", True, str(root))

    # Assemble the install layout: the backup's ``dbs/`` and ``assets/`` are
    # the data dir's CONTENTS (FA-268). They were copied in as two folders,
    # one level below every path Kazma reads, and a Kazma started on the
    # restore found none of its stores -- with every step reported ok.
    try:
        data_dir = dest / "kazma-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        restored_dbs = _flatten_into_data_dir(root, data_dir)
        moved = ["dbs", "assets"] if restored_dbs or (root / "assets").is_dir() else []
        for name in ("research", ".env", "kazma.yaml", "manifest.json", "neo4j_graph.jsonl"):
            src = root / name
            if not src.exists():
                continue
            tgt = dest / name
            if src.is_dir():
                shutil.copytree(src, tgt, dirs_exist_ok=True)
            else:
                shutil.copy2(src, tgt)
            moved.append(name)
        res.add("assemble", bool(moved), ", ".join(moved) or "nothing found")
    except Exception as exc:  # noqa: BLE001
        res.add("assemble", False, str(exc)[:200])
        return res

    # The .env is what makes the encrypted vault readable at all.
    res.add("env present", (dest / ".env").is_file(),
            "" if (dest / ".env").is_file()
            else "no .env -- the restored vault cannot be decrypted")
    res.add("config present", (dest / "kazma.yaml").is_file(),
            "" if (dest / "kazma.yaml").is_file()
            else "no kazma.yaml -- restored install boots with no tools")

    # Postgres dump, restored beside the files rather than loaded. Through a
    # staging folder of its own: restic rebuilds the source's absolute path
    # with its original permissions, and a ``pg/C/Users/...`` left in the
    # target could not be deleted without icacls (FA-270).
    if point.pg_snapshot_id:
        pg_staging = dest / ".restic-pg-staging"
        ok, err = _restic_restore(repo, password, point.pg_snapshot_id, pg_staging)
        dumps = sorted(pg_staging.rglob("pg_shared_*.dump")) if ok else []
        if dumps:
            pg_dir = dest / "pg"
            pg_dir.mkdir(exist_ok=True)
            for dump in dumps:
                shutil.copy2(dump, pg_dir / dump.name)
        _force_rmtree(pg_staging)
        res.add("postgres dump", bool(dumps),
                dumps[0].name if dumps else (err[:200] or "not found"))
    else:
        res.add("postgres dump", False,
                "no Postgres dump in this repository -- the database cannot "
                "be recovered from it")

    res.add(*_verify_databases(data_dir, restored_dbs))
    res.add(*_check_layout(dest, data_dir))

    # shutil.rmtree leaves read-only and briefly-locked files behind on
    # Windows, which stranded ~950 MB of duplicate data inside the restore
    # target on the first real run.
    _force_rmtree(staging)
    res.add("cleanup", not staging.exists(),
            "" if not staging.exists() else f"could not remove {staging}")
    # A restore that leaves no trace in the log is a recovery nobody can
    # confirm ever ran -- which is the audit's whole finding. Say it once,
    # in a line the firing ledger can count.
    logger.info("[restore] %s", res.summary())
    return res


def _force_rmtree(path: Path) -> None:
    """Remove the staging tree. Windows needs three steps, not one.

    restic reconstructs the source's absolute path under the target, so it
    synthesises the drive-letter parents -- and restores the ORIGINAL
    metadata onto them. Those parents inherit the real directory's owner,
    ACL and read-only attribute, and the result is a nearly empty tree that
    refuses to be deleted.

    Three explanations were tested before the right one. It is NOT MAX_PATH
    (deepest path measured 202 characters against a 260 limit) and NOT the
    read-only attribute alone (chmod cleared it and rmdir still failed).
    ``rmdir`` reports rc=0 while printing "Access is denied" to stderr, so
    even the exit code lies about it.

    What works is taking ownership first -- the current user does not own a
    directory restored with someone else's owner, and cannot change its ACL
    until they do -- then granting themselves full control, then deleting.

    Left unhandled this stranded ~950 MB inside every restore target.
    """
    if not path.exists():
        return

    def _run(args: list[str]) -> None:
        try:
            subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=900, check=False)
        except Exception:  # noqa: BLE001
            logger.debug("[restore] %s failed", args[0], exc_info=True)

    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        _run(["takeown", "/F", str(path), "/R", "/D", "Y"])
        if user:
            _run(["icacls", str(path), "/grant", f"{user}:(OI)(CI)F",
                  "/T", "/C", "/Q"])
        _run(["cmd", "/c", "rmdir", "/s", "/q", str(path)])
        if not path.exists():
            return

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        logger.debug("[restore] staging cleanup failed", exc_info=True)


#: What sits beside a SQLite file while a writer has it open. A raw copy of
#: one taken from a live data dir does not belong to the consistent copy the
#: backup's ``dbs/`` holds: opened beside it, SQLite would replay it.
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _posix(path: Path) -> str:
    return path.as_posix()


def _flatten_into_data_dir(root: Path, data_dir: Path) -> list[str]:
    """Lay a backup's ``assets/`` and ``dbs/`` out as the data dir's children.

    ``assets/`` first, then ``dbs/`` over it: a database nested in an asset
    folder is in both, raw in ``assets/`` and copied consistently in
    ``dbs/`` (FA-267), and the consistent one wins, with no sidecar of the
    raw one left beside it. Returns the databases restored from ``dbs/``,
    relative to the data dir.
    """
    dbs = root / "dbs"
    from_dbs = sorted(_posix(f.relative_to(dbs)) for f in dbs.rglob("*.db")) if dbs.is_dir() else []
    held = set(from_dbs)
    assets = root / "assets"
    if assets.is_dir():
        for child in sorted(assets.iterdir()):
            if child.is_dir():
                shutil.copytree(child, data_dir / child.name, dirs_exist_ok=True,
                                ignore=_raw_copies_of(held, assets))
            else:
                shutil.copy2(child, data_dir / child.name)
    for rel in from_dbs:
        target = data_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dbs / rel, target)
        for suffix in _SQLITE_SIDECARS:
            Path(str(target) + suffix).unlink(missing_ok=True)
    return from_dbs


def _raw_copies_of(held: set[str], assets: Path):
    """``copytree``'s ignore: an asset's database that ``dbs/`` holds, and its sidecars."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory)
        skipped = set()
        for name in names:
            base = name
            for suffix in _SQLITE_SIDECARS:
                if name.endswith(suffix):
                    base = name[: -len(suffix)]
                    break
            if _posix((here / base).relative_to(assets)) in held:
                skipped.add(name)
        return skipped
    return ignore


def _verify_databases(data_dir: Path, restored: list[str]) -> tuple[str, bool, str]:
    """Open every restored SQLite file and integrity-check it.

    Done here rather than deferred to the drill: the drill reads a BACKUP
    layout (manifest.json beside dbs/), not an install layout, so telling
    the operator to run it against a restored install was advice that could
    not work. Proof belongs in the restore, not in a follow-up command
    somebody has to remember.

    Read ``immutable=1``: ``mode=ro`` on a WAL database creates its ``-wal``
    and ``-shm`` beside it (FA-270), files the backup did not hold.
    """
    import sqlite3

    if not restored:
        return ("databases readable", False, "no databases restored")
    checked = 0
    bad: list[str] = []
    for rel in restored:
        f = data_dir / rel
        try:
            conn = sqlite3.connect(f"{f.resolve().as_uri()}?immutable=1", uri=True)
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
            finally:
                conn.close()
            if not row or str(row[0]).lower() != "ok":
                bad.append(f.name)
            checked += 1
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{f.name} ({str(exc)[:40]})")
    if bad:
        return ("databases readable", False,
                f"{len(bad)} of {checked} failed: {', '.join(bad[:4])}")
    return ("databases readable", True, f"{checked} databases pass integrity_check")


#: The stores ``kazma_core.paths`` places, by the function a Kazma started
#: on the restore would ask (FA-268): the layout is checked by its reader.
_STORE_PATH_FUNCTIONS = (
    "settings_db", "vault_db_path", "primary_memory_db", "memory_ops_db",
    "memory_forget_journal_db", "checkpoints_db", "knowledge_db",
    "swarm_tasks_db", "audit_db", "rbac_db",
)

_ASK_KAZMA = (
    "import json, sys\n"
    "from kazma_core import paths\n"
    "names = sys.argv[1].split(',')\n"
    "print(json.dumps({'data_dir': str(paths.data_dir()), "
    "'stores': {n: str(getattr(paths, n)()) for n in names}}))\n"
)


def _check_layout(dest: Path, data_dir: Path) -> tuple[str, bool, str]:
    """Ask Kazma, pinned to the restored data dir, where each store lives.

    A child process with no ``KAZMA_*`` variable but ``KAZMA_DATA_DIR``
    resolves every store path; each store the backup's manifest lists must
    be a file there. The restore used to report every step ok over a layout
    no reader looked in.
    """
    import json
    import subprocess
    import sys

    from kazma_core.security.child_env import tool_child_env

    try:
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        listed = {_posix(Path(str(item.get("path", ""))))
                  for item in (manifest.get("databases") or {}).get("items") or []
                  if isinstance(item, dict) and not item.get("error")}
    except (OSError, ValueError):
        return ("layout readable by Kazma", False, "no readable manifest.json restored")
    env = tool_child_env()
    env["KAZMA_DATA_DIR"] = str(data_dir)
    try:
        out = subprocess.run([sys.executable, "-c", _ASK_KAZMA, ",".join(_STORE_PATH_FUNCTIONS)],
                             env=env, capture_output=True, text=True, timeout=120, check=True).stdout
        seen = json.loads(out.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        return ("layout readable by Kazma", False, f"could not ask Kazma: {str(exc)[:120]}")
    if Path(seen["data_dir"]).resolve() != data_dir.resolve():
        return ("layout readable by Kazma", False, f"Kazma reads {seen['data_dir']}, not {data_dir}")
    missing = []
    found = 0
    for path in seen["stores"].values():
        try:
            rel = _posix(Path(path).resolve().relative_to(data_dir.resolve()))
        except ValueError:
            continue  # a store outside the data dir is not the restore's
        if Path(path).is_file():
            found += 1
        elif rel in listed:
            missing.append(rel)
    if missing:
        return ("layout readable by Kazma", False,
                f"{len(missing)} store(s) not where Kazma reads: {', '.join(missing[:4])}")
    if listed and not found:
        return ("layout readable by Kazma", False, "Kazma finds none of the backup's stores")
    return ("layout readable by Kazma", True, f"{found} store(s) where Kazma reads them")


def _next_steps(res: RestoreResult) -> str:
    """What a human still has to do, printed rather than performed."""
    d = Path(res.target)
    dump = next((p for p in (d / "pg").rglob("pg_shared_*.dump")), None)
    lines = [
        "",
        "Files are restored. The databases are NOT loaded -- that overwrites",
        "live data, so it stays an explicit decision:",
        "",
        f"  1. Point Kazma at {d} (or copy .env, kazma.yaml and kazma-data/",
        "     into a fresh install root).",
    ]
    if dump:
        lines += [
            "",
            "  2. Postgres:",
            f"       pg_restore --clean --if-exists -d <dsn> \"{dump}\"",
        ]
    if (d / "neo4j_graph.jsonl").is_file():
        lines += [
            "",
            "  3. Graph memory, into an EMPTY Neo4j:",
            "       python -c \"from kazma_core.backup.neo4j_backup import "
            f"restore_graph; print(restore_graph(r'{d / 'neo4j_graph.jsonl'}'))\"",
        ]
    lines += [
        "",
        "  The SQLite databases were already integrity-checked above; the",
        "  Postgres and graph loads are the parts still to prove.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Rebuild a Kazma install from a restic backup.")
    ap.add_argument("--target", help="empty directory to restore into")
    ap.add_argument("--generation", type=int, default=None,
                    help="which generation (default: newest DATA, not newest snapshot)")
    ap.add_argument("--repo", default="", help="restic repo (default: local)")
    ap.add_argument("--list", action="store_true", help="show restore points and exit")
    ap.add_argument("--allow-nonempty", action="store_true",
                    help="restore into a directory that already has files")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from kazma_core.backup.restic_repo import ensure_password, password_file, repo_paths

    password, _ = ensure_password()
    if not password:
        # The file restic_repo reads, not a place written here (FA-269: this
        # named the legacy ~/.kazma, the install's .kazma is the one used).
        print("No restic passphrase. Set KAZMA_RESTIC_PASSWORD or create "
              f"{password_file()}")
        return 2
    repo = args.repo or repo_paths()["local"]

    if args.list or not args.target:
        try:
            points = list_restore_points(repo, password)
        except _SnapshotListError as exc:
            print(f"Could not read {repo}: {exc}")
            return 2
        if not points:
            print("No restore points found in", repo)
            return 1
        print(f"{len(points)} restore point(s) in {repo}:\n")
        print(f"  {'GENERATION':<28} {'SNAPSHOT':<10} {'PG DUMP':<10} TAKEN")
        for p in points:
            print(f"  {p.label:<28} {p.snapshot_id:<10} "
                  f"{(p.pg_snapshot_id or '-'):<10} {p.taken_at}")
        print("\nNewest DATA:", points[-1].label)
        print("Restore with:  --target <empty dir> [--generation N]")
        return 0 if args.list else 1

    res = restore_files(repo, password, args.target,
                        generation=args.generation,
                        allow_nonempty=args.allow_nonempty)
    for s in res.steps:
        print(f"  [{'ok  ' if s['ok'] else 'FAIL'}] {s['step']}"
              + (f" -- {s['detail']}" if s["detail"] else ""))
    if res.error:
        print("\n" + res.error)
    print("\n" + res.summary())
    if res.ok:
        print(_next_steps(res))
    return 0 if res.ok else 1


if __name__ == "__main__":
    from kazma_core.env_files import load_env_files

    load_env_files()
    raise SystemExit(main())
