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
import time
from collections.abc import Callable
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
    """Where the repository passphrase lives on this machine.

    Project-local (``<install>/.kazma``), because that is the rule this
    project documents in paths.py -- state travels with the install -- and
    because the alternative actively bit us. It was written to
    ``~/.kazma``, and ``migrate_legacy_user_home`` tells operators in a
    warning that that directory is "safe to archive/delete" once the
    project home exists. On 2026-08-29 it went, and every encrypted
    snapshot became unreadable while backups carried on reporting success.

    An existing legacy file still wins, so an install that already has one
    keeps working until it is moved deliberately. ``.kazma/`` is
    gitignored, so the passphrase cannot be committed from here.

    It still belongs in a password manager as well: this is where the
    software looks, not where the only copy should live.
    """
    legacy = Path(os.path.expanduser("~")) / ".kazma" / "restic.pass"
    try:
        # Non-EMPTY only: a stray zero-byte file is not state worth
        # honouring, and pinning to it would send the operator's passphrase
        # to the directory this move exists to get out of.
        if legacy.is_file() and legacy.stat().st_size > 0:
            return legacy
    except OSError:
        pass
    try:
        from kazma_core.paths import user_home

        return Path(user_home()) / "restic.pass"
    except Exception:  # noqa: BLE001
        return legacy


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


# A remote that can be read but not written is the worst shape a backup
# destination can take, because every check that only reads says it is
# fine. A Google service account is exactly that: it has no Drive storage
# quota of its own, so it lists a shared folder in milliseconds and fails
# every upload with 403 storageQuotaExceeded. restic writes a lock file
# before it will even LIST snapshots, so on such a remote `restic
# snapshots` does not fail -- it retries with exponential backoff for
# fifteen minutes and reads as a hang. Two 600-second probes were spent
# proving that before the cause was visible.
#
# So prove the remote is writable first, cheaply, and say so plainly.
_WRITE_PROBE_TTL_S = 300.0
_write_probe_cache: dict[str, tuple[float, bool, str]] = {}


#: The probe writes and deletes one object under ``locks/``. That prefix is
#: chosen deliberately: an append-only backup key must still be allowed to
#: delete locks -- restic writes one at the start of every run and removes it
#: at the end -- so probing there exercises exactly the permission the policy
#: is meant to grant, and leaves nothing behind when it succeeds.
_S3_PROBE_PREFIX = "locks"


def _parse_s3_repo(repo: str) -> tuple[str, str, str] | None:
    """Split a restic ``s3:`` URL into (endpoint, bucket, key prefix)."""
    rest = repo[len("s3:"):]
    if rest.startswith(("http://", "https://")):
        scheme, _, tail = rest.partition("://")
    else:
        scheme, tail = "https", rest
    host, _, path = tail.partition("/")
    parts = [seg for seg in path.split("/") if seg]
    if not host or not parts:
        return None
    return f"{scheme}://{host}", parts[0], "/".join(parts[1:])


