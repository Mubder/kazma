"""A space's files are its own: no walk of them follows a link or a junction
out of the space's folder (Spaces S1-27, S4-06, S2-08).

``export_space`` and the S4-06 bundle walked the folder with ``rglob`` and
``is_file``, which follow a link to a file (and, on Python 3.11, enter a
Windows junction): a link inside a space's folder put the file it leads to
into the space's export. The backups and the migration's export asked
``is_symlink``, which a junction passes. Nothing a member has today makes such
a link; an owner's copy of a folder can.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from kazma_core.backup import universal
from kazma_core.migration import exporter
from kazma_core.migration.bundle import KazmaBundle, Manifest
from kazma_core.spaces import files as space_files
from kazma_core.spaces import space_data, space_limits, space_move
from kazma_core.spaces.files import FILES, own_files, space_dir

from tests._space_world import A, plant_world

REPO = Path(__file__).resolve().parents[1]
OUTSIDE = b"a file of this computer, outside every space"


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _plant_links(root: Path, outside: Path) -> None:
    """A folder link and, where this account may make one, a file link, in
    *root*, each leading to *outside*."""
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "secret.txt").write_bytes(OUTSIDE)
    root.mkdir(parents=True, exist_ok=True)
    _junction(root / "elsewhere", outside)
    try:
        os.symlink(outside / "secret.txt", root / "linked.txt")
    except OSError:  # Windows without the right to make one: the junction is the case
        assert sys.platform == "win32"


@pytest.fixture
def world(monkeypatch, tmp_path):
    yield from plant_world(monkeypatch, tmp_path)


def _rglob_files(folder: Path, *, unreadable: list[Path] | None = None):
    """The walk as it was, entering what it leads to (a junction on 3.11)."""
    for base, _dirs, names in os.walk(folder, followlinks=True):
        for name in sorted(names):
            path = Path(base) / name
            if path.is_file():
                yield path


# ── what each walk leaves alone ─────────────────────────────────────────


def test_an_export_never_follows_a_link_out_of_the_space(world, tmp_path) -> None:
    before = space_data.count_space(A)["files"]
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    target = tmp_path / "a.zip"
    space_data.export_space(A, target)
    with zipfile.ZipFile(target) as zipped:
        assert all(OUTSIDE not in zipped.read(name) for name in zipped.namelist()), "a linked file was exported"
    assert space_data.count_space(A)["files"] == before


def test_control_the_walk_as_it_was_exports_the_linked_file(world, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(space_files, "own_files", _rglob_files)
    with pytest.raises(AssertionError, match="a linked file was exported"):
        test_an_export_never_follows_a_link_out_of_the_space(world, tmp_path)


def _bundled(space: str) -> bytes:
    return b"".join(path.read_bytes() for _part, _where, path in space_move._moving_files(space))


def test_a_bundle_never_follows_a_link_out_of_the_space(world, tmp_path) -> None:
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    assert OUTSIDE not in _bundled(A), "a linked file travels"


def test_control_the_walk_as_it_was_bundles_the_linked_file(world, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(space_files, "own_files", _rglob_files)
    with pytest.raises(AssertionError, match="a linked file travels"):
        test_a_bundle_never_follows_a_link_out_of_the_space(world, tmp_path)


def test_a_linked_file_is_no_file_of_the_spaces_size(world, tmp_path) -> None:
    before = space_limits._folder_bytes(space_dir(A))
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    assert space_limits._folder_bytes(space_dir(A)) == before


def test_the_walk_finds_every_own_file_and_says_what_it_could_not_read(tmp_path, monkeypatch) -> None:
    root = tmp_path / "own"
    (root / "b" / "c").mkdir(parents=True)
    for rel in ("a.txt", "b/b.txt", "b/c/c.txt", "ملف.txt"):
        (root / rel).write_bytes(b"x")
    assert sorted(p.relative_to(root).as_posix() for p in own_files(root)) == [
        "a.txt", "b/b.txt", "b/c/c.txt", "ملف.txt"]
    real = os.scandir

    def refuses(path):
        if Path(path).name == "c":
            raise PermissionError("not yours")
        return real(path)

    monkeypatch.setattr(os, "scandir", refuses)
    with pytest.raises(PermissionError):
        list(own_files(root))
    missed: list[Path] = []
    assert len(list(own_files(root, unreadable=missed))) == 3
    assert [p.name for p in missed] == ["c"]


# ── the copies of the whole data folder ──────────────────────────────────


def _linked_data(tmp_path: Path) -> Path:
    """A data folder with a space's file, an empty folder of its own, and
    links in it that lead out."""
    data = tmp_path / "kazma-data"
    own = data / "spaces" / "s0123456789ab" / FILES
    (own / "empty").mkdir(parents=True)
    (own / "mine.txt").write_bytes(b"mine")
    _plant_links(own, tmp_path / "outside")
    with sqlite3.connect(tmp_path / "outside" / "other.db") as conn:
        conn.execute("CREATE TABLE t (x)")
    conn.close()
    return data


def _all_bytes(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in own_files(folder))


def test_a_backup_never_follows_a_link_out_of_a_space(tmp_path) -> None:
    data = _linked_data(tmp_path)
    failures: list[dict[str, str]] = []
    copied = universal._robust_copytree(data / "spaces", tmp_path / "out", failures)
    assert OUTSIDE not in _all_bytes(tmp_path / "out"), "a linked file is in the backup"
    assert (copied, failures) == (1, [])
    assert (tmp_path / "out" / "s0123456789ab" / FILES / "empty").is_dir()
    assert universal.selectable_databases(data) == [], "a database of another place is in the backup"


def test_a_migration_bundle_never_follows_a_link_out_of_a_space(tmp_path) -> None:
    data = _linked_data(tmp_path)
    failures: list[str] = []
    exporter._copy_carried(data / "spaces", tmp_path / "out", "spaces", failures, tmp_path / "x.zip", [])
    assert OUTSIDE not in _all_bytes(tmp_path / "out"), "a linked file is in the bundle"
    assert failures == [] and (tmp_path / "out" / "s0123456789ab" / FILES / "mine.txt").is_file()
    assert (tmp_path / "out" / "s0123456789ab" / FILES / "empty").is_dir()


def test_the_backup_script_never_follows_a_link_out_of_a_space(tmp_path) -> None:
    data = _linked_data(tmp_path)
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "backup_kazma.py"), "--data-dir", str(data),
                           "--dest", str(tmp_path / "to")], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    with zipfile.ZipFile(next((tmp_path / "to").glob("*.zip"))) as zipped:
        names = zipped.namelist()
        assert all(OUTSIDE not in zipped.read(name) for name in names), "a linked file is in the archive"
    assert any(name.endswith("mine.txt") for name in names)


def test_a_folder_of_the_data_folder_may_be_on_another_disk(tmp_path) -> None:
    """An install's own layout: ``document-store`` is a junction. Its
    databases are still taken, and nothing below it is followed."""
    data = _linked_data(tmp_path)
    elsewhere = tmp_path / "other-disk"
    elsewhere.mkdir()
    with sqlite3.connect(elsewhere / "documents.db") as conn:
        conn.execute("CREATE TABLE t (x)")
    conn.close()
    _plant_links(elsewhere, tmp_path / "outside")
    _junction(data / "document-store", elsewhere)
    found = universal.selectable_databases(data)
    assert [p.relative_to(data).as_posix() for p in found] == ["document-store/documents.db"]


# ── a link directly in the data folder is taken, one below is named ───────


def _file_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("this account makes no link to a file (Windows)")


def _other_disk(tmp_path: Path) -> Path:
    """A data folder whose memory database, notes and document store are on
    another disk, by links directly in it."""
    data, other = tmp_path / "kazma-data", tmp_path / "other-disk"
    (other / "store").mkdir(parents=True)
    data.mkdir()
    for place in (other / "memory_state.db", data / "settings.db", other / "store" / "documents.db"):
        with sqlite3.connect(place) as conn:
            conn.execute("CREATE TABLE t (x)")
        conn.close()
    (other / "notes.txt").write_bytes(b"the operator's notes")
    (other / "store" / "blob.bin").write_bytes(b"a blob")
    _file_link(data / "memory_state.db", other / "memory_state.db")
    _file_link(data / "notes.txt", other / "notes.txt")
    _junction(data / "document-store", other / "store")
    return data


def test_a_database_that_is_a_link_in_the_data_folder_stays_in_every_backup(tmp_path) -> None:
    """The old list against the new one: ``rglob`` listed a linked database,
    the one walk does not, and nothing said it was gone."""
    data = _other_disk(tmp_path)
    old = {p.relative_to(data).as_posix() for p in data.rglob("*.db")}
    new = {p.relative_to(data).as_posix() for p in universal.selectable_databases(data)}
    assert old <= new and "memory_state.db" in new, old - new
    assert universal._backup_one_db(data / "memory_state.db", tmp_path / "copy.db")
    copied = {row["path"]: row["type"] for row in universal._copy_assets(data, _made(tmp_path / "assets"))}
    assert copied == {"document-store": "dir", "notes.txt": "file"}
    assert (tmp_path / "assets" / "notes.txt").read_bytes() == b"the operator's notes"
    assert (tmp_path / "assets" / "document-store" / "blob.bin").is_file()


def _made(folder: Path) -> Path:
    folder.mkdir(parents=True)
    return folder


def test_the_script_and_the_bundle_take_what_a_link_in_the_data_folder_leads_to(tmp_path) -> None:
    data = _other_disk(tmp_path)
    done = _script(data, tmp_path / "to")
    assert done.returncode == 0, done.stdout + done.stderr
    with zipfile.ZipFile(next((tmp_path / "to").glob("*.zip"))) as zipped:
        names = set(zipped.namelist())
    assert {f"kazma-data/{n}" for n in ("memory_state.db", "settings.db", "notes.txt", "document-store/blob.bin",
                                        "document-store/documents.db")} <= names
    manifest = Manifest()
    exporter._export_data_dir_files(data, tmp_path / "bundle", manifest, print, lean=False, skip=tmp_path / "x.zip")
    assert (tmp_path / "bundle" / "notes.txt").read_bytes() == b"the operator's notes"
    assert manifest.links_left_out == [] and not (tmp_path / "bundle" / "notes.txt").is_symlink()


def test_a_database_whose_disk_is_away_is_a_failed_database(tmp_path) -> None:
    data = _other_disk(tmp_path)
    (tmp_path / "other-disk" / "memory_state.db").unlink()
    assert data / "memory_state.db" in universal.selectable_databases(data)
    assert not universal._backup_one_db(data / "memory_state.db", tmp_path / "copy.db")
    assert not (tmp_path / "other-disk" / "memory_state.db").exists(), "an empty database was made where the link leads"


def _script(data: Path, to: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "scripts" / "backup_kazma.py"), "--data-dir", str(data),
                           "--dest", str(to)], capture_output=True, text=True, timeout=120, check=False)


def _linked_below(tmp_path: Path) -> Path:
    """Links below the data folder: a space's whole folder, the uploads'
    originals and a project are on another disk."""
    data = tmp_path / "kazma-data"
    for rel in ("spaces/s0123456789ab", "attachments/originals", "workspace/proj"):
        there = tmp_path / "other-disk" / rel.replace("/", "-")
        there.mkdir(parents=True)
        (there / "words.txt").write_bytes(b"words")
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        _junction(data / rel, there)
    (data / "workspace" / "here").mkdir()
    _junction(data / "workspace" / "again", data / "workspace" / "here")  # it leads to what the backup holds
    (data / "attachments" / "a.txt").write_bytes(b"a")
    return data


OUT_LINKS = ["attachments/originals", "spaces/s0123456789ab", "workspace/proj"]


@pytest.fixture
def backups(monkeypatch, tmp_path):
    """``run()`` makes a universal backup of the linked data folder: its
    manifest, and the alerts sent."""
    data = _linked_below(tmp_path)
    base = data / "backups" / "universal"
    base.mkdir(parents=True)
    for name, value in (("_data_dir", lambda: data), ("_universal_dir", lambda: base),
                        ("_offsite_sync", lambda dest: {"skipped": "test"}), ("_snapshot_to_restic", lambda dest: None),
                        ("_pg_dump_state", dict), ("_rebuilt_dirs", set),
                        ("_copy_root_artifacts", lambda dest: {"artifacts": []})):
        monkeypatch.setattr(universal, name, value)
    import kazma_core.backup.neo4j_backup as neo
    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(neo, "export_graph", lambda dest: type("R", (), {"as_dict": lambda self: {}})())
    sent: list[tuple[str, str, str]] = []
    quiet: list[bool] = []

    def alert(key, title, detail="", **_k):
        if not quiet:
            sent.append((key, title, detail))
        return not quiet

    monkeypatch.setattr(ops_alerts, "alert", alert)
    clock = [1_800_000_000]

    def run():
        clock[0] += 60
        monkeypatch.setattr(universal, "_backup_progress", {"phase": "idle"})
        monkeypatch.setattr(universal.time, "time", lambda: float(clock[0]))
        sent.clear()
        summary = universal.perform_universal_backup(retention=5, trigger="manual")
        manifest = json.loads((Path(summary["backup_dir"]) / "manifest.json").read_text(encoding="utf-8"))
        return manifest, summary, list(sent)

    run.data, run.quiet = data, quiet
    return run


def test_every_link_a_backup_leaves_out_is_named(backups, caplog) -> None:
    with caplog.at_level("WARNING", logger="kazma_core.backup.universal"):
        manifest, summary, sent = backups()
    row = manifest["links_left_out"]
    assert sorted(row["items"]) == sorted([*OUT_LINKS, "workspace/again"]) and row["count"] == 4
    assert sorted(row["leading_out"]) == OUT_LINKS and row["paged"] == row["leading_out"], "a link to what the backup holds is no gap"
    assert "links_left_out" not in json.dumps(manifest["assets"])
    said = [r.getMessage() for r in caplog.records if "left out" in r.getMessage()]
    assert len(said) == 1 and all(link in said[0] for link in row["items"]), said
    assert [key for key, _t, _d in sent] == ["backup.files_failed"]
    assert "3 link(s)" in sent[0][1] and "spaces/s0123456789ab" in sent[0][1], sent
    assert summary["complete"] is True, "a link left out is no failed copy"


def test_a_link_is_paged_once_and_named_every_time(backups, caplog, tmp_path) -> None:
    backups()
    with caplog.at_level("WARNING", logger="kazma_core.backup.universal"):
        manifest, _summary, sent = backups()
    assert sent == [] and sorted(manifest["links_left_out"]["leading_out"]) == OUT_LINKS
    assert [r for r in caplog.records if "left out" in r.getMessage()]
    _junction(backups.data / "workspace" / "later", tmp_path / "other-disk")
    _manifest, _summary, sent = backups()
    assert len(sent) == 1 and "workspace/later" in sent[0][1] and "attachments" not in sent[0][1]


def test_a_link_whose_page_was_not_sent_is_paged_at_the_next_backup(backups) -> None:
    """Inside the alert's quiet period the page is not sent."""
    backups.quiet.append(True)
    manifest, _summary, sent = backups()
    assert sent == [] and manifest["links_left_out"]["paged"] == {}
    backups.quiet.clear()
    _manifest, _summary, sent = backups()
    assert len(sent) == 1 and "3 link(s)" in sent[0][1], "a link inside the quiet period is never paged"


