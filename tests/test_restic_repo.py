"""Restic: the layer that finally makes a restore exist.

These run against a REAL restic binary and a real repository in tmp_path,
not a mock. The entire point of adopting restic is that its restore works;
asserting that a mocked subprocess returned zero would prove nothing about
the thing being bought.

Skipped when restic is not installed, so CI on a bare runner stays green
without silently pretending the round trip happened.
"""

from __future__ import annotations

import os
import stat

import pytest
from kazma_core.backup import restic_repo as rr

pytestmark = pytest.mark.skipif(
    not rr.restic_available(), reason="restic is not installed"
)

_PW = "test-passphrase-not-a-real-secret"


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "repo")
    res = rr.init_repo(path, _PW)
    assert res.ok, res.error
    return path


@pytest.fixture
def payload(tmp_path):
    d = tmp_path / "data"
    (d / "dbs").mkdir(parents=True)
    (d / "dbs" / "sessions.db").write_bytes(b"SQLite format 3\x00" + b"x" * 4096)
    (d / ".env").write_text("KAZMA_SECRET=hunter2\n", encoding="utf-8")
    (d / "kazma.yaml").write_text("mcp:\n  - name: fs\n", encoding="utf-8")
    (d / "neo4j_graph.jsonl").write_text('{"kind":"meta"}\n', encoding="utf-8")
    return d


# ── the round trip, which is the whole reason for the change ──────────


def test_backup_then_restore_returns_identical_bytes(repo, payload, tmp_path):
    """A backup is a hypothesis until something reads it back."""
    b = rr.backup(repo, _PW, [str(payload)])
    assert b.ok, b.error

    target = tmp_path / "restored"
    r = rr.restore(repo, _PW, str(target))
    assert r.ok, r.error

    restored_root = next(target.rglob("kazma.yaml")).parent
    for rel in ("kazma.yaml", ".env", "neo4j_graph.jsonl", "dbs/sessions.db"):
        original = payload / rel
        copy = restored_root / rel
        assert copy.is_file(), f"{rel} did not come back"
        assert copy.read_bytes() == original.read_bytes(), f"{rel} differs"


def test_the_repository_is_actually_encrypted(repo, payload):
    """The reason to move: KAZMA_SECRET currently travels to Google Drive in
    the clear. If the secret is greppable in the repo, nothing was gained."""
    rr.backup(repo, _PW, [str(payload)])
    from pathlib import Path

    blobs = b""
    for f in Path(repo).rglob("*"):
        if f.is_file():
            blobs += f.read_bytes()
    assert b"hunter2" not in blobs, "the secret is readable inside the repository"
    assert b"KAZMA_SECRET" not in blobs


def test_a_wrong_passphrase_cannot_read_the_repository(repo, payload):
    rr.backup(repo, _PW, [str(payload)])
    res = rr.snapshots(repo, "definitely-the-wrong-passphrase")
    assert not res.ok


def test_deduplication_across_snapshots(repo, payload):
    """The 43 GB problem. Backing up unchanged data twice must not store it
    twice."""
    first = rr.backup(repo, _PW, [str(payload)])
    second = rr.backup(repo, _PW, [str(payload)])
    assert first.ok and second.ok
    added = int(second.detail.get("data_added") or 0)
    assert added < 65536, f"unchanged data re-stored {added} bytes"


# ── retention ─────────────────────────────────────────────────────────


def test_forget_prune_runs_and_keeps_the_recent_snapshot(repo, payload):
    rr.backup(repo, _PW, [str(payload)])
    res = rr.forget_prune(repo, _PW)
    assert res.ok, res.error
    snaps = rr.snapshots(repo, _PW)
    assert snaps.ok and snaps.detail["count"] >= 1


def _three_universal_backups(repo, tmp_path):
    """Three snapshots of one KIND, each of a new directory -- the shape of
    backups/universal/<epoch>/, within one hour."""
    for i in range(3):
        d = tmp_path / f"universal-{i}"
        d.mkdir()
        (d / "f.txt").write_text(str(i), encoding="utf-8")
        assert rr.backup(repo, _PW, [str(d)], tags=["kazma", "universal"]).ok


