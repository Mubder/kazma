"""Kazma installs what it needs, on every OS (resilience plan K2).

``setup.ps1``/``setup.sh`` installed uv and the Python packages only;
Tesseract with Arabic, LibreOffice, ClamAV and its signature refresh, the
fonts and Docker were installed by hand, and a missing one was found later,
one failed document at a time. ``kazma setup system`` checks each, shows the
plan, asks, installs, records; a second run changes nothing. Each package
manager is faked here; nothing is installed.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
from types import SimpleNamespace

import pytest

from kazma_core import system_deps as sd
from kazma_core.postgres_service import Platform

PLATFORMS = {
    "winget": Platform("windows", "winget"),
    "brew": Platform("macos", "brew"),
    "apt": Platform("debian", "apt"),
    "dnf": Platform("fedora", "dnf"),
}


class _Machine:
    """A machine with some programs; ``run`` answers as they would."""

    def __init__(self, *, tesseract_langs=("ara", "eng", "osd"), clam=True, fonts=True, docker=True,
                 freshclam_scheduled=True, soffice=True):
        self.programs = {"tesseract", "fc-list"} if tesseract_langs is not None else {"fc-list"}
        if clam:
            self.programs |= {"clamscan", "freshclam"}
        self.langs, self.fonts, self.docker = tesseract_langs, fonts, docker
        self.scheduled, self.soffice = freshclam_scheduled, soffice
        self.ran: list[list[str]] = []

    def finders(self):
        return {"soffice": lambda: "/usr/bin/soffice" if self.soffice else None,
                "docker": lambda: "/usr/bin/docker" if self.docker else None}

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.programs else None

    def run(self, argv, **kw):
        self.ran.append(list(argv))
        name = argv[0].rsplit("/", 1)[-1]
        if name == "tesseract" and "--list-langs" in argv:
            return subprocess.CompletedProcess(argv, 0, "List of available languages (3):\n" + "\n".join(self.langs), "")
        if name == "tesseract":
            return subprocess.CompletedProcess(argv, 0, "tesseract 5.3.0\n", "")
        if name == "fc-list":
            return subprocess.CompletedProcess(argv, 0, "Noto Naskh Arabic\n" if self.fonts else "DejaVu Sans\n", "")
        if name in ("systemctl", "schtasks", "launchctl"):
            return subprocess.CompletedProcess(argv, 0 if self.scheduled else 1, "enabled" if self.scheduled else "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def machine():
    return _Machine()


def test_a_complete_machine_needs_nothing(machine, tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    for platform in PLATFORMS.values():
        states = sd.check_all(platform, run=machine.run, which=machine.which, **machine.finders())
        assert all(s.ok for s in states), [s for s in states if not s.ok]
        assert sd.install_plan(platform, states) == []


@pytest.mark.parametrize("manager", sorted(PLATFORMS))
def test_each_manager_installs_the_table(machine, manager, tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))  # this machine's own programs are not the fake's
    machine.langs, machine.programs = None, set()
    machine.soffice = machine.docker = machine.fonts = False
    platform = PLATFORMS[manager]
    states = sd.check_all(platform, run=machine.run, which=machine.which, **machine.finders())
    steps = sd.install_plan(platform, states)
    argvs = [" ".join(s.argv) for s in steps if s.argv]
    for dep in ("ocr", "office", "malware", "docker"):
        packages = sd.PACKAGES[manager][dep]
        assert any(all(p in line for p in packages) for line in argvs), (dep, argvs)
    if manager in ("apt", "dnf"):
        assert any(all(p in line for p in sd.PACKAGES[manager]["fonts"]) for line in argvs)
    if manager == "apt":
        assert argvs[0] == "sudo apt-get update"
    if manager == "winget":
        assert [s.action for s in steps if s.action] == ["tessdata:ara", "tessdata:eng"]
    refresh = [s for s in steps if s.title.startswith("refresh the malware signatures")]
    assert len(refresh) == 1, "the signatures get a daily refresh of their own"


def test_only_what_is_missing_is_installed(machine):
    machine.langs = ("eng", "osd")  # tesseract without Arabic
    states = sd.check_all(PLATFORMS["apt"], run=machine.run, which=machine.which, **machine.finders())
    ocr = [s for s in states if s.name == "ocr"][0]
    assert ocr.ok is False and ocr.missing == ["ara"]
    steps = sd.install_plan(PLATFORMS["apt"], states)
    assert [" ".join(s.argv) for s in steps] == [
        "sudo apt-get update",
        "sudo apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng",
    ]
    # Windows: only the Arabic model is downloaded, Tesseract is not reinstalled.
    win = sd.install_plan(PLATFORMS["winget"], states)
    assert [s.action for s in win] == ["tessdata:ara"]


def test_an_unscheduled_signature_refresh_is_scheduled(machine):
    machine.scheduled = False
    states = sd.check_all(PLATFORMS["winget"], run=machine.run, which=machine.which, **machine.finders())
    steps = sd.install_plan(PLATFORMS["winget"], states)
    assert [s.argv[:4] for s in steps] == [("schtasks", "/Create", "/TN", sd.FRESHCLAM_TASK)]


def test_no_package_manager_means_no_plan(machine):
    machine.soffice = False
    states = sd.check_all(Platform("linux", None), run=machine.run, which=machine.which, **machine.finders())
    assert sd.install_plan(Platform("linux", None), states) == []


# ── the pinned Tesseract models ───────────────────────────────────────


def test_a_model_is_kept_only_when_it_matches_its_pin(tmp_path, monkeypatch):
    data = b"fake model bytes"
    monkeypatch.setitem(sd.TESSDATA, "ara", ("https://example.invalid/ara", len(data), hashlib.sha256(data).hexdigest()))

    def opener(url, timeout):
        return io.BytesIO(data)

    assert sd.fetch_tessdata("ara", tmp_path, opener=opener).read_bytes() == data
    # Negative control: other bytes are refused and nothing is left behind.
    with pytest.raises(ValueError, match="pinned"):
        sd.fetch_tessdata("ara", tmp_path / "x", opener=lambda url, timeout: io.BytesIO(b"tampered model!!"))
    assert not (tmp_path / "x").exists() or not list((tmp_path / "x").iterdir())


def test_the_pins_name_a_release_and_a_digest():
    for url, size, digest in sd.TESSDATA.values():
        assert url.startswith("https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/")
        assert size > 1_000_000 and len(digest) == 64


# ── the command ───────────────────────────────────────────────────────


def _options(**kw):
    return SimpleNamespace(**{"yes": False, "check": False, "dry_run": False, **kw})


def _record(tmp_path, monkeypatch):
    import kazma_core.postgres_service as pgs

    path = tmp_path / "setup_record.json"
    monkeypatch.setattr(pgs, "_record_path", lambda: path)
    return path


def test_nothing_is_installed_without_a_yes(machine, tmp_path, monkeypatch):
    from kazma_cli.setup import _setup_system

    _record(tmp_path, monkeypatch)
    machine.soffice = False
    code = _setup_system(_options(), run=machine.run, which=machine.which, ask=lambda q: "n",
                         platform=PLATFORMS["apt"], finders=machine.finders())
    assert code == 1
    assert not any(argv[:2] == ["sudo", "apt-get"] for argv in machine.ran), "nothing was installed"


def test_a_yes_installs_and_a_second_run_changes_nothing(machine, tmp_path, monkeypatch):
    import json

    from kazma_cli.setup import _setup_system

    record = _record(tmp_path, monkeypatch)
    machine.soffice = False

    def run(argv, **kw):
        if argv[:3] == ["sudo", "apt-get", "install"] and "libreoffice-writer" in argv:
            machine.soffice = True  # the install worked
        return machine.run(argv, **kw)

    assert _setup_system(_options(yes=True), run=run, which=machine.which, platform=PLATFORMS["apt"], finders=machine.finders()) == 0
    installs = [a for a in machine.ran if a[:3] == ["sudo", "apt-get", "install"]]
    assert len(installs) == 1
    assert json.loads(record.read_text())["system"]["office"]["ok"] is True
    machine.ran.clear()
    assert _setup_system(_options(yes=True), run=run, which=machine.which, platform=PLATFORMS["apt"], finders=machine.finders()) == 0
    assert not any(a[:2] == ["sudo", "apt-get"] for a in machine.ran), "a second run changes nothing"


def test_check_and_dry_run_change_nothing(machine, tmp_path, monkeypatch):
    from kazma_cli.setup import _setup_system

    _record(tmp_path, monkeypatch)
    machine.soffice = False
    assert _setup_system(_options(check=True), run=machine.run, which=machine.which, platform=PLATFORMS["dnf"], finders=machine.finders()) == 1
    assert _setup_system(_options(dry_run=True), run=machine.run, which=machine.which, platform=PLATFORMS["dnf"], finders=machine.finders()) == 0
    assert not any(a[:2] == ["sudo", "dnf"] for a in machine.ran)


def test_doctor_names_the_fix(machine):
    from kazma_cli.doctor import _system_rows

    machine.soffice = False
    rows = _system_rows(sd.check_all(PLATFORMS["apt"], run=machine.run, which=machine.which, **machine.finders()))
    text = "\n".join(r for _, r in rows)
    assert "LibreOffice is not installed" in text and "kazma setup system" in text
