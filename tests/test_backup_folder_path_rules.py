"""The backup folder's rules hold for every name a path can have.

SonarCloud review, 2026-11-21 (pythonsecurity:S2083 x4 on
``folder_copy.test_folder``): the folder Settings → Backup's Test names is
written to. Choosing any folder is the feature -- another drive, a USB disk,
a network share -- and the route is admin-only and same-origin, so the
finding itself is by design. Reading the path's rules for it found three
gaps, each fixed here with the old code as its negative control:

* a device path (``\\\\.\\pipe\\x``, ``\\\\.\\PhysicalDrive0``,
  ``\\\\?\\C:\\x``, ``\\??\\...``) passed ``path_problem`` as "absolute", so
  the Test opened it, wrote its probe into it and the save kept it. It is now
  refused on its text (``remote_paths.device_path_reason``), before anything
  opens it, by the Test, the save and the picker alike. A plain share is not
  a device path.
* "inside Kazma's data folder" was judged by the resolved path, and a second
  name of the same folder resolves to itself: ``\\\\?\\C:\\...`` and a
  loopback share (``\\\\localhost\\C$\\...``) were "another place". The
  check now also compares each folder's identity (volume and file id).
* "the disk Kazma's data is on" was judged by the drive letter: a loopback
  share of Kazma's own disk read as another disk (the Test told the owner the
  copy would survive that disk). It is now the volume itself.
* the save refused two of ``path_problem``'s codes by name and kept a path
  with any other: every code now refuses the save (a class gate over the
  codes, the page's words and the catalog).
"""

from __future__ import annotations

import ast
import errno
import inspect
import os
import re
from pathlib import Path

import pytest
from kazma_core.backup import folder_copy as fc
from kazma_core.settings_validation import SettingRejected, validate_setting
from kazma_core.workspace import folder_identity
from kazma_core.workspace.remote_paths import device_path_reason, remote_path_reason

ROOT = Path(__file__).resolve().parents[1]

DEVICE = [
    r"\\.\pipe\attacker",
    r"\\.\PhysicalDrive0\attacker",
    r"\\?\C:\attacker\backups",
    r"\\?\UNC\attacker\share\backups",
    r"\??\C:\attacker",
    "//./pipe/attacker",
    "//?/C:/attacker",
    r"\\.",
    '  "\\\\.\\pipe\\attacker"  ',
]
SHARES = [r"\\attacker\share\backups", "//attacker/share/backups", r"\\nas.local\kazma"]


@pytest.mark.parametrize("text", DEVICE)
def test_device_paths_are_named_on_their_text(text):
    assert device_path_reason(text)
    assert remote_path_reason(text), "the wider rule refuses them too"


@pytest.mark.parametrize("text", SHARES + [r"C:\backups", "/mnt/usb", "D:", ""])
def test_a_share_or_a_drive_is_not_a_device_path(text):
    assert device_path_reason(text) is None


@pytest.fixture
def touches(monkeypatch) -> list[str]:
    """Every filesystem call made on an "attacker" path, answered here so no
    test ever opens one."""
    seen: list[str] = []

    def names_attacker(path) -> bool:
        return isinstance(path, (str, os.PathLike)) and "attacker" in str(os.fspath(path))

    def guard(real, *, returns_path=False):
        def call(path, *args, **kwargs):
            if names_attacker(path):
                seen.append(os.fspath(path))
                if returns_path:
                    return os.fspath(path)
                raise FileNotFoundError(os.fspath(path))
            return real(path, *args, **kwargs)
        return call

    monkeypatch.setattr(os.path, "realpath", guard(os.path.realpath, returns_path=True))
    for name in ("stat", "lstat", "scandir", "listdir", "makedirs", "mkdir"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    for name in ("resolve", "is_dir", "exists", "mkdir", "write_bytes", "read_bytes", "unlink"):
        real = getattr(Path, name)

        def method(self, *args, _real=real, _name=name, **kwargs):
            if names_attacker(self):
                seen.append(f"{_name}:{self}")
                if _name == "resolve":
                    return self
                if _name in ("is_dir", "exists"):
                    return False
                raise FileNotFoundError(str(self))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, method)
    return seen


@pytest.mark.parametrize("text", DEVICE[:-1])
def test_the_folder_test_refuses_a_device_path_before_opening_it(touches, text):
    res = fc.test_folder(text)
    assert res["ok"] is False and res["problem"] == "device"
    assert res["checks"] == []
    assert touches == [], f"the Test touched the device path: {touches}"