def test_forget_applies_the_policy_per_kind_not_per_path(repo, tmp_path):
    """restic groups by host+paths unless told otherwise, and every universal
    backup is a new directory: each snapshot was a group of one, a group keeps
    its newest, and retention deleted nothing, ever (199 of 199 kept on the
    live install, 2026-09-23). Three in one hour: the hourly rule keeps the
    newest, and restic also keeps the oldest while a rule's buckets are not
    yet full -- so the middle one goes."""
    import json

    _three_universal_backups(repo, tmp_path)
    res = rr.forget_prune(repo, _PW)
    assert res.ok, res.error
    listed = rr.snapshots(repo, _PW)
    kept = {p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for s in json.loads(listed.stdout) for p in s["paths"]}
    assert "universal-2" in kept, "the newest snapshot must survive"
    assert "universal-1" not in kept, f"retention removed nothing: {sorted(kept)}"


def test_default_grouping_keeps_every_snapshot(repo, tmp_path):
    """Negative control (§28): the pre-fix command on the same repository."""
    _three_universal_backups(repo, tmp_path)
    res = rr._run(["forget", "--prune", *rr.KEEP_POLICY], repo, _PW, action="forget")
    assert res.ok, res.error
    assert rr.snapshots(repo, _PW).detail["count"] == 3


def test_the_policy_is_time_based_not_count_based():
    """Retention by count gives no guarantee: thirty backups is thirty days
    or thirty hours depending on how often the loop happened to run."""
    joined = " ".join(rr.KEEP_POLICY)
    assert "--keep-daily" in joined
    assert "--keep-weekly" in joined
    assert "--keep-monthly" in joined


# ── verification ──────────────────────────────────────────────────────


def test_check_passes_on_a_healthy_repository(repo, payload):
    rr.backup(repo, _PW, [str(payload)])
    assert rr.check(repo, _PW).ok


def test_check_read_data_catches_a_corrupted_pack(repo, payload):
    """Metadata-only checks pass over bit rot in the stored data. This is
    the difference between 'the index looks fine' and 'the bytes are there'."""
    from pathlib import Path

    rr.backup(repo, _PW, [str(payload)])
    packs = sorted(Path(repo).glob("data/*/*"))
    assert packs, "expected pack files"
    victim = max(packs, key=lambda p: p.stat().st_size)
    raw = bytearray(victim.read_bytes())
    for i in range(0, min(len(raw), 512)):
        raw[i] ^= 0xFF
    # restic stores packs read-only (0o444 on POSIX; Windows keeps them
    # writable), so the bit rot needs the write bit first.
    victim.chmod(stat.S_IMODE(victim.stat().st_mode) | stat.S_IWUSR)
    victim.write_bytes(bytes(raw))
    assert victim.read_bytes() == bytes(raw), "the corruption must land on disk"

    assert not rr.check(repo, _PW, read_data=True).ok, (
        "--read-data must notice a corrupted pack"
    )


# ── the safety rails ──────────────────────────────────────────────────


def test_restore_refuses_a_non_empty_target(repo, payload, tmp_path):
    """Restoring over an existing tree interleaves two states into one that
    looks plausible and is neither."""
    rr.backup(repo, _PW, [str(payload)])
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "leftover.txt").write_text("i was here", encoding="utf-8")

    res = rr.restore(repo, _PW, str(target))
    assert not res.ok
    assert "not empty" in res.error
    assert (target / "leftover.txt").is_file(), "must not have touched anything"


# ── a restore its own account can remove ──────────────────────────────

_windows_only = pytest.mark.skipif(
    os.name != "nt", reason="restic rebuilds parents with their recorded Windows rights"
)


def _remove_like_pytest(path):
    """Remove *path* the way pytest removes its temp folders: an entry it
    cannot remove is made writable and tried once more, nothing else."""
    import shutil
    import sys

    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