def test_a_link_that_leads_elsewhere_now_is_paged_again(backups, tmp_path) -> None:
    backups()
    link, there = backups.data / "workspace" / "proj", tmp_path / "other-disk" / "moved"
    there.mkdir()
    _unlink_link(link)
    _junction(link, there)
    manifest, _summary, sent = backups()
    assert len(sent) == 1 and "1 link(s)" in sent[0][1] and "workspace/proj" in sent[0][1]
    assert manifest["links_left_out"]["paged"]["workspace/proj"].endswith("moved")
    assert backups()[2] == []


def test_more_links_than_a_manifest_names_are_each_paged_once(backups, monkeypatch) -> None:
    from kazma_core.backup import data_files

    monkeypatch.setattr(data_files, "NAMED", 1)
    manifest, _summary, sent = backups()
    assert len(manifest["links_left_out"]["items"]) == 1 and manifest["links_left_out"]["count"] == 4
    assert len(sent) == 1 and backups()[2] == []


def _unlink_link(link: Path) -> None:
    if sys.platform == "win32":
        os.rmdir(link)
    else:
        link.unlink()


def test_the_script_makes_no_database_where_a_dangling_link_leads(tmp_path) -> None:
    data = _other_disk(tmp_path)
    (tmp_path / "other-disk" / "memory_state.db").unlink()
    done = _script(data, tmp_path / "to")
    assert done.returncode == 1 and "memory_state.db" in done.stderr and "incomplete" in done.stderr, done.stderr
    assert not (tmp_path / "other-disk" / "memory_state.db").exists(), "an empty database was made where the link leads"
    with zipfile.ZipFile(next((tmp_path / "to").glob("*.zip"))) as zipped:
        names = zipped.namelist()
        manifest = json.loads(zipped.read("MANIFEST.json"))
    assert "kazma-data/memory_state.db" not in names and "kazma-data/settings.db" in names
    assert manifest["databases_missing"] == ["memory_state.db"] and "memory_state.db" in manifest["links_left_out"]


