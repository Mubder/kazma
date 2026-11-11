"""Kazma's installer installs its document font (2026-11-12).

On the owner's PC IBM Plex Sans Arabic was installed for one Windows user,
and Office's Protected View drew Arabic set in it as wrong glyphs of other
scripts; Word showed Arial. Fonts in the Windows Fonts folder (Arial, Calibri,
Segoe UI, Dubai) displayed correctly there. Setup's check said "bundled with
Kazma" on Windows and macOS whatever was installed. Now setup installs the
bundled faces -- for all users on Windows, retiring a per-user copy of the
same faces -- and the check reads what is really installed. A fake Windows
(two font lists, two folders) stands in for the registry and GDI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kazma_core import house_font_install as hf
from kazma_core import system_deps as sd
from kazma_core.postgres_service import Platform

BUNDLE = Path(hf.__file__).resolve().parent / "documents" / "assets" / "fonts"


class _Windows(hf.WindowsFonts):
    """The registry's two font lists and the two font folders, in memory and tmp."""

    def __init__(self, root: Path):
        self.system = {"Arial (TrueType)": "arial.ttf"}
        self.user: dict[str, str] = {}
        self.sys_dir = root / "Windows" / "Fonts"
        self.usr_dir = root / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
        self.sys_dir.mkdir(parents=True)
        self.usr_dir.mkdir(parents=True)
        (self.sys_dir / "arial.ttf").write_bytes(b"arial")
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self.announced = 0

    def system_dir(self) -> Path:
        return self.sys_dir

    def user_dir(self) -> Path:
        return self.usr_dir

    def entries(self, scope: str) -> dict[str, str]:
        return dict(self.system if scope == "system" else self.user)

    def set_system(self, name: str, file: str) -> None:
        self.system[name] = file

    def delete_user(self, name: str) -> None:
        del self.user[name]

    def load(self, path: Path) -> None:
        self.loaded.append(Path(path).name)

    def unload(self, path: Path) -> None:
        self.unloaded.append(Path(path).name)

    def announce(self) -> None:
        self.announced += 1

    def install_per_user(self, *names: str) -> None:
        """What a double-click "Install" does without administrator rights."""
        for name in names:
            (self.usr_dir / name).write_bytes((BUNDLE / name).read_bytes())
            self.user[name.replace(".ttf", " (TrueType)")] = str(self.usr_dir / name)


@pytest.fixture
def windows(tmp_path):
    return _Windows(tmp_path)


def _font(windows) -> hf.HouseFont:
    return hf.HouseFont("windows", windows=windows)


def test_the_owners_machine_per_user_only_is_found_and_fixed(windows):
    windows.install_per_user(*hf.FACES)
    font = _font(windows)
    state = font.state()
    assert state.ok is False and set(state.missing) == {hf.NOT_INSTALLED, hf.PER_USER_COPY}
    assert "Protected View" in state.detail

    ok, said = font.install()
    assert ok, said
    for name, listed_as in hf.FACES.items():
        assert (windows.sys_dir / name).read_bytes() == (BUNDLE / name).read_bytes()
        assert windows.system[listed_as] == name
        assert not (windows.usr_dir / name).exists(), "the per-user copy is retired"
    assert windows.user == {}, "no per-user font list entry is left"
    assert sorted(windows.loaded) == sorted(hf.FACES) and sorted(windows.unloaded) == sorted(hf.FACES)
    assert windows.announced == 1
    assert font.state().ok is True
    assert "Arial (TrueType)" in windows.system, "nothing else in the font list is touched"


def test_a_second_run_changes_nothing(windows):
    font = _font(windows)
    assert font.install()[0]
    copies = {p.name: p.stat().st_mtime_ns for p in windows.sys_dir.iterdir()}
    windows.loaded.clear()
    ok, said = font.install()
    assert ok and said == "already installed for all users"
    assert windows.loaded == [] and {p.name: p.stat().st_mtime_ns for p in windows.sys_dir.iterdir()} == copies