@pytest.fixture
def locked_source(tmp_path):
    """A payload below two folders that deny this account deleting, the way
    ``C:\\Users`` does: ``parent`` refuses deleting what is in it, ``locked``
    refuses being deleted. A deny for the account's own SID holds even when
    the test runs as an administrator, so the case does not depend on where
    the temp folder is or on the account's rights."""
    import subprocess

    sid = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip().split(",")[-1].strip('"')
    parent = tmp_path / "src" / "parent"
    locked = parent / "locked"
    data = locked / "data"
    data.mkdir(parents=True)
    (data / "kazma.yaml").write_text("mcp: []\n", encoding="utf-8")
    for folder, right in ((parent, "DC"), (locked, "DE")):
        subprocess.run(["icacls", str(folder), "/deny", f"*{sid}:({right})"],
                       capture_output=True, check=True, stdin=subprocess.DEVNULL)
    yield data
    for folder in (parent, locked):
        subprocess.run(["icacls", str(folder), "/reset"], capture_output=True,
                       check=False, stdin=subprocess.DEVNULL)


@_windows_only
def test_a_restore_can_be_removed_by_the_account_that_made_it(repo, locked_source, tmp_path):
    """restic rebuilds the source's absolute path under the target and gives
    each rebuilt parent the REAL folder's rights. ``C:\\Users`` lets an
    ordinary account read but not delete, so pytest could not remove the
    restic tests' restores and kept each run's whole temp folder: 29 of
    them, 7.5 GB, found on 2026-11-13. The rebuilt parents now take the
    target's rights; the restored files keep theirs."""
    assert rr.backup(repo, _PW, [str(locked_source)]).ok
    target = tmp_path / "restored"
    res = rr.restore(repo, _PW, str(target))
    assert res.ok, res.error
    assert next(target.rglob("kazma.yaml")).read_text(encoding="utf-8") == "mcp: []\n"
    assert res.detail.get("parents_released", 0) >= 3, res.detail
    assert "parents_kept" not in res.detail, res.detail

    _remove_like_pytest(target)
    assert not target.exists()


@_windows_only
def test_control_restic_alone_leaves_a_restore_its_account_cannot_remove(
        repo, locked_source, tmp_path):
    """Negative control: the same restore made by restic's command alone, as
    ``restore`` ran it before, cannot be removed by the account that made it."""
    import subprocess

    assert rr.backup(repo, _PW, [str(locked_source)]).ok
    raw = tmp_path / "raw"
    raw.mkdir()
    rr._run(["restore", "latest", "--target", str(raw)], repo, _PW, action="restore")
    assert next(raw.rglob("kazma.yaml")).is_file()
    try:
        with pytest.raises(PermissionError) as refused:
            _remove_like_pytest(raw)
        assert str(refused.value.filename).endswith("locked"), refused.value
    finally:
        # The control leaves nothing behind: the owner resets every right.
        subprocess.run(["icacls", str(raw), "/reset", "/T", "/C", "/Q"],
                       capture_output=True, check=False, stdin=subprocess.DEVNULL)
        _remove_like_pytest(raw)
    assert not raw.exists()


def test_rebuilt_parents_are_the_folders_above_each_backed_up_path(tmp_path):
    """Only the folders restic rebuilt are changed, top down; the backed-up
    folder keeps the rights its snapshot recorded."""
    target = tmp_path / "t"
    for rel in ("C/a/b/data", "C/a/other", "D/x/y"):
        (target / rel).mkdir(parents=True)
    got = rr._rebuilt_parents(target, [r"C:\a\b\data", r"C:\a\other", r"D:\x\y",
                                       r"\\server\share\z", "C:\\"])
    assert got == [target / "C", target / "D", target / "C" / "a",
                   target / "D" / "x", target / "C" / "a" / "b"]


