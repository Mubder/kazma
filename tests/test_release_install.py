"""A release install (the Windows installer's) is updated from the release's
signed source archive (kazma_cli.release_tree, kazma_cli.update).

The installer lays down Kazma's source tree with no git. ``kazma update``
there replaces the files the new release ships, removes those it no longer
ships, never touches the owner's settings, data, environment or own files, and
puts everything back when the install after it fails or is refused. An
archive with anything but files under ``kazma-<version>/`` is refused.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from kazma_cli import release_tree, update
from kazma_cli.release_tree import MARKER, ArchiveRefused


def _archive(tmp: Path, version: str, files: dict[str, str], *, extra=None) -> Path:
    """A source archive ``kazma-<version>.tar.gz`` holding *files* under its folder."""
    path = tmp / f"kazma-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for rel, text in {"pyproject.toml": f'version = "{version}"\n', **files}.items():
            data = text.encode()
            info = tarfile.TarInfo(f"kazma-{version}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra or []:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _install(root: Path, version: str, files: dict[str, str]) -> None:
    """A release install of *version* holding *files*, as the installer leaves it."""
    files = {"pyproject.toml": f'version = "{version}"\n', **files}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    release_tree.write_marker(root, version, list(files))
    # The owner's own things.
    (root / ".env").write_text("KAZMA_SECRET=keep-me\n", encoding="utf-8")
    (root / "kazma-data").mkdir()
    (root / "kazma-data" / "memory.db").write_bytes(b"data")
    (root / "notes.txt").write_text("mine", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("env", encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".kazma" not in p.relative_to(root).parts}


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "Kazma"
    root.mkdir()
    _install(root, "1.0.0", {"kazma_cli/a.py": "old a\n", "kazma_cli/gone.py": "gone\n", "kazma.yaml": "old: 1\n"})
    return root


def test_an_update_replaces_adds_and_removes_shipped_files_and_keeps_the_owners(installed, tmp_path):
    archive = _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "new a\n", "kazma_cli/new.py": "new\n",
                                           "kazma.yaml": "new: 1\n"})
    applied = release_tree.apply_archive(installed, archive, "1.1.0")

    assert (installed / "kazma_cli" / "a.py").read_text() == "new a\n"
    assert (installed / "kazma_cli" / "new.py").read_text() == "new\n"
    assert not (installed / "kazma_cli" / "gone.py").exists()
    assert (installed / ".env").read_text() == "KAZMA_SECRET=keep-me\n"
    assert (installed / "kazma-data" / "memory.db").read_bytes() == b"data"
    assert (installed / "notes.txt").read_text() == "mine"
    assert (installed / ".venv" / "marker").read_text() == "env"
    marker = release_tree.read_marker(installed)
    assert marker["version"] == "1.1.0" and "kazma_cli/new.py" in marker["files"]
    assert set(applied.removed) == {"kazma_cli/gone.py"}
    assert (applied.backup / "files" / "kazma_cli" / "a.py").read_text() == "old a\n"


def test_a_rollback_puts_the_tree_back_exactly(installed, tmp_path):
    before = _snapshot(installed)
    marker_before = (installed / MARKER).read_text()
    archive = _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "new a\n", "kazma_cli/new.py": "new\n"})
    applied = release_tree.apply_archive(installed, archive, "1.1.0")
    release_tree.rollback(applied)
    assert _snapshot(installed) == before
    assert (installed / MARKER).read_text() == marker_before


def test_a_failure_half_way_leaves_the_tree_as_it_was(installed, tmp_path, monkeypatch):
    before = _snapshot(installed)
    archive = _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "new a\n", "kazma_cli/b.py": "b\n",
                                           "kazma_cli/c.py": "c\n"})
    real_move = release_tree._move
    calls = {"n": 0}

    def failing_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("disk full")
        real_move(src, dst)

    monkeypatch.setattr(release_tree, "_move", failing_move)
    with pytest.raises(OSError):
        release_tree.apply_archive(installed, archive, "1.1.0")
    monkeypatch.setattr(release_tree, "_move", real_move)
    assert _snapshot(installed) == before
    assert release_tree.read_marker(installed)["version"] == "1.0.0"


def _member(name: str, *, kind=tarfile.REGTYPE, link: str = "", data: bytes = b"x"):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, (data if kind == tarfile.REGTYPE else None)


@pytest.mark.parametrize("extra", [
    [_member("kazma-1.1.0/../../evil.py")],
    [_member("other-1.1.0/evil.py")],
    [_member("kazma-1.1.0/link.py", kind=tarfile.SYMTYPE, link="/etc/passwd")],
    [_member("kazma-1.1.0/hard.py", kind=tarfile.LNKTYPE, link="kazma-1.1.0/pyproject.toml")],
    [_member("kazma-1.1.0/.env")],
    [_member("kazma-1.1.0/kazma-data/memory.db")],
    [_member("kazma-1.1.0/.venv/Scripts/python.exe")],
    [_member(f"kazma-1.1.0/{MARKER}")],
], ids=["dotdot", "other-folder", "symlink", "hardlink", "env", "data", "venv", "marker"])
def test_an_archive_with_anything_but_shipped_files_is_refused(installed, tmp_path, extra):
    before = _snapshot(installed)
    archive = _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "new\n"}, extra=extra)
    with pytest.raises(ArchiveRefused):
        release_tree.apply_archive(installed, archive, "1.1.0")
    assert _snapshot(installed) == before


def test_an_archive_without_pyproject_or_of_another_version_is_refused(installed, tmp_path):
    no_pyproject = tmp_path / "np.tar.gz"
    with tarfile.open(no_pyproject, "w:gz") as tar:
        info = tarfile.TarInfo("kazma-1.1.0/a.py")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"a"))
    with pytest.raises(ArchiveRefused):
        release_tree.shipped_files(no_pyproject, "1.1.0")
    with pytest.raises(ArchiveRefused):  # the 1.1.0 archive offered as 1.2.0
        release_tree.shipped_files(_archive(tmp_path, "1.1.0", {}), "1.2.0")


def test_control_without_the_kept_rule_an_archive_could_replace_env(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(release_tree, "KEPT", frozenset())
    archive = _archive(tmp_path, "1.1.0", {".env": "KAZMA_SECRET=attacker\n"})
    release_tree.apply_archive(installed, archive, "1.1.0")
    assert (installed / ".env").read_text() == "KAZMA_SECRET=attacker\n"


def test_backups_keep_only_the_newest(installed, tmp_path):
    first = release_tree.apply_archive(installed, _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "1\n"}), "1.1.0")
    (tmp_path / "2").mkdir()
    second = release_tree.apply_archive(installed, _archive(tmp_path / "2", "1.2.0", {"kazma_cli/a.py": "2\n"}),
                                        "1.2.0")
    release_tree.prune_backups(installed, keep=second.backup)
    assert second.backup.is_dir() and not first.backup.exists()


# -- the release's source archive, judged as the wheel is ----------------------

PREFIX = update._RELEASE_DOWNLOAD_PREFIX
SHA = "a" * 64


def _assets(*, sha=SHA, digest=SHA, bundle=True, name="kazma-1.1.0.tar.gz"):
    assets = [{"name": name, "browser_download_url": f"{PREFIX}v1.1.0/{name}", "digest": f"sha256:{digest}"}]
    if bundle:
        assets.append({"name": f"{name}.sigstore.json",
                       "browser_download_url": f"{PREFIX}v1.1.0/{name}.sigstore.json"})
    return assets, {"kazma-1.1.0.tar.gz": sha}


def test_the_source_archive_is_taken_when_listed_matching_and_signed():
    assets, listed = _assets()
    found = update._source_archive(assets, "1.1.0", listed)
    assert found["source_sha256"] == SHA and found["source_bundle_url"].endswith(".sigstore.json")
    assert "source_problem" not in found


@pytest.mark.parametrize("change,problem", [
    (dict(sha=""), "SHA256SUMS does not list"),
    (dict(digest="b" * 64), "disagree"),
    (dict(bundle=False), "no Sigstore signature"),
    (dict(name="kazma-1.0.0.tar.gz"), "no source archive"),
])
def test_the_source_archive_is_refused_as_a_wheel_would_be(change, problem):
    assets, listed = _assets(**change)
    found = update._source_archive(assets, "1.1.0", listed)
    assert problem in found["source_problem"]


# -- the update itself --------------------------------------------------------

@pytest.fixture
def release_update(installed, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "1.1.0", {"kazma_cli/a.py": "new a\n", "kazma_cli/new.py": "new\n"})
    release = update.ReleaseInfo(version="1.1.0", page="https://example.test/r", source_name=archive.name,
                                 source_url=f"{PREFIX}v1.1.0/{archive.name}", source_sha256=SHA)
    monkeypatch.setattr(update, "_download_source", lambda rel, folder: archive)
    installs: list[str] = []

    def held(outcomes):
        def _held(command, install):
            installs.append(command)
            return outcomes.pop(0)
        monkeypatch.setattr(update, "_install_held", _held)

    return installed, release, held, installs


def test_a_successful_update_keeps_the_new_tree(release_update):
    root, release, held, installs = release_update
    held(["done"])
    assert update.do_release_update(release, root, "kazma update -y") is True
    assert (root / "kazma_cli" / "a.py").read_text() == "new a\n"
    assert release_tree.read_marker(root)["version"] == "1.1.0"
    assert installs == ["kazma update -y"]


def test_a_failed_install_puts_the_tree_and_its_packages_back(release_update):
    root, release, held, installs = release_update
    before = _snapshot(root)
    held(["failed", "done"])
    assert update.do_release_update(release, root, "kazma update -y") is False
    assert _snapshot(root) == before
    assert release_tree.read_marker(root)["version"] == "1.0.0"
    assert len(installs) == 2  # the old tree's packages installed again


def test_a_refused_install_puts_the_tree_back_and_installs_nothing(release_update):
    root, release, held, installs = release_update
    before = _snapshot(root)
    held(["refused"])
    assert update.do_release_update(release, root, "kazma update -y") is False
    assert _snapshot(root) == before
    assert len(installs) == 1


def test_a_release_whose_archive_is_refused_changes_nothing(release_update):
    root, release, held, installs = release_update
    before = _snapshot(root)
    import dataclasses

    release = dataclasses.replace(
        release, source_problem="the release carries no Sigstore signature for its source archive")
    held([])
    assert update.do_release_update(release, root, "kazma update -y") is False
    assert _snapshot(root) == before and installs == []


def test_a_release_install_is_its_own_install_type(installed, monkeypatch):
    monkeypatch.setattr(update, "_find_release_root", lambda: installed)
    assert update.detect_install_type() == "release"
    monkeypatch.setattr(update, "_find_git_root", lambda: None)
    assert update._install_root() == installed


def test_the_marker_is_json_with_the_version_and_files(tmp_path):
    release_tree.write_marker(tmp_path, "1.0.0", ["b", "a"])
    assert json.loads((tmp_path / MARKER).read_text()) == {"version": "1.0.0", "files": ["a", "b"]}
