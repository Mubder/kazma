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
import sys
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


class _HouseFont:
    """Kazma's document font on a fake machine (``house_font_install.HouseFont``)."""

    def __init__(self, installed=True):
        self.installed, self.installs = installed, 0

    def state(self):
        if self.installed:
            return sd.DepState("fonts", True, "IBM Plex Sans Arabic, installed for all users")
        return sd.DepState("fonts", False, "IBM Plex Sans Arabic is not installed", ["ibm plex"])

    def install(self):
        self.installs += 1
        self.installed = True
        return True, "installed for all users"


class _Machine:
    """A machine with some programs; ``run`` answers as they would."""

    def __init__(self, *, tesseract_langs=("ara", "eng", "osd"), clam=True, fonts=True, docker=True,
                 freshclam_scheduled=True, soffice=True, node="v22.11.0", vcruntime=(14, 44)):
        self.vcruntime = vcruntime
        self.house = _HouseFont()
        self.programs = {"tesseract", "fc-list"} if tesseract_langs is not None else {"fc-list"}
        if clam:
            self.programs |= {"clamscan", "freshclam"}
        if node:
            self.programs |= {"node", "npx"}
        self.node = node
        self.langs, self.fonts, self.docker = tesseract_langs, fonts, docker
        self.scheduled, self.soffice = freshclam_scheduled, soffice
        self.ran: list[list[str]] = []

    def finders(self):
        return {"soffice": lambda: "/usr/bin/soffice" if self.soffice else None,
                "docker": lambda: "/usr/bin/docker" if self.docker else None,
                "vcruntime": lambda: self.vcruntime,
                "house_font": self.house}

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
        if name == "node" and "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, f"{self.node}\n", "")
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
    machine.house.installed = False
    machine.vcruntime = None
    platform = PLATFORMS[manager]
    states = sd.check_all(platform, run=machine.run, which=machine.which, **machine.finders())
    steps = sd.install_plan(platform, states)
    argvs = [" ".join(s.argv) for s in steps if s.argv]
    if manager == "winget":
        assert steps[0].dep == "vcruntime" and "Microsoft.VCRedist.2015+.x64" in argvs[0], \
            "the runtime first: what loads it is installed after"
    else:
        assert "vcruntime" not in {s.name for s in states}, "Windows's runtime is never listed elsewhere"
    for dep in ("ocr", "office", "malware", "node", "docker"):
        packages = sd.PACKAGES[manager][dep]
        assert any(all(p in line for p in packages) for line in argvs), (dep, argvs)
    if manager in ("apt", "dnf"):
        assert any(all(p in line for p in sd.PACKAGES[manager]["fonts"]) for line in argvs)
    if manager == "apt":
        assert argvs[0] == "sudo apt-get update"
    if manager == "winget":
        assert [s.action for s in steps if s.action] == ["tessdata:ara", "tessdata:eng", "fonts:house"]
        assert [s.admin for s in steps if s.action == "fonts:house"] == [True], "for all users: an elevated terminal"
    else:
        assert [s.action for s in steps if s.action == "fonts:house"] == ["fonts:house"], \
            "Kazma's document font is installed on every OS"
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


@pytest.mark.parametrize("version, ok", [(None, False), ((14, 29), False), ((14, 40), True), ((14, 51), True)])
def test_the_visual_cpp_runtime_is_checked_by_its_version(machine, version, ok):
    """A fresh Windows has no Visual C++ runtime: PyTorch then fails to load
    c10.dll (the clean VM, 2026-11-08)."""
    machine.vcruntime = version
    states = sd.check_all(PLATFORMS["winget"], run=machine.run, which=machine.which, **machine.finders())
    runtime = states[0]
    assert runtime.name == "vcruntime" and runtime.ok is ok, runtime
    plan = sd.install_plan(PLATFORMS["winget"], states)
    assert ("Microsoft.VCRedist.2015+.x64" in " ".join(plan[0].argv)) is (not ok) if plan else ok


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows registry")
def test_the_runtime_is_read_from_the_registry():
    found = sd._vcruntime_version()
    if found is None:
        pytest.skip("no Visual C++ runtime on this Windows")
    assert isinstance(found, tuple) and found[0] == 14 and found[1] >= 0


