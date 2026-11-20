"""Spaces S1-28: backups and migration carry the identity store; a restore
keeps spaces apart.

Two spaces are planted in every store (``tests/_space_world.py``: a word of
A's and another of B's in every content column, their chats recorded in
their space, their settings, secrets and files). Then:

* a real universal backup of the data folder, laid out the way
  ``kazma restore`` lays it out (``restore._flatten_into_data_dir``);
* a real migration bundle (``export_bundle``), imported into an empty data
  folder by a child process (``import_bundle``);

and each restored folder is judged by a child pinned to it: the identity
store holds both spaces and their memberships, each chat thread's home is
its space, and each space counts and exports exactly what it did before the
backup -- A's export holds A's word, never B's (S1-27's walk, the matrix of
every store).

The Settings file (``settings_restore``) is the admin's: once Spaces is
enforced it holds no other space's settings or Soul, and a restore of an
older file never writes one. And a backup that holds several people's
spaces never leaves the machine as a plain copy (decision N10).

Controls: a restore without the identity store loses the spaces (their
counts and exports refuse, their chats are nobody's); the Settings file and
restore of before S1-28 carry another space's keys.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from kazma_core import paths
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, scope, space_data

from tests._space_world import A, B, plant_world

# The child that judges a restored data folder.
_JUDGE = r"""
import json, sys, zipfile
from pathlib import Path
args = json.loads(sys.argv[1])
from kazma_core import paths
if args.get("vault"):
    paths.vault_db_path = lambda: args["vault"]
from kazma_core.spaces import identity, scope, space_data, threads
scope.set_enforced(True)
reg = identity.read_registry()
out = {
    "spaces": sorted(reg["spaces"]),
    "memberships": sorted([m["account"], m["space"], m.get("role", "")] for m in reg["memberships"]),
    "counts": {}, "homes": {}, "exports": {},
}
for space in args["spaces"]:
    try:
        out["counts"][space] = space_data.count_space(space)
    except space_data.SpaceDataRefused as exc:
        out["counts"][space] = exc.code
    target = Path(args["scratch"]) / (space + ".zip")
    try:
        space_data.export_space(space, target)
        with zipfile.ZipFile(target) as zipped:
            out["exports"][space] = "\n".join(
                zipped.read(n).decode("utf-8", "replace") for n in zipped.namelist())
    except space_data.SpaceDataRefused as exc:
        out["exports"][space] = exc.code
for thread in args["threads"]:
    out["homes"][thread] = threads.thread_home(thread)