def test_no_copy_of_a_database_is_made_from_a_dangling_link(tmp_path) -> None:
    """Every copier is the one helper (``tests/test_sqlite_snapshot.py``)."""
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    _file_link(tmp_path / "a.db", tmp_path / "away" / "a.db")
    (tmp_path / "away").mkdir()
    with pytest.raises(FileNotFoundError):
        snapshot_copy(tmp_path / "a.db", tmp_path / "copy.db")
    assert not (tmp_path / "away" / "a.db").exists() and not (tmp_path / "copy.db").exists()


def test_a_bundle_names_a_database_whose_disk_is_away(tmp_path, caplog) -> None:
    """The whole export: its check refuses the bundle, and says which."""
    from kazma_core import paths

    data = paths.data_dir()
    held = [data / "swarm_tasks.db", data / "knowledge.db"]
    for place in held:
        if place.exists():
            pytest.skip("this test's data folder holds the store")
        _file_link(place, tmp_path / "away" / place.name)
    try:
        with caplog.at_level("WARNING", logger="kazma_core.migration.exporter"):
            made = exporter.export_bundle(tmp_path / "x.zip")
        bundle = KazmaBundle(made)
        assert {"swarm_tasks.db", "knowledge.db"} <= set(bundle.manifest.links_left_out)
        report = bundle.verify(check_hashes=False)
        assert not report.ok and any("swarm_tasks.db" in error for error in report.errors), report.errors
        assert any("swarm_tasks.db" in r.getMessage() for r in caplog.records)
    finally:
        for place in held:
            place.unlink(missing_ok=True)