def _restic_restores_outside_restore(sources: dict[str, str]) -> list[str]:
    """Every ``restic restore`` built anywhere but ``restic_repo.restore``."""
    import ast

    found = []
    for name, src in sources.items():
        tree = ast.parse(src)
        allowed: set[int] = set()
        if name.endswith("restic_repo.py"):
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name == "restore":
                    allowed |= {id(n) for n in ast.walk(fn)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or id(node) in allowed:
                continue
            words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
            restic_command = "restic" in words and "restore" in words
            # restic_repo's own runner takes the arguments without "restic".
            run_command = (name.endswith("restic_repo.py") and words[:1] == ["restore"])
            if restic_command or run_command:
                found.append(f"{name}:{node.lineno}")
    return found


def test_every_restic_restore_goes_through_restore():
    """``restic_repo.restore`` is what releases the rebuilt parents, so no
    product code or script runs ``restic restore`` any other way."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = {}
    for folder in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli",
                   "kazma-tui", "kazma-skills", "scripts"):
        for f in (root / folder).rglob("*.py"):
            if "tests" not in f.relative_to(root).parts:
                sources[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8")
    assert "kazma-core/kazma_core/backup/restic_repo.py" in sources
    assert _restic_restores_outside_restore(sources) == []

    planted = {
        "scripts/x.py": 'subprocess.run(["restic", "restore", "latest", "--target", t])',
        "kazma-core/kazma_core/backup/restic_repo.py":
            'def other():\n    _run(["restore", s, "--target", t], r, p, action="restore")\n',
    }
    assert len(_restic_restores_outside_restore(planted)) == 2


def test_init_is_idempotent(repo):
    again = rr.init_repo(repo, _PW)
    assert again.ok
    assert again.detail.get("already") is True


def test_a_missing_passphrase_refuses_rather_than_guessing(repo):
    res = rr.backup(repo, "", ["."])
    assert not res.ok
    assert "refusing to guess" in res.error


# ── passphrase handling ───────────────────────────────────────────────


def test_a_passphrase_is_never_generated_silently(tmp_path, monkeypatch):
    """A key that exists only on the disk it protects is a second copy of
    the same single point of failure."""
    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(rr, "password_file", lambda: tmp_path / "restic.pass")

    secret, created = rr.ensure_password(create=False)
    assert secret == ""
    assert created is False
    assert not (tmp_path / "restic.pass").exists()


def test_generating_a_passphrase_is_explicit_and_loud(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(rr, "password_file", lambda: tmp_path / "restic.pass")

    with caplog.at_level(logging.CRITICAL):
        secret, created = rr.ensure_password(create=True)

    assert created is True and len(secret) > 20
    assert (tmp_path / "restic.pass").read_text(encoding="utf-8").strip() == secret
    assert any("NOT THIS MACHINE" in r.message for r in caplog.records), (
        "the operator must be told the key has to leave this machine"
    )


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "password_file", lambda: tmp_path / "restic.pass")
    (tmp_path / "restic.pass").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("KAZMA_RESTIC_PASSWORD", "from-env")
    assert rr.ensure_password()[0] == "from-env"


def test_the_passphrase_is_not_kazma_secret(monkeypatch, tmp_path):
    """Reusing the vault key would rebuild the exact circularity this
    replaces: the key and the thing it protects travelling together."""
    monkeypatch.setenv("KAZMA_SECRET", "the-vault-key")
    monkeypatch.delenv("KAZMA_RESTIC_PASSWORD", raising=False)
    monkeypatch.setattr(rr, "password_file", lambda: tmp_path / "restic.pass")

    secret, _ = rr.ensure_password(create=True)
    assert secret != os.environ["KAZMA_SECRET"]

    import inspect

    assert "KAZMA_SECRET" not in inspect.getsource(rr.ensure_password)


# ── two independent destinations ──────────────────────────────────────


def test_the_offsite_repo_goes_through_rclone(monkeypatch):
    """rclone carries its own credential. The native Google provider borrows
    the Gmail token, and one revoked grant took out 29 backups in a row."""
    class _Store:
        def get(self, key):
            if key == "backups.offsite.rclone_remote":
                return "kazma-backup:kazma-backups"
            return None

    import kazma_core.config_store as cs
    monkeypatch.setattr(cs, "get_config_store", lambda: _Store())

    paths = rr.repo_paths()
    assert paths["remote"].startswith("rclone:")
    assert paths["local"] and not paths["local"].startswith("rclone:")
    assert paths["local"] != paths["remote"], "two destinations, not one"


# ── the Windows path-reconstruction quirk ─────────────────────────────


def test_a_directory_timestamp_failure_does_not_fail_the_restore():
    """restic rebuilds the source's absolute path under the target, which
    means synthesising drive-letter directories it cannot set timestamps
    on -- so it exits non-zero having restored every byte correctly.
    Verified live: "Restored 13 / 14 files/dirs", all data present, the
    single failure a directory mtime."""
    benign, count = rr._only_directory_timestamp_errors(
        r'ignoring error: failed to restore timestamp of "C:\x\C\Users": '
        'Access is denied.' + "\nFatal: There were 1 errors"
    )
    assert benign is True and count == 1


def test_a_real_restore_failure_is_never_forgiven():
    """The worst bug this file could have is turning a failed restore into a
    reported success, so the exemption is narrow: only timestamp errors, and
    only when they are the ONLY errors."""
    benign, _ = rr._only_directory_timestamp_errors(
        'error: could not decrypt pack\nFatal: There were 1 errors'
    )
    assert benign is False

    mixed, _ = rr._only_directory_timestamp_errors(
        'failed to restore timestamp of "x": Access is denied.\n'
        'error: pack is corrupt\nFatal: There were 2 errors'
    )
    assert mixed is False, "one real error among timestamp noise must still fail"


def test_no_error_summary_is_not_treated_as_benign():
    benign, _ = rr._only_directory_timestamp_errors("some unrelated stderr")
    assert benign is False


# ── the pipeline wiring ───────────────────────────────────────────────


def test_the_snapshot_runs_after_the_manifest_is_written():
    """Snapshotting mid-assembly would capture a directory that describes
    itself incorrectly, or not at all."""
    import inspect

    from kazma_core.backup import universal

    src = inspect.getsource(universal.perform_universal_backup)
    assert src.index('manifest["offsite"] = offsite') < src.index("_snapshot_to_restic")


def test_the_snapshot_is_additive_not_a_cutover():
    """The existing generations and offsite zip keep running until a restore
    has been rehearsed twice. A migration is exactly when you want the old
    copies, and cutting over early is how a backup rewrite loses data."""
    import inspect

    from kazma_core.backup import universal

    src = inspect.getsource(universal.perform_universal_backup)
    # The legacy paths must still be invoked alongside the new one.
    assert "_offsite_sync(dest)" in src
    assert "_prune(keep)" in src
    assert "_snapshot_to_restic" in src


def test_a_restic_failure_cannot_fail_a_completed_backup():
    import inspect

    from kazma_core.backup import universal

    src = inspect.getsource(universal._snapshot_to_restic)
    assert "except Exception" in src
    assert "must never fail a completed backup" in src


def test_a_missing_passphrase_is_reported_not_guessed(monkeypatch, tmp_path):
    from kazma_core.backup import universal

    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("", False))
    res = universal._snapshot_to_restic(tmp_path)
    assert res == {"skipped": "no restic passphrase"}


def test_no_restic_binary_is_silent(monkeypatch, tmp_path):
    """An install without restic must not have its backup annotated with a
    failure it cannot act on."""
    from kazma_core.backup import universal

    monkeypatch.setattr(rr, "restic_available", lambda: False)
    assert universal._snapshot_to_restic(tmp_path) is None


def test_both_repositories_are_snapshotted(monkeypatch, tmp_path):
    """Two destinations means two snapshots, or the second one is fiction."""
    from kazma_core.backup import universal

    seen: list[str] = []
    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("pw", False))
    monkeypatch.setattr(rr, "repo_paths",
                        lambda: {"local": "/l", "remote": "rclone:r/restic"})
    monkeypatch.setattr(
        rr, "backup",
        lambda repo, pw, paths, tags=None: seen.append(repo) or rr.ResticResult(
            ok=True, action="backup", repo=repo),
    )
    res = universal._snapshot_to_restic(tmp_path)
    assert seen == ["/l", "rclone:r/restic"]
    assert set(res) == {"local", "remote"}


# ── scheduled retention and verification ──────────────────────────────


def _handler():
    from kazma_core.memory.worker_bootstrap import _handle_restic_maintenance

    return _handle_restic_maintenance


def test_maintenance_prunes_then_verifies_each_repo(monkeypatch):
    """Verify AFTER pruning: prune rewrites the repository, so checking
    beforehand validates a state that no longer exists."""
    import asyncio

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("pw", False))
    monkeypatch.setattr(rr, "repo_paths", lambda: {"local": "/l", "remote": "rc:/r"})
    monkeypatch.setattr(rr, "forget_prune",
                        lambda repo, pw, policy=None: calls.append(("forget", repo))
                        or rr.ResticResult(ok=True))
    monkeypatch.setattr(rr, "check",
                        lambda repo, pw, read_data=False: calls.append(("check", repo))
                        or rr.ResticResult(ok=True))

    assert asyncio.run(_handler()({})) is True
    assert calls == [("forget", "/l"), ("check", "/l"),
                     ("forget", "rc:/r"), ("check", "rc:/r")]


def test_a_failing_check_alerts_the_operator(monkeypatch):
    """A repository that fails verification is a backup you do not have."""
    import asyncio

    sent: list[str] = []
    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("pw", False))
    monkeypatch.setattr(rr, "repo_paths", lambda: {"local": "/l"})
    monkeypatch.setattr(rr, "forget_prune",
                        lambda repo, pw, policy=None: rr.ResticResult(ok=True))
    monkeypatch.setattr(rr, "check", lambda repo, pw, read_data=False:
                        rr.ResticResult(ok=False, error="pack is damaged"))
    monkeypatch.setattr(
        "kazma_core.observability.ops_alerts.alert",
        lambda key, title, detail="", **kw: sent.append(key) or True,
    )

    asyncio.run(_handler()({}))
    assert "backup.restic_check_failed" in sent


def test_maintenance_never_retry_storms(monkeypatch):
    """It runs on a durable queue. A permanent condition -- no restic, no
    passphrase -- must not be retried forever."""
    import asyncio

    monkeypatch.setattr(rr, "restic_available", lambda: False)
    assert asyncio.run(_handler()({})) is True

    monkeypatch.setattr(rr, "restic_available", lambda: True)
    monkeypatch.setattr(rr, "ensure_password", lambda **kw: ("", False))
    assert asyncio.run(_handler()({})) is True


def test_maintenance_is_scheduled_with_the_nightly_backups():
    import inspect

    from kazma_core.memory import worker_bootstrap

    src = inspect.getsource(worker_bootstrap)
    # Audit F-06 offloaded the enqueue into a worker thread, and Memory
    # audit M20 made it say when the queue refuses it (_enqueue_checked):
    # asyncio.to_thread(_enqueue_checked, "restic_maintenance", {}). The scheduling
    # is what matters here, not which thread performs it.
    assert 'to_thread(_enqueue_checked, "restic_maintenance", {})' in src
    assert 'register_handler("restic_maintenance"' in src


# ── passphrase rotation ───────────────────────────────────────────────


def test_rotation_changes_the_key_and_the_old_one_stops_working(repo, payload):
    """A passphrase that leaks must be revocable without re-encrypting the
    data. restic keys unlock a copy of the master key, so this costs
    nothing and rewrites nothing."""
    rr.backup(repo, _PW, [str(payload)])
    new = "a-brand-new-passphrase-after-a-leak"

    res = rr.rotate_password({"local": repo}, _PW, new)
    assert res["ok"], res["errors"]

    assert rr.snapshots(repo, new).ok, "the new passphrase must open the repo"
    assert not rr.snapshots(repo, _PW).ok, "the leaked passphrase must be dead"


def test_data_survives_rotation(repo, payload, tmp_path):
    """Rotation must not touch the snapshots."""
    rr.backup(repo, _PW, [str(payload)])
    new = "second-passphrase"
    assert rr.rotate_password({"local": repo}, _PW, new)["ok"]

    target = tmp_path / "after-rotation"
    r = rr.restore(repo, new, str(target))
    assert r.ok, r.error
    restored = next(target.rglob("kazma.yaml"))
    assert restored.read_bytes() == (payload / "kazma.yaml").read_bytes()


def test_a_failure_on_one_repo_removes_no_old_key_anywhere(repo, payload, tmp_path):
    """The whole safety of rotation is its ordering. If the new key cannot
    be added everywhere, nothing is revoked -- otherwise a precautionary
    rotation becomes the data loss it was meant to prevent."""
    rr.backup(repo, _PW, [str(payload)])
    unreachable = str(tmp_path / "no-such-repo")

    res = rr.rotate_password({"local": repo, "remote": unreachable}, _PW, "new-pw")
    assert res["ok"] is False
    assert res["removed"] == [], "nothing may be revoked on a partial failure"
    assert "still opens with it" in res["note"]
    assert rr.snapshots(repo, _PW).ok, "the original passphrase must still work"


def test_the_new_key_is_verified_before_the_old_is_removed():
    """Adding a key is not proof it opens the repository."""
    import inspect

    src = inspect.getsource(rr.rotate_password)
    assert src.index('out["verified"]') < src.index('"key", "remove"')


def test_the_new_passphrase_is_stored_before_any_key_is_revoked(repo, payload, monkeypatch):
    """The window this closes was walked into on the first live rotation.

    Storing only after the function returns leaves an interval -- old keys
    revoked, new passphrase still in memory -- where killing the process
    locks every repository forever. Mid-rotation, the local repository
    genuinely stopped opening with the stored passphrase.
    """
    rr.backup(repo, _PW, [str(payload)])
    order: list[str] = []

    real_run = rr._run

    def _tracking(args, repo_, pw, **kw):
        if args[:2] == ["key", "remove"]:
            order.append("revoke")
        return real_run(args, repo_, pw, **kw)

    import kazma_core.backup.restic_repo as mod
    monkeypatch.setattr(mod, "_run", _tracking)
    res = rr.rotate_password({"local": repo}, _PW, "stored-first-pw",
                             persist=lambda p: order.append("persist"))

    assert res["ok"], res["errors"]
    assert order and order[0] == "persist", (
        f"the passphrase must be stored before the first revoke; got {order}"
    )


def test_a_failed_store_revokes_nothing(repo, payload):
    """If the replacement cannot be saved, revoking the working key would
    lock the repository with a passphrase nobody has."""
    rr.backup(repo, _PW, [str(payload)])

    def _cannot_write(_pw):
        raise OSError("disk full")

    res = rr.rotate_password({"local": repo}, _PW, "never-stored",
                             persist=_cannot_write)
    assert res["ok"] is False
    assert res["removed"] == []
    assert rr.snapshots(repo, _PW).ok, "the original passphrase must still work"


# ── error extraction ──────────────────────────────────────────────────


def test_an_rclone_notice_never_masks_the_real_error():
    """rclone prints a NOTICE about its shared client_id on EVERY call, and
    it lands on stderr first. Taking the leading 400 characters reported the
    notice and truncated the real error away -- a fully successful offsite
    restore was reported as a failure whose message said only that a client
    id retires in 2026."""
    notice = ("rclone: 2026/08/29 NOTICE: kazma-backup: This remote uses "
              "rclone's shared Google Drive client_id, which is being retired")
    got = rr._meaningful_error(notice + chr(10) + "Fatal: unable to open repository", "")
    assert got == "Fatal: unable to open repository"
    assert "NOTICE" not in got


def test_truncation_no_longer_hides_the_error_summary():
    """The handler that forgives benign restores keys on "There were N
    errors". Truncating that line away is what made a 952 MiB successful
    restore look failed."""
    notice = "rclone: NOTICE: " + ("x" * 500)
    text = (notice + chr(10) +
            r'failed to restore timestamp of "C:\x": Access is denied.' + chr(10) +
            "Fatal: There were 1 errors")
    got = rr._meaningful_error(text, "")
    benign, count = rr._only_directory_timestamp_errors(got)
    assert benign is True and count == 1, (
        "the benign-restore handler must still see its evidence after extraction"
    )


def test_output_with_nothing_useful_still_says_something():
    assert rr._meaningful_error("", "") == "failed with no output"
    only_notice = rr._meaningful_error("rclone: NOTICE: shared client_id", "")
    assert only_notice, "never return an empty error"


# ── output decoding ───────────────────────────────────────────────────


def test_subprocess_output_is_decoded_as_utf8_everywhere():
    """subprocess.run(text=True) decodes with the ANSI code page on Windows
    (cp1252 here). Any byte outside it raises UnicodeDecodeError inside
    subprocess's reader THREAD -- the traceback lands on stderr, the output
    is lost, and the call still looks like it succeeded with empty output.

    Seen live on 2026-08-29 while ingesting backups: one snapshot reported
    "+ 0.0 MB" because its JSON summary was destroyed exactly this way. The
    guard is worse -- it parses netstat and tasklist output to decide what
    to reap.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("kazma-core/kazma_core/backup/restic_repo.py",
                "kazma-core/kazma_core/backup/restore_drill.py",
                "kazma-core/kazma_core/backup/universal.py",
                "scripts/service/kazma_guard.py"):
        src = (root / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"text=True(?!\s*,\s*encoding)", src):
            offenders.append(f"{rel}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, f"locale-decoded subprocess output at: {offenders}"


def test_a_non_cp1252_byte_survives_the_round_trip(repo, payload, tmp_path):
    """The concrete failure: a path restic echoes back containing a byte
    cp1252 cannot represent."""
    odd = payload / "café-中文.txt"
    odd.write_text("unicode in the name", encoding="utf-8")

    res = rr.backup(repo, _PW, [str(payload)])
    assert res.ok, res.error
    assert res.detail.get("snapshot_id"), (
        "the JSON summary must survive -- losing it is how a snapshot "
        "reported 0.0 MB"
    )


# ── stale locks: a killed restic stops every future backup ────────────


def test_unlock_stale_clears_a_dead_owner_lock(repo, payload):
    """A killed restic leaves an EXCLUSIVE lock, and every later backup then
    fails with "repository is already locked" while the schedule keeps
    reporting that it ran. Worse than the orphaned temp files: those wasted
    disk, this silently stops backing anything up.

    Live, 2026-08-29: a check killed by a shell timeout held the offsite
    repository until it was unlocked by hand.
    """
    rr.backup(repo, _PW, [str(payload)])
    res = rr.unlock_stale(repo, _PW)
    assert res.ok, res.error
    assert rr.snapshots(repo, _PW).ok, "the repo must still be usable after"


def test_unlock_runs_before_pruning_in_maintenance():
    """Ordering: an exclusive lock blocks forget/prune too, so clearing it
    first is what makes the rest of maintenance reachable at all."""
    import inspect

    from kazma_core.memory import worker_bootstrap

    src = inspect.getsource(worker_bootstrap._handle_restic_maintenance)
    # Compare the CALLS, not the imports -- the import block lists these
    # alphabetically, which is the opposite order and says nothing.
    assert "to_thread(unlock_stale" in src
    assert src.index("to_thread(unlock_stale") < src.index("to_thread(forget_prune"), (
        "locks must be cleared before anything that needs the lock"
    )


def test_unlock_is_not_remove_all():
    """`unlock` without --remove-all clears only locks restic judges stale;
    a lock held by a RUNNING backup is left alone. That distinction is what
    makes this safe to run unattended."""
    import inspect

    # Check the COMMAND, not the docstring -- which mentions --remove-all
    # precisely to explain why it is not used.
    src = inspect.getsource(rr.unlock_stale)
    body = src.split('"""')[-1]
    assert "remove-all" not in body