@pytest.mark.parametrize("manager", ["apt", "dnf"])
def test_docker_on_linux_starts_at_boot_and_this_account_may_use_it(machine, manager, monkeypatch):
    """Kazma's container engine on Linux is the system's Docker service: setup
    starts it at boot and adds the account Kazma runs as to the docker group
    (without it every container of Kazma's fails on the socket)."""
    monkeypatch.setattr("getpass.getuser", lambda: "kazma-owner")
    machine.docker = False
    states = sd.check_all(PLATFORMS[manager], run=machine.run, which=machine.which, **machine.finders())
    argvs = [" ".join(s.argv) for s in sd.install_plan(PLATFORMS[manager], states)]
    install = next(i for i, a in enumerate(argvs) if any(p in a for p in sd.PACKAGES[manager]["docker"]))
    assert argvs[install + 1:install + 3] == ["sudo systemctl enable --now docker",
                                              "sudo usermod -aG docker kazma-owner"]


def _socket_refused(machine):
    def run(argv, **kw):
        if argv[0].endswith("docker"):
            machine.ran.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "permission denied while trying to connect to the "
                                                            "Docker daemon socket at unix:///var/run/docker.sock")
        return machine.run(argv, **kw)
    return run


def test_a_docker_this_account_may_not_use_gets_the_group_only(machine, monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "kazma-owner")
    states = sd.check_all(PLATFORMS["apt"], run=_socket_refused(machine), which=machine.which, **machine.finders())
    docker = next(s for s in states if s.name == "docker")
    assert docker.ok is False and docker.missing == [sd.DOCKER_GROUP]
    assert [" ".join(s.argv) for s in sd.install_plan(PLATFORMS["apt"], states)] == [
        "sudo systemctl enable --now docker", "sudo usermod -aG docker kazma-owner"], \
        "Docker is not installed again, and the package lists are not refreshed for it"


def test_control_elsewhere_a_refused_socket_is_not_the_group(machine):
    states = sd.check_all(PLATFORMS["winget"], run=_socket_refused(machine), which=machine.which,
                          **machine.finders())
    docker = next(s for s in states if s.name == "docker")
    assert docker.ok is None and sd.install_plan(PLATFORMS["winget"], states) == []


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


# ── a step that fails, or would wait (the drill VM, 2026-11-06) ───────
#
# winget answered 0x80004004 "Cancelled" 59 MB into LibreOffice's 358 MB, the
# setup stopped there ("Nothing past this step was done": no ClamAV, no
# Docker), its message was winget's progress bar read in the wrong code page,
# and at one point the owner had to press Enter for the setup to go on.

#: What winget wrote: its progress bar, one redraw a megabyte, then "Cancelled".
_WINGET_CANCELLED = ("Found LibreOffice [TheDocumentFoundation.LibreOffice] Version 26.2.0.3\n"
                     "Downloading https://download.documentfoundation.org/libreoffice/x86_64.msi\n"
                     + "".join(f"  ████▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒  {mb}.0 MB /  358 MB\r" for mb in range(53, 60))
                     + "\nCancelled\n")
#: E_ABORT, as Python reads winget's exit code.
_E_ABORT = 0x80004004


def _windows(tmp_path, monkeypatch):
    import kazma_core.postgres_service as pgs

    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))  # this machine's own programs are not the fake's
    monkeypatch.setattr(pgs, "is_admin", lambda: True)
    _record(tmp_path, monkeypatch)
    return PLATFORMS["winget"]


def _winget(machine, answers: dict[str, list[tuple[int, bool]]]):
    """A runner whose ``winget install --id X`` answers in turn from
    *answers[X]*: (exit code, whether the package is installed after it)."""
    installs: list[str] = []
    attribute = {"TheDocumentFoundation.LibreOffice": "soffice", "Docker.DockerDesktop": "docker"}

    def run(argv, **kw):
        if argv[:2] == ["winget", "install"]:
            package = argv[argv.index("--id") + 1]
            installs.append(package)
            code, installed = answers[package].pop(0)
            if installed and package in attribute:
                setattr(machine, attribute[package], True)
            elif installed and package == "UB-Mannheim.TesseractOCR":
                machine.programs.add("tesseract")
            return subprocess.CompletedProcess(argv, code, _WINGET_CANCELLED if code else "Successfully installed\n", "")
        return machine.run(argv, **kw)

    return run, installs