def test_a_failed_file_and_a_link_are_one_alert(backups, monkeypatch) -> None:
    real = universal.shutil.copy2

    def locked(src, dst, *args, **kwargs):
        if Path(src).name == "a.txt":
            raise PermissionError(13, "held", str(src))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(universal.shutil, "copy2", locked)
    _manifest, summary, sent = backups()
    assert len(sent) == 1 and "1 file(s)" in sent[0][1] and "3 link(s)" in sent[0][1] and summary["complete"] is False


def test_the_script_and_the_bundle_name_every_link_they_leave_out(tmp_path, caplog) -> None:
    data = _linked_below(tmp_path)
    done = _script(data, tmp_path / "to")
    assert done.returncode == 0, done.stdout + done.stderr
    with zipfile.ZipFile(next((tmp_path / "to").glob("*.zip"))) as zipped:
        named = json.loads(zipped.read("MANIFEST.json"))["links_left_out"]
    assert sorted(named) == sorted([*OUT_LINKS, "workspace/again"])
    assert all(link in done.stderr for link in named), done.stderr
    manifest, said = Manifest(), []
    _junction(data / "whole", tmp_path)  # it leads to the folder that holds the data folder
    with caplog.at_level("WARNING", logger="kazma_core.migration.exporter"):
        exporter._export_data_dir_files(data, tmp_path / "bundle", manifest, said.append, lean=False,
                                        skip=tmp_path / "x.zip")
    assert sorted(manifest.links_left_out) == sorted([*OUT_LINKS, "workspace/again", "whole"])
    assert any("left out" in line for line in said)
    assert len([r for r in caplog.records if "left out" in r.getMessage()]) == 1
    again = Manifest.from_json(manifest.to_json())
    assert again.links_left_out == manifest.links_left_out


