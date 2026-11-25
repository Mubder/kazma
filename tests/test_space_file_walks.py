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
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from kazma_core.backup import universal
from kazma_core.migration import exporter
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
    exporter._copy_carried(data / "spaces", tmp_path / "out", "spaces", failures, tmp_path / "x.zip")
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