def test_a_cancelled_download_is_tried_again(machine, tmp_path, monkeypatch, capsys):
    from kazma_cli.setup import _setup_system

    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = False
    run, installs = _winget(machine, {"TheDocumentFoundation.LibreOffice": [(_E_ABORT, False), (0, True)]})
    waits: list[float] = []
    code = _setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                         finders=machine.finders(), sleep=waits.append)
    out = capsys.readouterr().out
    assert code == 0, out
    assert installs == ["TheDocumentFoundation.LibreOffice"] * 2
    assert waits == [15.0]
    assert "0x80004004" in out and "trying again" in out


def test_one_attempt_leaves_it_out(machine, tmp_path, monkeypatch, capsys):
    """Negative control: one try, as before, and LibreOffice stays missing."""
    import kazma_cli.setup as setup_mod

    monkeypatch.setattr(setup_mod, "_SYSTEM_ATTEMPTS", 1)
    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = False
    run, installs = _winget(machine, {"TheDocumentFoundation.LibreOffice": [(_E_ABORT, False), (0, True)]})
    code = setup_mod._setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                                   finders=machine.finders(), sleep=lambda s: None)
    assert code == 1 and installs == ["TheDocumentFoundation.LibreOffice"]
    assert "LibreOffice is not installed" in capsys.readouterr().out


def test_an_install_that_failed_after_installing_counts_as_done(machine, tmp_path, monkeypatch, capsys):
    from kazma_cli.setup import _setup_system

    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = False
    run, installs = _winget(machine, {"TheDocumentFoundation.LibreOffice": [(_E_ABORT, True)]})
    code = _setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                         finders=machine.finders(), sleep=lambda s: None)
    assert code == 0 and installs == ["TheDocumentFoundation.LibreOffice"], "installed: not run again"
    assert "installed, though the installer answered exit 0x80004004" in capsys.readouterr().out


# The clean Windows 11 VM (2026-11-08): winget answered 0x8A15000F ("Data
# required by the source is missing") to every install until its package
# list's own MSIX was registered; `winget source update` said "Done" and
# changed nothing.

def _winget_without_its_list(machine):
    """A machine whose winget fails every install until the package list is
    registered (Add-AppxPackage of its MSIX)."""
    state = {"registered": False}
    installs: list[str] = []
    repairs: list[list[str]] = []

    def run(argv, **kw):
        if argv[0] == "powershell.exe" and any("Add-AppxPackage" in a for a in argv):
            repairs.append(list(argv))
            state["registered"] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["winget", "install"]:
            package = argv[argv.index("--id") + 1]
            installs.append(package)
            if not state["registered"]:
                return subprocess.CompletedProcess(
                    argv, 0x8A15000F, "Failed when opening source(s); try the 'source reset' command if the "
                    "problem persists.\nAn unexpected error occurred while executing the command:\n"
                    "0x8a15000f : Data required by the source is missing\n", "")
            if package == "TheDocumentFoundation.LibreOffice":
                machine.soffice = True
            return subprocess.CompletedProcess(argv, 0, "Successfully installed\n", "")
        return machine.run(argv, **kw)

    return run, installs, repairs


def test_a_missing_winget_package_list_is_registered_and_the_install_goes_on(machine, tmp_path, monkeypatch,
                                                                            capsys):
    from kazma_cli.setup import WINGET_SOURCE_PACKAGE, _setup_system

    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = False
    run, installs, repairs = _winget_without_its_list(machine)
    waits: list[float] = []
    code = _setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                         finders=machine.finders(), sleep=waits.append)
    out = capsys.readouterr().out
    assert code == 0, out
    assert installs == ["TheDocumentFoundation.LibreOffice"] * 2, "tried again at once, after the repair"
    assert len(repairs) == 1 and WINGET_SOURCE_PACKAGE in " ".join(repairs[0])
    assert waits == [], "no retry wait: the repair is what the install needed"


def test_control_without_the_repair_every_attempt_fails(machine, tmp_path, monkeypatch, capsys):
    """Negative control: the step runner before 2026-11-08 tried winget three
    times and left LibreOffice out."""
    import kazma_cli.setup as setup_mod

    monkeypatch.setattr(setup_mod, "_winget_source_missing", lambda step, detail: False)
    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = False
    run, installs, repairs = _winget_without_its_list(machine)
    code = setup_mod._setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                                   finders=machine.finders(), sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == 1 and installs == ["TheDocumentFoundation.LibreOffice"] * 3 and repairs == []
    assert "0x8A15000F" in out and "LibreOffice is not installed" in out


