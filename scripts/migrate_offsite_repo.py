"""Move the offsite restic repository to S3-compatible object storage.

Why this exists
---------------
The offsite copy lived on Google Drive through rclone. A Google service
account has no Drive storage quota of its own, so it listed the folder in
milliseconds and failed every upload with ``403 storageQuotaExceeded``: a
destination that reads perfectly and writes nothing. Every health check that
only listed reported success. Drive cannot be fixed for this use without
Workspace, so the destination changes rather than the workaround stack.

restic speaks S3 natively. Removing rclone removes a moving part, a config
file with its own failure modes, and a credential shape that cannot work.

The gate
--------
This script will NOT switch the live configuration until it has restored real
data out of the new repository. Not an ``init``, not a ``snapshots`` listing,
not a write probe -- a restore, compared byte for byte against the source.

That rule is here because on 2026-08-29 the backups were declared fixed on
the strength of an ``rclone lsd``, which is a read, against a failure that was
write-only. The alert arrived again an hour later. A verification that cannot
fail the way the system fails is not a verification, so the check is wired
into the control flow instead of left to judgement.

Usage
-----
Credentials come from the environment (``AWS_ACCESS_KEY_ID``,
``AWS_SECRET_ACCESS_KEY``, optionally ``AWS_DEFAULT_REGION`` -- use ``auto``
for Cloudflare R2)::

    python scripts/migrate_offsite_repo.py \\
        --endpoint https://<account>.r2.cloudflarestorage.com \\
        --bucket kazma-backup

It runs read-only by default and prints what it would do. Add ``--apply`` to
actually create, copy and verify, and ``--cutover`` to additionally point the
live config at the new repository once verification has passed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kazma-core"))

from kazma_core.backup import restic_repo as rr  # noqa: E402


class MigrationError(RuntimeError):
    """Any step that must stop the migration."""


def _say(msg: str) -> None:
    print(msg, flush=True)


def _restic(args: list[str], *, password_file: str | Path, extra_env: dict | None = None,
            timeout: int = 3600, binary: bool = False) -> subprocess.CompletedProcess:
    """Run restic. ``binary=True`` keeps stdout as bytes, for ``dump``."""
    env = dict(os.environ)
    # str(): these come from password_file(), which returns a Path, and
    # CreateProcess rejects a non-string value in the environment mapping.
    env["RESTIC_PASSWORD_FILE"] = str(password_file)
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    if binary:
        return subprocess.run(
            ["restic", *args], env=env, capture_output=True,
            timeout=timeout, check=False,
        )
    return subprocess.run(
        ["restic", *args], env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _require_credentials() -> None:
    missing = [
        k for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
        if not os.environ.get(k)
    ]
    if missing:
        raise MigrationError(
            f"missing {', '.join(missing)} in the environment. Put the host "
            "key in .env -- and keep the full-access key OUT of this machine."
        )


def _check_writable(repo: str) -> None:
    """The probe from restic_repo, forced past its cache."""
    ok, detail = rr.remote_writable(repo, force=True)
    if not ok:
        raise MigrationError(f"the new repository is not writable: {detail}")
    _say("  writable: PUT and DELETE under locks/ both succeeded")


def _init(repo: str, password_file: str) -> None:
    probe = _restic(["-r", repo, "cat", "config"], password_file=password_file, timeout=120)
    if probe.returncode == 0:
        _say("  already initialised -- leaving it alone")
        return
    res = _restic(["-r", repo, "init"], password_file=password_file, timeout=300)
    if res.returncode != 0:
        raise MigrationError(f"restic init failed: {(res.stderr or res.stdout)[:500]}")
    _say("  initialised")


def _copy(src: str, dst: str, password_file: str) -> None:
    """Copy every snapshot across. Same passphrase both sides, deliberately.

    Deduplication does not carry over between repositories, so this re-uploads
    the data once. At 1.9 GB that is minutes, and it happens exactly once.
    """
    res = _restic(
        ["-r", dst, "copy", "--from-repo", src],
        password_file=password_file,
        extra_env={"RESTIC_FROM_PASSWORD_FILE": password_file},
        timeout=7200,
    )
    if res.returncode != 0:
        raise MigrationError(f"restic copy failed: {(res.stderr or res.stdout)[:800]}")
    _say("  copied")


def _snapshots(repo: str, password_file: str) -> list[dict]:
    import json
    res = _restic(["-r", repo, "snapshots", "--json"], password_file=password_file, timeout=600)
    if res.returncode != 0:
        raise MigrationError(f"could not list snapshots in {repo}: {(res.stderr or '')[:400]}")
    try:
        return json.loads(res.stdout or "[]")
    except Exception as exc:  # noqa: BLE001
        raise MigrationError(f"unreadable snapshot listing: {exc}") from exc


def _snapshot_files(repo: str, snap_id: str, password_file) -> list[tuple[str, int]]:
    """(stored path, size) for every regular file in a snapshot.

    Asked of restic rather than derived from the local path. restic stores
    Windows paths in its own normalised form -- ``C:\\x\\y`` becomes
    ``/C/x/y`` -- and ``dump`` only accepts that form, so guessing the
    transform is a bug waiting for the first path that does not fit it.
    """
    import json

    res = _restic(
        ["-r", repo, "ls", "--json", snap_id], password_file=password_file, timeout=1800
    )
    if res.returncode != 0:
        raise MigrationError(
            f"could not list snapshot {snap_id}: {(res.stderr or res.stdout)[:400]}"
        )
    out: list[tuple[str, int]] = []
    for line in (res.stdout or "").splitlines():
        try:
            node = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if node.get("struct_type") == "node" and node.get("type") == "file":
            size = int(node.get("size") or 0)
            if size > 0 and node.get("path"):
                out.append((node["path"], size))
    return out


def _check_integrity(repo: str, password_file) -> None:
    """Structural verification: does every pack the index promises exist?"""
    res = _restic(["-r", repo, "check"], password_file=password_file, timeout=3600)
    if res.returncode != 0:
        raise MigrationError(
            f"restic check failed -- migration not accepted: {(res.stderr or res.stdout)[:800]}"
        )
    _say("  check: repository structure is intact")


def _verify_by_reading_back(repo: str, password_file, snapshots: list[dict]) -> None:
    """THE gate: pull real file content back out and compare it byte for byte.

    Deliberately ``dump`` rather than ``restore``. A full restore also tries to
    reapply filesystem metadata, and on Windows it fails setting a timestamp on
    the synthetic directories it creates under the target -- restic exits
    non-zero over a metadata detail while the data itself came back perfectly.
    A gate that trips on that is worse than no gate, because the next person
    will disable it.

    ``dump`` streams the file out of the repository: it reads the packs,
    decrypts them, and reassembles the content. Comparing those bytes against
    the live file proves the three things that actually matter -- the data is
    stored, the key still opens it, and what comes back is what went in.
    """
    # Map each stored path back to a live file, so the comparison has
    # something to be right about. Newest snapshots first.
    candidates: list[tuple[str, str, str]] = []  # (snapshot, stored path, live path)
    for snap in reversed(snapshots):
        snap_id = snap.get("short_id") or snap.get("id") or ""
        if not snap_id:
            continue
        live_by_name = {}
        for root in snap.get("paths") or []:
            rp = Path(root)
            if rp.is_file():
                live_by_name[rp.name] = str(rp)
        for stored, _size in _snapshot_files(repo, snap_id, password_file):
            live = live_by_name.get(stored.replace("\\", "/").rsplit("/", 1)[-1])
            if live:
                candidates.append((snap_id, stored, live))
        if len(candidates) >= 3:
            break

    if not candidates:
        raise MigrationError(
            "no file in any snapshot still exists on disk to compare against, "
            "so a read-back cannot be proved. Refusing to call this verified."
        )

    verified = 0
    for snap_id, stored, live_path in candidates[:3]:
        res = _restic(
            ["-r", repo, "dump", snap_id, stored],
            password_file=password_file, timeout=3600, binary=True,
        )
        if res.returncode != 0:
            detail = res.stderr or b""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", "replace")
            raise MigrationError(
                f"READ-BACK FAILED for {stored} -- migration not accepted: {detail[:600]}"
            )
        live = Path(live_path).read_bytes()
        if res.stdout != live:
            raise MigrationError(
                f"READ-BACK MISMATCH for {stored}: {len(res.stdout)} bytes out of "
                f"the repository vs {len(live)} on disk. Migration not accepted."
            )
        verified += 1
        _say(f"  read back, byte-identical: {Path(live_path).name} ({len(live):,} bytes)")

    _say(f"  {verified} file(s) proved to come back out of the repository intact")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", required=True, help="e.g. https://acct.r2.cloudflarestorage.com")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", default="", help="optional key prefix inside the bucket")
    ap.add_argument("--apply", action="store_true", help="actually init, copy and verify")
    ap.add_argument("--cutover", action="store_true",
                    help="after verification passes, point the live config at the new repo")
    args = ap.parse_args()

    endpoint = args.endpoint.rstrip("/")
    path = "/".join(p for p in (args.bucket, args.prefix.strip("/")) if p)
    new_repo = f"s3:{endpoint}/{path}"

    paths = rr.repo_paths()
    local, old_remote = paths["local"], paths["remote"]

    _say("Offsite repository migration")
    _say(f"  source (local):  {local}")
    _say(f"  current offsite: {old_remote or '(none configured)'}")
    _say(f"  new offsite:     {new_repo}")
    _say("")

    if not args.apply:
        _say("DRY RUN. Nothing was changed. Re-run with --apply to migrate,")
        _say("and add --cutover to switch the live config once a restore has passed.")
        return 0

    try:
        _require_credentials()

        password_file = rr.password_file()
        if not Path(password_file).is_file():
            raise MigrationError(
                "no restic passphrase on this machine. Without it the existing "
                "1.9 GB of history cannot be read back at all."
            )

        _say("[1/5] probing the new repository for WRITE access")
        _check_writable(new_repo)

        _say("[2/5] initialising")
        _init(new_repo, password_file)

        _say("[3/5] copying snapshots from the local repository")
        src_snaps = _snapshots(local, password_file)
        _say(f"  source holds {len(src_snaps)} snapshots")
        _copy(local, new_repo, password_file)
        dst_snaps = _snapshots(new_repo, password_file)
        _say(f"  destination holds {len(dst_snaps)} snapshots")
        if len(dst_snaps) < len(src_snaps):
            raise MigrationError(
                f"destination has fewer snapshots than the source "
                f"({len(dst_snaps)} < {len(src_snaps)})"
            )

        _say("[4/5] VERIFYING BY READING THE DATA BACK -- the only check that counts")
        _check_integrity(new_repo, password_file)
        _verify_by_reading_back(new_repo, password_file, dst_snaps)

        _say("[5/5] cutover")
        if not args.cutover:
            _say("  skipped (--cutover not given). The new repository is populated")
            _say("  and verified; the live config still points at the old one.")
            return 0

        from kazma_core.config_store import get_config_store
        store = get_config_store()
        store.set("backups.restic.remote", new_repo)
        _say(f"  backups.restic.remote = {new_repo}")
        _say("")
        _say("Done. Remaining, and both are yours:")
        _say("  * delete the rclone remote and its service-account JSON")
        _say("  * confirm the passphrase is stored somewhere that is not this machine")
        return 0

    except MigrationError as exc:
        _say("")
        _say(f"STOPPED: {exc}")
        _say("The live configuration was NOT changed.")
        return 1
    except subprocess.TimeoutExpired as exc:
        _say("")
        _say(f"STOPPED: restic timed out after {exc.timeout}s.")
        _say("The live configuration was NOT changed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