def _sigv4_request(
    method: str, endpoint: str, bucket: str, key: str, payload: bytes, timeout: float
) -> tuple[int, str]:
    """One path-style S3 call signed with SigV4. Returns (status, detail).

    Deliberately hand-rolled against the standard library rather than pulling
    in boto3: the point of this function is to run when the backup path is
    already suspect, so it should not depend on anything that path does not
    already need.
    """
    import datetime
    import hashlib
    import hmac
    import urllib.error
    import urllib.parse
    import urllib.request

    access = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    token = os.environ.get("AWS_SESSION_TOKEN", "")
    if not access or not secret:
        return 0, "no AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the environment"
    # R2 wants "auto"; B2 and AWS supply a real region. Either signs fine.
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "auto"

    host = urllib.parse.urlsplit(endpoint).netloc
    now = datetime.datetime.now(datetime.UTC)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_uri = "/" + urllib.parse.quote(f"{bucket}/{key}", safe="/~")
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    if token:
        headers["x-amz-security-token"] = token
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    # Fields: method, URI, query (empty), headers, signed-header list, digest.
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    scope = f"{datestamp}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amzdate,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _sign(key_bytes: bytes, msg: str) -> bytes:
        return hmac.new(key_bytes, msg.encode(), hashlib.sha256).digest()

    signing_key = _sign(
        _sign(_sign(_sign(f"AWS4{secret}".encode(), datestamp), region), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urllib.request.Request(
        f"{endpoint}{canonical_uri}", data=payload, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        return exc.code, f"HTTP {exc.code}: {body or exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {str(exc)[:200]}"


def _s3_writable(repo: str) -> tuple[bool, str]:
    """PUT then DELETE one object under ``locks/``. Never raises."""
    parsed = _parse_s3_repo(repo)
    if parsed is None:
        return False, f"could not parse the S3 repository URL {repo!r}"
    endpoint, bucket, prefix = parsed
    key = "/".join(
        seg
        for seg in (prefix, _S3_PROBE_PREFIX, f".kazma-write-probe-{int(time.time())}")
        if seg
    )

    status, detail = _sigv4_request("PUT", endpoint, bucket, key, b"probe", 30.0)
    if status not in (200, 201):
        if status in (401, 403):
            detail = (
                "the remote is READ-ONLY for this credential: the S3 key was "
                f"refused on PUT ({detail}). Offsite restic snapshots are NOT "
                "being written until this is changed."
            )
        return False, detail or f"unexpected status {status} on PUT"

    # Cleaning up is part of the test, not politeness. An append-only key that
    # cannot delete its own locks wedges restic a few runs later, and that
    # should surface here rather than at 3am.
    status, detail = _sigv4_request("DELETE", endpoint, bucket, key, b"", 30.0)
    if status not in (200, 204):
        return False, (
            "writes succeed but this key cannot delete under locks/ "
            f"({detail or status}). restic writes a lock on every run and "
            "removes it at the end, so stale locks will accumulate until it "
            "refuses to back up. Grant DeleteObject on the locks/ prefix."
        )
    return True, ""


def remote_writable(repo: str, *, force: bool = False) -> tuple[bool, str]:
    """Can we actually PUT to this remote? Cached; never raises.

    A local path is writable or the backup has already failed loudly. Anything
    remote gets proved rather than assumed, because a destination that reads
    fine and refuses every write is the one shape where all the cheap checks
    lie -- which is exactly how this went unnoticed the first time.
    """
    if repo.startswith("s3:"):
        now = time.time()
        hit = _write_probe_cache.get(repo)
        if hit and not force and now - hit[0] < _WRITE_PROBE_TTL_S:
            return hit[1], hit[2]
        try:
            ok, detail = _s3_writable(repo)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"write probe would not run: {str(exc)[:200]}"
        _write_probe_cache[repo] = (now, ok, detail)
        return ok, detail
    if not repo.startswith("rclone:"):
        return True, ""
    remote = repo[len("rclone:"):]
    now = time.time()
    hit = _write_probe_cache.get(remote)
    if hit and not force and now - hit[0] < _WRITE_PROBE_TTL_S:
        return hit[1], hit[2]

    ok, detail = False, ""
    name = f".kazma-write-probe-{int(now)}"
    try:
        env = dict(os.environ)
        env.setdefault("RCLONE_RETRIES", "1")
        env.setdefault("RCLONE_LOW_LEVEL_RETRIES", "1")
        proc = subprocess.run(
            ["rclone", "rcat", f"{remote.rstrip('/')}/{name}"],
            input="probe", env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=90, check=False,
        )
        ok = proc.returncode == 0
        if not ok:
            detail = _meaningful_error(proc.stderr, proc.stdout)
        else:
            subprocess.run(
                ["rclone", "deletefile", f"{remote.rstrip('/')}/{name}"],
                env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=90, check=False,
            )
    except Exception as exc:  # noqa: BLE001
        detail = f"write probe would not run: {str(exc)[:200]}"

    if not ok and "storageQuotaExceeded" in detail:
        detail = (
            "the remote is READ-ONLY for this credential: Google service "
            "accounts have no Drive storage quota, so uploads fail with 403 "
            "storageQuotaExceeded even when reads succeed. Use a user OAuth "
            "remote (or a Shared Drive, which needs Workspace) instead. "
            "Offsite restic snapshots are NOT being written until this is "
            "changed."
        )
    _write_probe_cache[remote] = (now, ok, detail)
    return ok, detail



def alert_missing_password(source: str) -> bool:
    """Shout when snapshots are being skipped for want of a passphrase.

    This used to be a single INFO line, and on 2026-08-29 that is exactly
    what it looked like for four hours while every snapshot was silently
    dropped -- the backup still reported "complete", because the local
    dump had in fact been written. A missing passphrase with an existing
    repository is not a configuration note. It means new data is not being
    protected AND the history already in that repository cannot be read
    back without the key.

    A FRESH install with no repository is a different case: nothing is at
    stake yet, and alerting there would train the operator to ignore this.
    So the alert fires only when a repository actually exists.

    Returns whether an alert was raised.
    """
    try:
        local = repo_paths().get("local") or ""
        exists = bool(local) and (Path(local) / "config").is_file()
        if not exists:
            logger.info("[restic] no passphrase and no repository yet (%s)", source)
            return False

        from kazma_core.observability.ops_alerts import alert

        alert(
            "backup.restic_passphrase_missing",
            "Encrypted snapshots are being SKIPPED -- no restic passphrase.",
            (f"{source} found an existing repository at {local} but no "
             f"passphrase, so every snapshot is being dropped silently. "
             f"Restore {password_file()} from wherever you saved it, or set "
             "KAZMA_RESTIC_PASSWORD. Until then new data is unprotected and "
             "the history already in that repository cannot be decrypted."),
            severity="critical",
            cooldown_s=6 * 3600,
        )
        return True
    except Exception:  # noqa: BLE001 -- alerting must never break a backup
        logger.warning("[restic] could not raise missing-passphrase alert",
                       exc_info=True)
        return False


def alert_read_only_remote(repo: str, why: str) -> bool:
    """Shout when the offsite repository accepts reads but refuses writes."""
    try:
        from kazma_core.observability.ops_alerts import alert

        alert(
            "backup.restic_remote_read_only",
            "Offsite snapshots are NOT being written -- the remote is read-only.",
            f"{repo}: {why[:400]}",
            severity="critical",
            cooldown_s=6 * 3600,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[restic] could not raise read-only-remote alert",
                       exc_info=True)
        return False


def _run(args: list[str], repo: str, password: str, *,
         action: str, timeout: int = _TIMEOUT_S,
         stdin: str | None = None) -> ResticResult:
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

    writable, why = remote_writable(repo)
    if not writable:
        res.error = f"{action} skipped -- {why}"
        alert_read_only_remote(repo, why)
        return res

    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = repo
    env["RESTIC_PASSWORD"] = password
    # Bound the rclone child restic spawns. Without this a permanently
    # failing PUT is retried until the whole operation looks hung.
    env.setdefault("RCLONE_RETRIES", "2")
    env.setdefault("RCLONE_LOW_LEVEL_RETRIES", "3")
    try:
        proc = subprocess.run(
            ["restic", *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, input=stdin,
        )
    except Exception as exc:  # noqa: BLE001
        res.error = f"{action} would not run: {exc}"
        return res

    res.stdout = proc.stdout or ""
    if proc.returncode != 0:
        res.error = _meaningful_error(proc.stderr, proc.stdout)
        return res
    res.ok = True
    return res


def _meaningful_error(stderr: str | None, stdout: str | None) -> str:
    """Return the part of the output that actually says what went wrong.

    rclone prints a NOTICE about its shared Google client_id on EVERY
    invocation, and it lands on stderr before anything else. Taking the
    first 400 characters therefore reported the notice and truncated away
    the real error -- a failed offsite restore whose message said only that
    a client id is being retired in 2026.

    An error message that hides the error is worse than no message: it
    looks like a diagnosis and sends you somewhere else entirely.
    """
    text = ((stderr or "") + chr(10) + (stdout or "")).strip()
    if not text:
        return "failed with no output"
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and "NOTICE:" not in ln
    ]
    if not lines:
        return text[:400]
    # Prefer lines that name a failure; fall back to the tail, which is
    # where a CLI usually puts its verdict.
    flagged = [
        ln for ln in lines
        if any(w in ln.lower() for w in ("error", "fatal", "failed", "cannot",
                                         "denied", "no such", "unable"))
    ]
    chosen = flagged or lines[-3:]
    return " | ".join(chosen)[:400]


def init_repo(repo: str, password: str) -> ResticResult:
    """Create the repository if it does not exist. Idempotent."""
    existing = _run(["cat", "config"], repo, password, action="probe", timeout=120)
    if existing.ok:
        existing.action = "init"
        existing.detail["already"] = True
        return existing
    return _run(["init"], repo, password, action="init", timeout=600)


# restic treats a lock as stale once it is older than this and its owning
# process is gone. Matching restic's own default rather than inventing one.
_STALE_LOCK_AGE_S = 30 * 60


def unlock_stale(repo: str, password: str) -> ResticResult:
    """Remove locks whose owning process is dead. Safe on a live repository.

    ``restic unlock`` without ``--remove-all`` only clears locks restic
    itself judges stale -- the owner is gone and the lock is older than its
    threshold. A lock held by a running backup is left alone, which is what
    makes this safe to run unattended.

    It matters because a killed restic leaves an EXCLUSIVE lock behind, and
    every subsequent backup then fails with "repository is already locked".
    That is worse than the orphaned temp files this codebase already grew:
    those merely wasted disk, this silently stops backing anything up while
    the schedule keeps reporting that it ran. Kazma is restarted mid-backup
    regularly, so it is a matter of time rather than bad luck.

    Observed 2026-08-29: a check killed by a shell timeout held the offsite
    repository until it was unlocked by hand.
    """
    return _run(["unlock"], repo, password, action="unlock", timeout=600)


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


def rotate_password(
    repos: dict[str, str],
    old: str,
    new: str,
    *,
    persist: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Replace the repository passphrase without re-encrypting the data.

    restic keys are indirection: the data is encrypted with a master key,
    and each passphrase merely unlocks a copy of it. Adding a key and
    removing the old one therefore costs nothing and rewrites nothing.

    Order matters and is the whole safety of this function. The new key is
    added to EVERY repository and verified to actually work before ANY old
    key is removed. Removing first, or removing from one repository while
    another still rejects the new key, would leave a repository nobody can
    open -- turning a precautionary rotation into the data loss it was
    meant to prevent.

    ``persist`` closes the last window in that ordering, and it exists
    because the first live rotation walked straight into it. The caller
    used to store the new passphrase only after this function returned,
    which leaves an interval -- old keys revoked, new passphrase still
    only in memory -- where killing the process locks every repository
    forever. That interval was observed: mid-rotation the local repository
    genuinely stopped opening with the stored passphrase.

    So the new passphrase is written HERE, after every repository has
    verified it and before the first old key is revoked. Worst case the
    file names a passphrase that both keys accept, which is harmless; the
    alternative worst case is a repository nobody can ever open.
    """
    out: dict[str, Any] = {"added": [], "verified": [], "removed": [], "errors": []}
    targets = {k: v for k, v in repos.items() if v}

    # restic reads the new passphrase from a FILE, not stdin ("--new-password-file -"
    # is taken literally and fails with "- does not exist"). The file is written
    # with a restrictive mode, lives only for the duration of the call, and is
    # removed in a finally so a crash mid-rotation cannot leave a passphrase
    # lying in the temp directory.
    import tempfile

    fd, pw_path = tempfile.mkstemp(prefix="kazma-restic-", suffix=".pw")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new)
        try:
            os.chmod(pw_path, 0o600)
        except OSError:
            pass

        for name, repo in targets.items():
            res = _run(["key", "add", "--new-password-file", pw_path], repo, old,
                       action="key-add", timeout=600)
            if res.ok:
                out["added"].append(name)
            else:
                out["errors"].append(f"{name}: add failed: {res.error[:200]}")
    finally:
        try:
            os.remove(pw_path)
        except OSError:
            pass

    if out["errors"]:
        out["ok"] = False
        out["note"] = "no old key removed; every repository still opens with it"
        return out

    for name, repo in targets.items():
        if _run(["cat", "config"], repo, new, action="verify", timeout=300).ok:
            out["verified"].append(name)
        else:
            out["errors"].append(f"{name}: the new passphrase does not open it")

    if len(out["verified"]) != len(targets):
        out["ok"] = False
        out["note"] = "no old key removed; the old passphrase still works"
        return out

    # Persist BEFORE revoking anything -- see the note in the docstring.
    if persist is not None:
        try:
            persist(new)
            out["persisted"] = True
        except Exception as exc:  # noqa: BLE001
            out["ok"] = False
            out["errors"].append(f"could not store the new passphrase: {exc}")
            out["note"] = (
                "no old key removed; refusing to revoke a working passphrase "
                "when the replacement could not be saved"
            )
            return out

    for name, repo in targets.items():
        listed = _run(["key", "list", "--json"], repo, new,
                      action="key-list", timeout=300)
        old_ids: list[str] = []
        try:
            import json as _json

            for k in _json.loads(listed.stdout or "[]"):
                if not k.get("current"):
                    old_ids.append(str(k.get("id") or ""))
        except Exception:  # noqa: BLE001
            out["errors"].append(f"{name}: could not list keys")
            continue
        for kid in old_ids:
            if _run(["key", "remove", kid], repo, new,
                    action="key-remove", timeout=300).ok:
                out["removed"].append(f"{name}:{kid[:8]}")

    out["ok"] = not out["errors"]
    return out


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