def test_the_bundles_check_says_which_links_are_not_in_it(tmp_path) -> None:
    manifest = Manifest(links_left_out=["workspace/proj"])
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr("manifest.json", manifest.to_json())
    report = KazmaBundle(path).verify(check_hashes=False)
    assert any("workspace/proj" in warning for warning in report.warnings), report.warnings


# ── the gate: one walk of a space's files ────────────────────────────────

#: What a module names when a space's folder, or the data folder that holds
#: them all, is in its hands.
_PLACES = frozenset({"space_dir", "space_folder", "sandboxed_files_root", "host_space_root", "_spaces_root",
                     "data_dir", "get_document_storage_root", "SPACES_DIR"})
_WALKS = frozenset({"rglob", "walk", "iterdir", "copytree", "scandir", "listdir", "fwalk"})

#: The walks in those modules that enter no space's folder, by why.
REVIEWED: dict[str, frozenset[str]] = {
    "a folder of backups or bundles Kazma made, or one being restored": frozenset({
        "kazma-core/kazma_core/backup/restic_repo.py::restore",
        "kazma-core/kazma_core/backup/restore.py::_find_backup_root",
        "kazma-core/kazma_core/backup/restore.py::_flatten_into_data_dir",
        "kazma-core/kazma_core/backup/restore.py::_next_steps",
        "kazma-core/kazma_core/backup/restore.py::restore_files",
        "kazma-core/kazma_core/backup/universal.py::_links_paged_before",
        "kazma-core/kazma_core/backup/universal.py::_offsite_files",
        "kazma-core/kazma_core/backup/universal.py::_offsite_rclone_excludes",
        "kazma-core/kazma_core/backup/universal.py::archive_universal_backup",
        "kazma-core/kazma_core/backup/universal.py::list_universal_backups",
        "kazma-core/kazma_core/backup/universal.py::perform_universal_backup",
        "kazma-core/kazma_core/backup/wal_archive.py::_base_backups",
        "kazma-core/kazma_core/backup/wal_archive.py::_push_offsite",
        "kazma-core/kazma_core/backup/wal_archive.py::prune",
        "kazma-core/kazma_core/backup/wal_archive.py::restore_to",
        "kazma-core/kazma_core/migration/exporter.py::_finish_bundle",
        "kazma-core/kazma_core/migration/importer.py::_restore_data_dir_files",
        "kazma-core/kazma_core/migration/importer.py::_restore_document_store",
        "scripts/restore_kazma.py::main",
    }),
    "the data folder's own names, one level, and no folder of it is entered here": frozenset({
        "kazma-core/kazma_core/backup/universal.py::_copy_assets",
        "kazma-core/kazma_core/migration/exporter.py::_export_data_dir_files",
        "kazma-core/kazma_core/migration/exporter.py::export_bundle",
        "kazma-core/kazma_core/migration/importer.py::_dest_map_for",
        "kazma-core/kazma_core/spaces/space_data.py::store_paths",
        "kazma-core/kazma_core/store_registry.py::install_secret_files",
        "kazma-core/kazma_core/store_registry.py::kazma_files_in",
        "kazma-core/kazma_core/store_registry.py::kazma_paths_inside",
    }),
    "looks for Kazma's own files, to refuse them; it leaves every link unentered": frozenset({
        "kazma-core/kazma_core/store_registry.py::kazma_file_under",
    }),
    "the document store's garbage: what it deletes is checked by its real path, inside the store": frozenset({
        "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector._iter_files",
        "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector._space_dirs",
        "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector._sweep_manifests",
        "kazma-core/kazma_core/documents/retention.py::DocumentGarbageCollector._sweep_scratch",
    }),
    "another folder: a database's, the engine's, a package's, Kazma's own code": frozenset({
        "kazma-cli/kazma_cli/setup.py::_holds_files",
        "kazma-cli/kazma_cli/update.py::_cleanup_broken_distributions",
        "kazma-cli/kazma_cli/update.py::_vector_memory_data_present",
        "kazma-core/kazma_core/data_folder.py::kept_cluster_major",
        "kazma-core/kazma_core/data_folder_move.py::_copy_tree",
        "kazma-core/kazma_core/data_folder_move.py::_holds_files",
        "kazma-core/kazma_core/data_folder_move.py::_links_inside",
        "kazma-core/kazma_core/data_folder_move.py::_manifest",
        "kazma-core/kazma_core/data_folder_move.py::_size",
        "kazma-core/kazma_core/observability/watch_setup.py::WatchSetup._write_worker",
        "kazma-core/kazma_core/paths.py::migrate_legacy_user_home",
        "kazma-core/kazma_core/security/certification.py::KazmaCertification._has_test_files",
        "kazma-core/kazma_core/stores/knowledge_ingest.py::_check_playwright_available",
        "kazma-core/kazma_core/stores/knowledge_ingest.py::_save_provenance",
        "kazma-ui/kazma_ui/app.py::KazmaAppBuilder._setup_templates_and_middlewares._js_version",
        "kazma-ui/kazma_ui/routes_direct/system.py::register_system_routes._get_system_status",
    }),
    "the admin's folder picker: the names of one folder": frozenset({
        "kazma-core/kazma_core/backup/folder_copy.py::browse",
    }),
    "a file move: one folder's spelling of a name, and the holding folder's own names": frozenset({
        "kazma-core/kazma_core/spaces/file_moves.py::_spelled",
        "kazma-core/kazma_core/spaces/file_moves.py::_attempts",
    }),
}


