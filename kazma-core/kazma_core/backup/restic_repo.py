"""Deduplicated, encrypted snapshots with a restore that someone else tests.

Step 2 of the backup plan. The existing dump layer stays exactly as it is:
restic snapshots files and has no idea a WAL-mode SQLite database needs the
Online Backup API or that Postgres needs pg_dump. The two layers do
different jobs, and only one of them was missing.

What this replaces is everything around the dumps -- storage, retention,
encryption and recovery:

* 43 GB of near-identical full copies become one deduplicated repository
* the archive is encrypted, so KAZMA_SECRET stops travelling to Google
  Drive in the clear
* retention becomes real grandfather-father-son instead of "last 30 copies"
* and there is finally a ``restore``, which is the half that was never
  written

The passphrase
--------------
It is deliberately NOT ``KAZMA_SECRET``. The whole reason the current
archive ships secrets in plaintext is that the vault key and the thing it
protects travel together; reusing it here would rebuild that circularity
with extra steps.

It is also never generated silently. A passphrase that exists only on the
machine being backed up is not protection -- it is a second copy of the
same single point of failure, and the operator discovers this at exactly
the wrong moment. ``ensure_password`` will create one, but it says loudly
that it must be copied somewhere else, and ``password_is_offsite_ack``
stays False until the operator confirms they have.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ResticResult",
    "restic_available",
    "repo_paths",
    "password_file",
    "ensure_password",
    "init_repo",
    "backup",
    "forget_prune",
    "check",
    "snapshots",
    "restore",
    "KEEP_POLICY",
]

# Grandfather-father-son. A week of dailies covers "I broke it yesterday",
# two months of weeklies covers "this has been wrong for a while", and a
# year of monthlies covers the slow corruption nobody noticed. Retention by
# COUNT -- what the current system does -- gives you none of those
# guarantees: thirty backups is thirty days or thirty hours depending on
# how often the loop happened to run.
KEEP_POLICY: tuple[str, ...] = (
    "--keep-daily", "7",
    "--keep-weekly", "8",
    "--keep-monthly", "12",
)

# restic writes progress to stderr and can run for a while on first ingest.
_TIMEOUT_S = 3600


@dataclass
class ResticResult:
    ok: bool = False
    action: str = ""
    repo: str = ""
    stdout: str = ""
    error: str = ""
    skipped: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok, "action": self.action}
        if self.repo:
            d["repo"] = self.repo
        if self.error:
            d["error"] = self.error[:400]
        if self.skipped:
            d["skipped"] = self.skipped
        if self.detail:
            d.update(self.detail)
        return d


def restic_available() -> bool:
    return shutil.which("restic") is not None


def _data_dir() -> Path:
    from kazma_core.paths import data_dir

    return Path(data_dir())


def password_file() -> Path:
    """Where the repository passphrase lives on this machine."""
    return Path(os.path.expanduser("~")) / ".kazma" / "restic.pass"


def repo_paths() -> dict[str, str]:
    """Local and offsite repository locations.

    Two independent destinations, and the offsite one goes through rclone
    so it carries its own credential. Coupling the offsite copy to Kazma's
    Google token is what let 29 consecutive backups go local-only without
    anyone noticing.
    """
    local = str(_data_dir() / "backups" / "restic")
    remote = ""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        configured = str(store.get("backups.restic.remote") or "").strip()
        if configured:
            remote = configured
        else:
            rc = str(store.get("backups.offsite.rclone_remote") or "").strip()
            if rc:
                remote = f"rclone:{rc.rstrip('/')}/restic"
    except Exception:  # noqa: BLE001
        logger.debug("[restic] repo config read failed", exc_info=True)
    return {"local": local, "remote": remote}


def ensure_password(*, create: bool = False) -> tuple[str, bool]:
    """Return ``(passphrase, was_created)``. Never generates unless asked.

    A missing passphrase is an error, not something to paper over: silently
    generating one would produce a repository whose only key sits on the
    disk it is meant to survive.
    """
    env = os.environ.get("KAZMA_RESTIC_PASSWORD", "").strip()
    if env:
        return env, False

    path = password_file()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip(), False

    if not create:
        return "", False

    import secrets

    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort; Windows ACLs are not POSIX modes
    logger.critical(
        "[restic] A NEW repository passphrase was generated at %s. "
        "COPY IT SOMEWHERE THAT IS NOT THIS MACHINE. Without it every "
        "encrypted backup is unrecoverable, and a key stored only on the "
        "disk it protects is not a backup strategy.",
        path,
    )
    return secret, True


def password_is_offsite_ack() -> bool:
    """Has the operator confirmed the passphrase is stored off this machine?"""
    try:
        from kazma_core.config_store import get_config_store

        return bool(get_config_store().get("backups.restic.password_offsite_ack"))
    except Exception:  # noqa: BLE001
        return False


def _run(args: list[str], repo: str, password: str, *,
         action: str, timeout: int = _TIMEOUT_S) -> ResticResult:
    """Invoke restic. Never raises; the caller is usually a backup path."""
    res = ResticResult(action=action, repo=repo)
    if not restic_available():
        res.skipped = "restic is not installed"
        res.ok = True
        return res
    if not password:
        res.error = (
            "no repository passphrase. Set KAZMA_RESTIC_PASSWORD or create "
            f"{password_file()} -- refusing to guess."
        )
        return res

    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = repo
    env["RESTIC_PASSWORD"] = password
    try:
        proc = subprocess.run(
            ["restic", *args], env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        res.error = f"{action} would not run: {exc}"
        return res

    res.stdout = proc.stdout or ""
    if proc.returncode != 0:
        res.error = ((proc.stderr or proc.stdout or "").strip())[:400]
        return res
    res.ok = True
    return res


def init_repo(repo: str, password: str) -> ResticResult:
    """Create the repository if it does not exist. Idempotent."""
    existing = _run(["cat", "config"], repo, password, action="probe", timeout=120)
    if existing.ok:
        existing.action = "init"
        existing.detail["already"] = True
        return existing
    return _run(["init"], repo, password, action="init", timeout=600)


def backup(repo: str, password: str, paths: list[str], *,
           tags: list[str] | None = None) -> ResticResult:
    args = ["backup", "--json"]
    for t in (tags or ["kazma"]):
        args += ["--tag", t]
    args += paths
    res = _run(args, repo, password, action="backup")
    if res.ok:
        import json as _json

        for line in reversed(res.stdout.splitlines()):
            try:
                obj = _json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if obj.get("message_type") == "summary":
                res.detail.update({
                    "files_new": obj.get("files_new"),
                    "files_changed": obj.get("files_changed"),
                    "data_added": obj.get("data_added"),
                    "snapshot_id": (obj.get("snapshot_id") or "")[:8],
                })
                break
    return res


def forget_prune(repo: str, password: str,
                 policy: tuple[str, ...] = KEEP_POLICY) -> ResticResult:
    return _run(["forget", "--prune", *policy], repo, password, action="forget")


def check(repo: str, password: str, *, read_data: bool = False) -> ResticResult:
    args = ["check"]
    if read_data:
        # Reads and verifies every pack rather than just the metadata. Slow,
        # and the only thing that catches bit rot in the stored data.
        args.append("--read-data")
    return _run(args, repo, password, action="check")


def snapshots(repo: str, password: str) -> ResticResult:
    res = _run(["snapshots", "--json"], repo, password, action="snapshots", timeout=300)
    if res.ok:
        import json as _json

        try:
            snaps = _json.loads(res.stdout or "[]")
            res.detail["count"] = len(snaps)
            res.detail["latest"] = (snaps[-1].get("time") if snaps else "")
        except Exception:  # noqa: BLE001
            res.detail["count"] = 0
    return res


def restore(repo: str, password: str, target: str, *,
            snapshot: str = "latest") -> ResticResult:
    """Restore a snapshot into *target*. Refuses a non-empty directory.

    The target is created fresh on purpose. Restoring over an existing tree
    interleaves old and new state, and the result looks plausible while
    being neither.
    """
    t = Path(target)
    if t.exists() and any(t.iterdir()):
        r = ResticResult(action="restore", repo=repo)
        r.error = (
            f"restore target {target} is not empty. Restoring over existing "
            "files mixes two states into one that looks plausible and is "
            "neither. Point at a fresh directory."
        )
        return r
    t.mkdir(parents=True, exist_ok=True)
    res = _run(["restore", snapshot, "--target", str(t)], repo, password,
               action="restore")
    if not res.ok:
        benign, count = _only_directory_timestamp_errors(res.error + res.stdout)
        if benign:
            # Windows only, and cosmetic. restic reconstructs the source's
            # absolute path under the target, which means synthesising
            # directories like <target>\C\Users -- and it cannot set a
            # timestamp on those, so it exits non-zero after restoring every
            # byte correctly. Verified: "Restored 13 / 14 files/dirs", all
            # data present, the one failure being a directory mtime.
            #
            # Narrow on purpose. Only timestamp failures are forgiven, and
            # only when they are the ONLY errors reported -- a blanket
            # ignore here would turn a genuinely failed restore into a
            # reported success, which is the worst bug this file could have.
            res.ok = True
            res.detail["timestamp_warnings"] = count
            logger.warning(
                "[restic] restore completed; %d directory timestamp(s) could "
                "not be set (Windows path reconstruction, data unaffected)",
                count,
            )
    return res


def _only_directory_timestamp_errors(text: str) -> tuple[bool, int]:
    """True when every reported error is a directory-timestamp failure."""
    import re

    if not text:
        return False, 0
    m = re.search(r"There were (\d+) errors", text)
    if not m:
        return False, 0
    total = int(m.group(1))
    ts = len(re.findall(r"failed to restore timestamp", text))
    return (ts > 0 and ts == total), ts