def test_a_per_user_copy_beside_an_all_users_install_is_retired(windows):
    font = _font(windows)
    font.install()
    windows.install_per_user("IBMPlexSansArabic-Regular.ttf")
    state = font.state()
    assert state.ok is False and state.missing == [hf.PER_USER_COPY]
    plan = sd.install_plan(Platform("windows", "winget"), [state])
    assert [(s.action, s.admin) for s in plan] == [("fonts:house", True)]
    assert "retiring the per-user copy" in plan[0].title
    # The plan prints what the step does, not the action's code name.
    assert "Windows Fonts folder" in plan[0].printable() and "per-user copy" in plan[0].printable()
    assert font.install()[0] and font.state().ok is True


def test_other_per_user_fonts_are_left_alone(windows):
    (windows.usr_dir / "IBMPlexSansArabic-Light.ttf").write_bytes(b"light")
    windows.user["IBM Plex Sans Arabic Light (TrueType)"] = str(windows.usr_dir / "IBMPlexSansArabic-Light.ttf")
    (windows.usr_dir / "Inter.ttf").write_bytes(b"inter")
    windows.user["Inter (TrueType)"] = str(windows.usr_dir / "Inter.ttf")
    font = _font(windows)
    assert font.install()[0]
    assert set(windows.user) == {"IBM Plex Sans Arabic Light (TrueType)", "Inter (TrueType)"}
    assert (windows.usr_dir / "Inter.ttf").exists()


def test_without_administrator_rights_it_says_so(windows, monkeypatch):
    def refused(name, file):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(windows, "set_system", refused)
    ok, said = _font(windows).install()
    assert not ok and "elevated" in said


def test_negative_control_the_old_check_passed_a_per_user_install(windows):
    windows.install_per_user(*hf.FACES)
    old = sd.DepState("fonts", True, "bundled with Kazma (IBM Plex Sans Arabic)")  # the check before 2026-11-12
    assert old.ok is True and sd.install_plan(Platform("windows", "winget"), [old]) == []
    new = sd._check_fonts("windows", subprocess.run, lambda name: None, _font(windows))
    assert new.ok is False and sd.install_plan(Platform("windows", "winget"), [new])


def test_macos_installs_in_the_users_font_folder(tmp_path):
    font = hf.HouseFont("macos", home=tmp_path)
    assert font.state().missing == [hf.NOT_INSTALLED]
    assert font.install()[0]
    for name in hf.FACES:
        assert (tmp_path / "Library" / "Fonts" / name).read_bytes() == (BUNDLE / name).read_bytes()
    assert font.state().ok is True
    plan = sd.install_plan(Platform("macos", "brew"), [sd.DepState("fonts", False, "", [hf.NOT_INSTALLED])])
    assert [(s.action, s.admin) for s in plan] == [("fonts:house", False)]


def test_linux_installs_in_the_users_font_folder_and_refreshes_fontconfig(tmp_path):
    ran: list[list[str]] = []

    def run(argv, **kw):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "DejaVu Sans\n", "")

    font = hf.HouseFont("linux", home=tmp_path, run=run, which=lambda name: f"/usr/bin/{name}")
    assert font.state().ok is False
    assert font.install()[0]
    folder = tmp_path / ".local" / "share" / "fonts" / "kazma"
    assert sorted(p.name for p in folder.iterdir()) == sorted(hf.FACES)
    assert ["fc-cache", "-f", str(folder)] in ran
    assert font.state().ok is True


def test_linux_keeps_its_noto_check_beside_the_house_font(tmp_path):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "DejaVu Sans\n", "")

    font = hf.HouseFont("linux", home=tmp_path, run=run, which=lambda name: f"/usr/bin/{name}")
    state = sd._check_fonts("linux", run, lambda name: f"/usr/bin/{name}", font)
    assert state.ok is False and state.missing == ["fonts", hf.NOT_INSTALLED]
    steps = sd.install_plan(Platform("debian", "apt"), [state])
    assert [s.action or " ".join(s.argv) for s in steps] == [
        "sudo apt-get update",
        "sudo apt-get install -y --no-install-recommends fonts-noto-core",
        "fonts:house",
    ]


def test_setup_runs_the_install_step_through_the_house_font():
    from kazma_cli.setup import run_system_step

    class Font:
        def install(self):
            return True, "installed for all users"

    step = sd._house_font_steps(Platform("windows", "winget"), sd.DepState("fonts", False, "", [hf.NOT_INSTALLED]))[0]
    assert run_system_step(step, which=lambda name: None, house_font=Font()) == (True, "installed for all users")