def _is_walk(node: ast.AST) -> bool:
    """Whether *node* walks a folder: a call of one of :data:`_WALKS`, a glob
    that enters folders or whose pattern is not written here, or one of them
    brought in by its bare name."""
    if isinstance(node, ast.ImportFrom):
        return node.module in ("os", "glob", "shutil") and any(a.name in _WALKS | {"glob", "iglob"} for a in node.names)
    if isinstance(node, ast.Attribute):
        return node.attr in _WALKS and not (isinstance(node.value, ast.Name) and node.value.id == "ast")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("glob", "iglob"):
        if any(k.arg == "recursive" for k in node.keywords):
            return True
        texts = [n.value for a in node.args for n in ast.walk(a)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        return not texts or any("**" in text for text in texts)
    return False


def _walks(text: str) -> dict[str, list[int]]:
    """``{function: lines}`` of what walks a folder in *text*."""
    found: dict[str, list[int]] = {}

    def look(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            if _is_walk(child):
                found.setdefault(inside, []).append(child.lineno)
            named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            look(child, f"{inside}.{child.name}".lstrip(".") if named else inside)

    look(ast.parse(text), "")
    return found


def _names(text: str) -> set[str]:
    tree = ast.parse(text)
    return ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            | {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names})


def _walks_of_a_spaces_files(rel: str, text: str) -> list[str]:
    """``file::function`` of each walk in a module that holds a space's
    folder or the data folder, the spaces package in any case."""
    if not (rel.startswith("kazma-core/kazma_core/spaces/") or _names(text) & _PLACES):
        return []
    return [f"{rel}::{name}" for name in _walks(text)]


def _product() -> list[str]:
    """The product's modules. A soak and a shadow run make an install of
    their own and read that; no person's files are in it."""
    listed = subprocess.run(["git", "ls-files", "kazma-*/*.py", "scripts/*.py"], cwd=REPO, check=True,
                            capture_output=True, text=True).stdout.split()
    return [f for f in listed if "/tests/" not in f and not f.endswith(("_soak.py", "x_shadow.py"))]


def test_every_walk_of_a_spaces_files_asks_own_files() -> None:
    files = _product()
    assert len(files) > 500, "the product was not found"
    found = {walk for rel in files
             for walk in _walks_of_a_spaces_files(rel, (REPO / rel).read_text(encoding="utf-8"))}
    reviewed = frozenset().union(*REVIEWED.values())
    assert found - reviewed == set(), "walk with real_paths.own_files, or say here why this enters no space's folder"
    assert reviewed - found == set(), "a reviewed walk is gone: take its line away"


@pytest.mark.parametrize("walk", [
    "for path in folder.rglob('*'):\n    pass",
    "for base in os.walk(folder):\n    pass",
    "for path in folder.glob('**/*'):\n    pass",
    "for path in folder.glob(pattern):\n    pass",
    "for path in glob.iglob('x', recursive=True):\n    pass",
    "for name in os.listdir(folder):\n    pass",
    "for entry in os.scandir(folder):\n    pass",
    "from os import walk",
    "from os import scandir as look",
    "for item in folder.iterdir():\n    pass",
    "shutil.copytree(folder, there)",
])
def test_control_a_walk_of_its_own_is_caught(walk) -> None:
    for rel in ("kazma-core/kazma_core/spaces/new.py", "kazma-ui/kazma_ui/new.py"):
        assert _walks_of_a_spaces_files(rel, f"folder = space_dir(space)\n{walk}\n") == [f"{rel}::"]
    assert _walks_of_a_spaces_files("kazma-ui/kazma_ui/new.py", walk + "\n") == []


def test_control_the_names_of_one_pattern_are_no_walk() -> None:
    assert _walks_of_a_spaces_files("kazma-ui/kazma_ui/new.py", "space_dir(s).glob('att_*.json')\n") == []