@pytest.mark.parametrize("text", DEVICE[:-1])
def test_the_save_refuses_a_device_path(touches, text):
    with pytest.raises(SettingRejected, match="device path"):
        validate_setting(fc.PATH_KEY, text)
    assert touches == []


def test_a_stored_device_path_is_no_folder(touches, monkeypatch):
    monkeypatch.setattr(fc, "folder_settings", lambda: {"enabled": True, "path": r"\\.\pipe\attacker", "keep": 7})
    assert fc.folder_state()["state"] == "off"
    assert fc.repo_for_backup() == ""
    assert touches == []


def _old_path_problem(path: str, data_dir: Path) -> str | None:
    """``path_problem`` as it was before 2026-11-21."""
    text = (path or "").strip()
    if not text:
        return "empty"
    candidate = Path(text)
    if not candidate.is_absolute() and not text.startswith("\\\\"):
        return "relative"
    try:
        candidate.resolve().relative_to(data_dir.resolve())
        return "inside_data"
    except (ValueError, OSError):
        return None


#: The device forms the old rule took for a folder (``\??\`` it read as relative).
_OLD_ACCEPTED = [t for t in DEVICE[:-1] if not t.startswith("\\??")]


@pytest.mark.parametrize("text", _OLD_ACCEPTED)
def test_control_the_old_rule_let_a_device_path_through(touches, tmp_path, text):
    assert _old_path_problem(text, tmp_path) is None, "the old rule called a device path a folder"


# ── A second name of the data folder ──────────────────────────────────────


def test_inside_data_is_judged_by_identity_too(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "backups").mkdir(parents=True)
    monkeypatch.setattr(fc, "_data_dir", lambda: data)
    alias = Path(tmp_path / "elsewhere" / "copy")  # a second name of data/backups/
    real_identity = fc._identity

    def identity(path: Path):
        if str(path) == str(alias.parent):
            return real_identity(data)
        return real_identity(path)

    # The rule lives in ``workspace.folder_identity`` (Spaces S2-12): the
    # backup folder and a shared space's project folder ask it.
    monkeypatch.setattr(fc, "_identity", identity)
    monkeypatch.setattr(folder_identity, "identity", identity)
    assert fc.path_problem(str(alias)) == "inside_data"
    assert fc.path_problem(str(tmp_path / "other")) is None
    assert fc.path_problem(str(data / "backups" / "copy")) == "inside_data"


def _unreachable(path: str) -> OSError:
    """What os.stat raises for a share whose host does not answer."""
    if os.name == "nt":
        return OSError(0, "The network path was not found", path, 53)  # ERROR_BAD_NETPATH
    return OSError(errno.EHOSTUNREACH, "No route to host", path)