def test_a_repair_that_fails_says_so(machine, tmp_path, monkeypatch, capsys):
    from kazma_cli.setup import run_system_step
    from kazma_core.postgres_service import Step

    def run(argv, **kw):
        if argv[0] == "powershell.exe":
            return subprocess.CompletedProcess(argv, 1, "", "Add-AppxPackage : Deployment failed")
        return subprocess.CompletedProcess(argv, 0x8A15000F, "0x8a15000f : Data required by the source is missing", "")

    ok, detail = run_system_step(Step("install LibreOffice", ("winget", "install", "--id", "X"), dep="office"),
                                 which=machine.which, run=run)
    assert not ok and "registering winget's package list failed" in detail and "Deployment failed" in detail


def test_a_long_step_has_time_and_a_cut_off_one_says_so():
    """The clean VM (2026-11-08): at about 1 MB/s Kazma's optional packages
    took more than the 30 minutes a step had; uv was killed mid-download and
    the step said "could not run"."""
    from kazma_cli.setup import STEP_TIMEOUT_S, _run_step
    from kazma_core.postgres_service import Step

    given: list[float] = []

    def run(argv, **kw):
        given.append(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    logged: list[str] = []
    ok, detail = _run_step(Step("install Kazma's packages", ("uv", "sync")), [], run=run, log=logged.append)
    assert given == [STEP_TIMEOUT_S] and STEP_TIMEOUT_S >= 2 * 3600, "an hour's download fits"
    assert not ok and detail == f"still running after {STEP_TIMEOUT_S // 60} min, stopped"
    assert "could not run" not in detail and "was still running" in logged[0]


def test_another_programs_failure_is_not_taken_for_winget_s(machine):
    from kazma_cli.setup import run_system_step
    from kazma_core.postgres_service import Step

    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0x8A15000F, "", "")

    ok, _ = run_system_step(Step("install x", ("brew", "install", "x"), dep="office"), which=machine.which, run=run)
    assert not ok and calls == [["brew", "install", "x"]], "only winget's answer means its list is missing"


def test_one_failed_dependency_does_not_stop_the_others(machine, tmp_path, monkeypatch, capsys):
    """LibreOffice comes before Docker: before, a failed LibreOffice meant no Docker."""
    from kazma_cli.setup import _setup_system

    platform = _windows(tmp_path, monkeypatch)
    machine.soffice = machine.docker = False
    run, installs = _winget(machine, {"TheDocumentFoundation.LibreOffice": [(_E_ABORT, False)] * 3,
                                      "Docker.DockerDesktop": [(0, True)]})
    code = _setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                         finders=machine.finders(), sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == 1
    assert installs == ["TheDocumentFoundation.LibreOffice"] * 3 + ["Docker.DockerDesktop"]
    assert machine.docker, "Docker was installed after LibreOffice failed"
    assert "Not done: install Office conversion (LibreOffice)." in out
    assert "MB /" not in out, "the progress bar is not the failure's message"
    assert "Cancelled" in out


