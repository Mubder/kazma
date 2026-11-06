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