def test_an_offline_share_is_asked_once_not_once_per_folder(tmp_path, monkeypatch):
    """A USB disk unplugged or a NAS asleep is the folder's "missing" state:
    each question to it waits out the network's timeout, so the walk up stops
    at the first folder that does not answer."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(fc, "_data_dir", lambda: data)
    asked: list[str] = []

    class _Os:
        def __getattr__(self, name):
            return getattr(os, name)

        def stat(self, path, *args, **kwargs):
            if "nas-asleep" in str(path):
                asked.append(str(path))
                raise _unreachable(str(path))
            return os.stat(path, *args, **kwargs)

    monkeypatch.setattr(fc, "os", _Os())
    monkeypatch.setattr(folder_identity, "os", _Os())
    folder = tmp_path / "nas-asleep" / "share" / "backups"
    assert fc.path_problem(str(folder)) is None
    assert asked == [str(folder)], f"one question to the offline share, not one per level: {asked}"
    assert fc._same_disk(folder, data) is False

    # The control: a walk that reads every error as "not there" asks each level.
    asked.clear()
    here, target = folder, fc._identity(data)
    while here != here.parent:
        try:
            info = fc.os.stat(here)
        except OSError:
            info = None
        if info is not None and (info.st_dev, info.st_ino) == target:
            break
        here = here.parent
    assert len(asked) == 3


def test_not_there_is_told_from_not_answering():
    assert fc._not_made_yet(FileNotFoundError(errno.ENOENT, "no such file"))
    assert not fc._not_made_yet(_unreachable("x"))
    if os.name == "nt":
        assert fc._not_made_yet(OSError(0, "The system cannot find the path specified", "x", 3))
        assert not fc._not_made_yet(OSError(0, "Access is denied", "x", 5))


def _loopback_alias(path: Path) -> Path | None:
    """*path* through this machine's administrative share, when Windows
    lets this account reach it (an administrator's session); else None."""
    if os.name != "nt":
        return None
    drive = path.drive.rstrip(":")
    if len(drive) != 1:
        return None
    alias = Path(rf"\\localhost\{drive}$") / path.relative_to(path.anchor)
    try:
        if os.path.samestat(os.stat(alias), os.stat(path)):
            return alias
    except OSError:
        return None
    return None


def test_a_loopback_share_of_the_data_folder_is_inside_it(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    alias = _loopback_alias(data)
    if alias is None:
        pytest.skip("this machine's administrative share cannot be reached from this account")
    monkeypatch.setattr(fc, "_data_dir", lambda: data)
    assert fc.path_problem(str(alias / "backups")) == "inside_data"
    with pytest.raises(SettingRejected, match="data folder"):
        validate_setting(fc.PATH_KEY, str(alias / "backups"))
    # The control: the old rule took the loopback name for another folder.
    assert _old_path_problem(str(alias / "backups"), data) is None


def test_a_loopback_share_of_kazmas_disk_is_the_same_disk(tmp_path):
    here = tmp_path / "copy"
    here.mkdir()
    alias = _loopback_alias(here)
    if alias is None:
        pytest.skip("this machine's administrative share cannot be reached from this account")
    assert fc._same_disk(alias, tmp_path) is True

    def old_same_disk(folder: Path, other: Path) -> bool:  # before 2026-11-21, on Windows
        return folder.resolve().drive.lower() == other.resolve().drive.lower()

    assert old_same_disk(alias, tmp_path) is False, "the control: the drive letter called it another disk"


def test_the_same_disk_is_the_volume(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert fc._same_disk(a, b) is True
    assert fc._same_disk(a, tmp_path / "not-there") is False
    monkeypatch.setattr(fc, "_identity", lambda p: (1, 1) if p == a else (2, 2))
    assert fc._same_disk(a, b) is False


# ── Every code refuses the save, and the page words it ────────────────────


def _codes_path_problem_answers() -> set[str]:
    tree = ast.parse(inspect.getsource(fc.path_problem).lstrip())
    return {node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)}


def test_path_problems_names_every_code():
    assert _codes_path_problem_answers() == set(fc.PATH_PROBLEMS)


def _a_path_for(code: str, data_dir: Path) -> str:
    return {
        "empty": "",
        "device": r"\\.\pipe\x",
        "relative": "backups/usb",
        "inside_data": str(data_dir / "backups" / "copy"),
    }[code]


@pytest.mark.parametrize("code", fc.PATH_PROBLEMS)
def test_every_code_refuses_the_save(code):
    from kazma_core.paths import data_dir

    path = _a_path_for(code, data_dir())
    assert fc.path_problem(path) == code
    if code == "empty":
        assert validate_setting(fc.PATH_KEY, path)[0] == "", "an empty path is no folder: the copy is off"
        return
    with pytest.raises(SettingRejected):
        validate_setting(fc.PATH_KEY, path)


def test_control_the_old_save_kept_a_path_with_a_code_it_did_not_name(monkeypatch):
    """The save as it was: two codes refused by name, any other kept."""
    def old_folder_path(text: str, problem: str | None) -> str:
        if problem == "relative":
            raise SettingRejected("relative")
        if problem == "inside_data":
            raise SettingRejected("inside_data")
        return text

    assert old_folder_path(r"\\.\pipe\x", "device") == r"\\.\pipe\x"


def test_the_page_words_every_code():
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS as CATALOG

    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_ops.js").read_text(encoding="utf-8")
    known = re.search(r"backupFolderProblemText\(code, where\) \{\s*const known = \[([^\]]*)\]", js)
    assert known, "settings_ops.js: backupFolderProblemText's list of known codes moved"
    listed = set(re.findall(r"'([a-z_]+)'", known.group(1)))
    for code in fc.PATH_PROBLEMS:
        assert code in listed, f"the page does not word {code!r}"
        entry = CATALOG.get(f"settings.bkf.problem_{code}")
        assert entry and entry.get("en") and entry.get("ar"), f"settings.bkf.problem_{code} in both languages"