def test_a_dependency_s_later_steps_wait_for_its_install(machine, tmp_path, monkeypatch, capsys):
    from kazma_cli.setup import _setup_system

    platform = _windows(tmp_path, monkeypatch)
    machine.programs.discard("tesseract")
    fetched: list[str] = []
    monkeypatch.setattr(sd, "fetch_tessdata", lambda lang, folder: fetched.append(lang))
    run, installs = _winget(machine, {"UB-Mannheim.TesseractOCR": [(_E_ABORT, False)] * 3})
    code = _setup_system(_options(yes=True), run=run, which=machine.which, platform=platform,
                         finders=machine.finders(), sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == 1 and fetched == []
    assert "download Tesseract's ara model: not done" in out


def test_no_terminal_to_answer_from_means_no(machine, tmp_path, monkeypatch, capsys):
    from kazma_cli.setup import _setup_system

    _record(tmp_path, monkeypatch)
    machine.soffice = False

    def no_terminal(question):
        raise EOFError

    code = _setup_system(_options(), run=machine.run, which=machine.which, ask=no_terminal,
                         platform=PLATFORMS["apt"], finders=machine.finders())
    assert code == 1 and "Nothing was done." in capsys.readouterr().out
    assert not any(a[:2] == ["sudo", "apt-get"] for a in machine.ran)


def test_an_installers_output_is_read_as_it_wrote_it():
    """A real child writing winget's bytes: UTF-8, its progress dropped, the
    HRESULT in hex. Read in the ANSI code page (as before), the same bytes
    are the mojibake the VM showed."""
    from kazma_cli.setup import _exit_text, _run_step
    from kazma_core.postgres_service import Step

    payload = ("  ██▒▒▒▒  59.0 MB / 358 MB" + chr(13) + chr(10) + "Cancelled — try again" + chr(10)).encode("utf-8")
    child = f"import sys; sys.stdout.buffer.write(bytes.fromhex('{payload.hex()}')); sys.exit(3)"
    ok, detail = _run_step(Step("t", (sys.executable, "-I", "-c", child)), [])
    assert not ok and detail == "exit 3: Cancelled — try again"
    assert "â" in payload.decode("cp1252", errors="replace"), "the old read's view"
    assert _exit_text(2147500036) == "0x80004004" and _exit_text(1) == "1"


def test_a_step_reads_what_it_is_given_or_nothing():
    """A real child that reads its input gets the end of it at once, never
    the terminal (an installer handed the terminal waited for Enter)."""
    from kazma_cli.setup import _run_step
    from kazma_core.postgres_service import Step

    child = "import sys; data = sys.stdin.read(); print('read', len(data))"
    assert _run_step(Step("t", (sys.executable, "-I", "-c", child)), []) == (True, "exit 0: read 0")
    assert _run_step(Step("t", (sys.executable, "-I", "-c", child), stdin="abc"), []) == (True, "exit 0: read 3")
    seen: dict = {}

    def record(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    _run_step(Step("t", ("x",)), [], run=record)
    assert seen.get("stdin") is subprocess.DEVNULL and "input" not in seen


#: The modules ``kazma setup`` runs programs from, at a terminal.
_SETUP_MODULES = ("kazma-cli/kazma_cli/setup.py", "kazma-core/kazma_core/system_deps.py",
                  "kazma-core/kazma_core/postgres_service.py", "kazma-core/kazma_core/backup/pg_move.py",
                  "kazma-core/kazma_core/backup/wal_archive.py")


def calls_on_the_terminal(source: str) -> list[int]:
    """Lines of process calls that capture output but leave the input as the
    terminal's: every call with ``capture_output``/``stdout`` must say its
    ``stdin`` or ``input`` (``**feed`` counts as saying it)."""
    import ast

    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            names = {k.arg for k in node.keywords}
            if names & {"capture_output", "stdout"} and not names & {"stdin", "input", None}:
                lines.append(node.lineno)
    return lines


def postgres_clients_that_can_prompt(source: str) -> list[int]:
    """Lines of a Postgres client argv that connects to a host ("-h") with no
    ``-w``/``--no-password``: psql and pg_restore ask for a refused password
    on the console, whatever their input is. ``postgres_service._psql`` is
    checked by what it returns (its flags come from a tuple it unpacks)."""
    import ast

    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_psql":
            skip |= {id(n) for n in ast.walk(node)}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and id(node) not in skip:
            words = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if "-h" in words and not words & {"-w", "--no-password"}:
                lines.append(node.lineno)
    return lines


def test_no_setup_program_waits_on_the_terminal():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for module in _SETUP_MODULES:
        source = (root / module).read_text(encoding="utf-8")
        assert calls_on_the_terminal(source) == [], module
        assert postgres_clients_that_can_prompt(source) == [], module
    # Negative controls: the shapes of before.
    assert calls_on_the_terminal("run(argv, capture_output=True, text=True, timeout=60)") == [1]
    assert postgres_clients_that_can_prompt('run([pg_restore, "-h", "127.0.0.1", "-d", "kazma", dump])') == [1]


def test_psql_never_asks_for_a_password():
    from kazma_core.postgres_service import _psql

    for platform in PLATFORMS.values():
        assert "-w" in _psql(platform, port=5432), platform