print("JUDGED" + json.dumps(out))
"""

#: Never asked of a restored folder: what changes when it moves.
_VOLATILE = ("secrets",)


def _child_env(data_dir: Path) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items()
           if k not in ("KAZMA_MEMORY_STATE_DB", "KAZMA_MEMORY_OPS_DB", "PYTEST_CURRENT_TEST")}
    env["KAZMA_DATA_DIR"] = str(data_dir)
    env["KAZMA_TEST_EGRESS_GUARD"] = "0"
    return env


def _judge(data_dir: Path, scratch: Path, *, threads: list[str], vault: Path | None) -> dict[str, Any]:
    scratch.mkdir(parents=True, exist_ok=True)
    args = {"spaces": [A, B], "threads": threads, "scratch": str(scratch),
            "vault": str(vault) if vault else ""}
    done = subprocess.run([sys.executable, "-c", _JUDGE, json.dumps(args)], env=_child_env(data_dir),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, check=False, stdin=subprocess.DEVNULL)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("JUDGED")), "")
    assert line, done.stderr[-3000:]
    return json.loads(line[len("JUDGED"):])


def _counts(found: Any, *, with_secrets: bool = False) -> Any:
    """A count as a backup carries it: without the stores no backup holds
    (``store_registry.NOT_BACKED_UP``: Time Travel's steps), and, for a
    bundle, without the secrets (it carries the install's vault.db)."""
    from kazma_core.store_registry import NOT_BACKED_UP

    if not isinstance(found, dict):
        return found
    stores = {k: v for k, v in found["stores"].items() if k not in NOT_BACKED_UP}
    out = {k: v for k, v in found.items() if k != "stores" and (with_secrets or k not in _VOLATILE)}
    out["stores"] = stores
    out["rows"] = sum(stores.values())
    return out


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The two spaces in a data folder of the test's own, with the settings
    and memory databases in it, as on an install: the backup copies what is
    there (the session keeps them in each test's own folder)."""
    yield from plant_world(monkeypatch, tmp_path)


def _threads_of(space: str) -> list[str]:
    with closing(sqlite3.connect(f"file:{paths.data_dir() / 'thread_scopes.db'}?mode=ro", uri=True)) as conn:
        return [r[0] for r in conn.execute("SELECT thread_id FROM thread_scopes WHERE space_id = ?", (space,))]


def _before(world) -> dict[str, Any]:
    reg = identity.read_registry()
    return {
        "spaces": {A, B},
        "memberships": {(m["account"], m["space"], m.get("role", "")) for m in reg["memberships"]
                        if m["space"] in (A, B)},
        "counts": {space: space_data.count_space(space) for space in (A, B)},
        "threads": {space: _threads_of(space) for space in (A, B)},
        "carried_settings": {space: _carried_settings(space) for space in (A, B)},
    }


def _carried_store(name: str) -> bool:
    """Whether a migration bundle carries the store *name*, as the registry
    declares it: a ``machine`` store never travels (the session manager's
    pytest names, ``chat_sessions_test*``, are such: the product's
    ``chat_sessions.db`` does)."""
    from kazma_core.store_registry import STORE_FAMILIES, STORES

    declared = STORES.get(name) or STORE_FAMILIES.get(name)
    return declared is not None and declared.migration != "machine"


def _carried_settings(space: str) -> int:
    """The space's settings a migration bundle carries: never a chat's
    running state (``settings_restore`` RUNTIME, FA-255)."""
    from kazma_core.migration.importer import _carried_in_migration

    keys = space_data._settings_keys(space, _threads_of(space))
    return sum(1 for key in keys if _carried_in_migration(key)) + len(space_data._mapping_entries(space))


def _assert_apart(judged: dict[str, Any], before: dict[str, Any], world, *, with_secrets: bool) -> None:
    assert before["spaces"] <= set(judged["spaces"]), judged["spaces"]
    assert before["memberships"] <= {tuple(m) for m in judged["memberships"]}
    for space in (A, B):
        got = _counts(judged["counts"][space], with_secrets=with_secrets)
        had = _counts(before["counts"][space], with_secrets=with_secrets)
        if not with_secrets:  # a migration: only the settings and stores a bundle carries
            had["settings"] = before["carried_settings"][space]
            had["stores"] = {k: v for k, v in had["stores"].items() if _carried_store(k)}
            had["rows"] = sum(had["stores"].values())
        assert got == had, (space, got, had)
        for thread in before["threads"][space]:
            assert judged["homes"][thread] == space, (space, thread)
    assert world["words"][A] in judged["exports"][A]
    assert world["words"][B] not in judged["exports"][A]
    assert world["words"][B] in judged["exports"][B]
    assert world["words"][A] not in judged["exports"][B]


# ── the universal backup ────────────────────────────────────────────────


def _universal_backup(tmp_path: Path, monkeypatch) -> Path:
    from kazma_core.backup import universal

    base = tmp_path / "backups"
    base.mkdir()
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    monkeypatch.setattr(universal, "_backup_progress", {"phase": "idle"})
    monkeypatch.setattr(universal, "_offsite_sync", lambda dest: {"skipped": "test"})
    monkeypatch.setattr(universal, "_snapshot_to_restic", lambda dest: None)
    monkeypatch.setattr(universal, "_pg_dump_state", lambda: {})
    monkeypatch.setattr(universal, "_rebuilt_dirs", lambda: set())
    monkeypatch.setattr(universal, "_alert_on_backup_gaps", lambda *a, **k: None)
    import kazma_core.backup.neo4j_backup as neo

    monkeypatch.setattr(neo, "export_graph", lambda dest: type("R", (), {"as_dict": lambda self: {}})())
    universal.perform_universal_backup(trigger="test")
    return next(p for p in base.iterdir() if (p / "manifest.json").is_file())


def _restored_from(backup: Path, tmp_path: Path, *, drop_identity: bool = False) -> Path:
    from kazma_core.backup import restore as restore_mod

    data = tmp_path / "restored" / "kazma-data"
    data.mkdir(parents=True)
    restore_mod._flatten_into_data_dir(backup, data)
    if drop_identity:
        with closing(sqlite3.connect(data / "settings.db")) as conn:
            conn.execute("DELETE FROM settings WHERE key LIKE 'identity.%'")
            conn.commit()
    return data


def _vault_in(data: Path) -> Path:
    return data / Path(paths.vault_db_path()).name


def test_a_universal_backup_restores_both_spaces_apart(world, tmp_path, monkeypatch) -> None:
    before = _before(world)
    backup = _universal_backup(tmp_path, monkeypatch)
    data = _restored_from(backup, tmp_path)
    judged = _judge(data, tmp_path / "judge", vault=_vault_in(data),
                    threads=before["threads"][A] + before["threads"][B])
    _assert_apart(judged, before, world, with_secrets=True)


def test_control_a_restore_without_the_identity_store_loses_the_spaces(world, tmp_path, monkeypatch) -> None:
    before = _before(world)
    backup = _universal_backup(tmp_path, monkeypatch)
    data = _restored_from(backup, tmp_path, drop_identity=True)
    judged = _judge(data, tmp_path / "judge", vault=_vault_in(data),
                    threads=before["threads"][A] + before["threads"][B])
    with pytest.raises(AssertionError):
        _assert_apart(judged, before, world, with_secrets=True)
    assert judged["counts"][A] == "no_such_space" and judged["exports"][A] == "no_such_space"


# ── a migration bundle ──────────────────────────────────────────────────


_IMPORT = r"""
import json, sys
from kazma_core.migration.importer import import_bundle
report = import_bundle(sys.argv[1], force=True, server_stopped=True)
print("IMPORTED" + json.dumps({"ok": not report.errors, "errors": report.errors}))
"""


def test_a_migration_carries_the_identity_store_and_keeps_spaces_apart(world, tmp_path, monkeypatch) -> None:
    from kazma_core.migration.exporter import export_bundle

    before = _before(world)
    bundle = tmp_path / "bundle.zip"
    export_bundle(bundle)
    with zipfile.ZipFile(bundle) as zipped:
        rows = json.loads(zipped.read("settings_rows.json"))
    assert any(row["key"] == identity.REGISTRY_KEY for row in rows)
    target = tmp_path / "target" / "kazma-data"
    target.mkdir(parents=True)
    env = _child_env(target)
    done = subprocess.run([sys.executable, "-c", _IMPORT, str(bundle)], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600, check=False,
                          stdin=subprocess.DEVNULL)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("IMPORTED")), "")
    assert line and json.loads(line[len("IMPORTED"):])["ok"], (done.stdout[-3000:], done.stderr[-3000:])
    # The bundle carries the install's vault.db, not this test's own vault:
    # the secrets are checked through the universal backup above.
    judged = _judge(target, tmp_path / "judge", vault=None,
                    threads=before["threads"][A] + before["threads"][B])
    _assert_apart(judged, before, world, with_secrets=False)


# ── the Settings file is the admin's ────────────────────────────────────


def _seed_other_spaces() -> None:
    store = get_config_store()
    store.set(f"tenant.{A}.connectors.x.handle", "a-handle", category="x")
    store.set(f"tenant.{scope.OWNER_SPACE_ID}.connectors.x.handle", "owner-handle", category="x")
    store.set("self_improvement.space_evolution", {"spaces": {A: {"agents": {}}}}, category="self_improvement")


def test_the_settings_file_holds_no_other_space_and_a_restore_writes_none(monkeypatch) -> None:
    from kazma_core import settings_restore

    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    _seed_other_spaces()
    try:
        text = settings_restore.backup_text(store, "json")
        held = json.loads(text)["settings"]
        assert f"tenant.{A}.connectors.x.handle" not in held
        assert "self_improvement.space_evolution" not in held
        assert f"tenant.{scope.OWNER_SPACE_ID}.connectors.x.handle" in held  # the owner's own
        # An older file that holds another space's settings writes none of them.
        older = json.loads(text)
        older["settings"][f"tenant.{A}.connectors.x.handle"] = "older-handle"
        older["settings"][f"tenant.{B}.connectors.x.handle"] = "erased-space-handle"
        plan = settings_restore.restore(json.dumps(older), store, dry_run=True)["plan"]
        assert plan["kept"]["other_spaces"] == 2
        assert not any(key.startswith((f"tenant.{A}.", f"tenant.{B}.")) for key in plan["changed"])
    finally:
        for key in (f"tenant.{A}.connectors.x.handle", f"tenant.{scope.OWNER_SPACE_ID}.connectors.x.handle",
                    "self_improvement.space_evolution"):
            store.delete(key)


def test_control_the_settings_file_of_before_carries_another_space(monkeypatch) -> None:
    from kazma_core import settings_restore

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(settings_restore, "_held_by_another_space", lambda key: False)
    store = get_config_store()
    _seed_other_spaces()
    try:
        held = json.loads(settings_restore.backup_text(store, "json"))["settings"]
        assert f"tenant.{A}.connectors.x.handle" in held
        older = {**json.loads(settings_restore.backup_text(store, "json"))}
        older["settings"][f"tenant.{B}.connectors.x.handle"] = "erased-space-handle"
        plan = settings_restore.restore(json.dumps(older), store, dry_run=True)["plan"]
        assert f"tenant.{B}.connectors.x.handle" in plan["changed"]
    finally:
        for key in (f"tenant.{A}.connectors.x.handle", f"tenant.{scope.OWNER_SPACE_ID}.connectors.x.handle",
                    "self_improvement.space_evolution"):
            store.delete(key)


def test_without_spaces_the_settings_file_is_as_before(monkeypatch) -> None:
    from kazma_core import settings_restore

    store = get_config_store()
    _seed_other_spaces()
    try:
        held = json.loads(settings_restore.backup_text(store, "json"))["settings"]
        assert f"tenant.{A}.connectors.x.handle" in held
    finally:
        for key in (f"tenant.{A}.connectors.x.handle", f"tenant.{scope.OWNER_SPACE_ID}.connectors.x.handle",
                    "self_improvement.space_evolution"):
            store.delete(key)


# ── no plain copy of several people's backup leaves the machine ─────────


def test_the_plain_offsite_copy_is_refused_once_a_second_person_exists(tmp_path, monkeypatch) -> None:
    from kazma_core.backup import universal
    from kazma_core.spaces import people

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(universal, "_offsite_config",
                        lambda: {"enabled": True, "provider": "", "rclone_remote": "", "keep": 7})
    dest = tmp_path / "1700000000"
    dest.mkdir()
    (dest / "manifest.json").write_text("{}", encoding="utf-8")
    try:
        # One account: the plain copy goes on as before (no provider here: skipped).
        assert universal._plain_copy_refused() == ""
        assert universal._offsite_sync(dest) == {"skipped": "no offsite provider configured"}
        people.create_person("backup-person", "a-long-enough-password", by=scope.OWNER_ACCOUNT_ID)
        refused = universal._offsite_sync(dest)
        assert refused == {"ok": False, "error": refused["error"], "refused": "plain_copy"}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)


def test_control_the_plain_copy_check_of_before_sends_it(tmp_path, monkeypatch) -> None:
    from kazma_core.backup import universal
    from kazma_core.spaces import people

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(universal, "_plain_copy_refused", lambda: "")
    monkeypatch.setattr(universal, "_offsite_config",
                        lambda: {"enabled": True, "provider": "", "rclone_remote": "", "keep": 7})
    dest = tmp_path / "1700000000"
    dest.mkdir()
    try:
        people.create_person("backup-person", "a-long-enough-password", by=scope.OWNER_ACCOUNT_ID)
        assert universal._offsite_sync(dest) == {"skipped": "no offsite provider configured"}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
