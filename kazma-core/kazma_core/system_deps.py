"""Every system dependency, installed by Kazma (resilience plan K2).

``setup.ps1`` and ``setup.sh`` install uv and the Python packages only.
Tesseract (with Arabic and English), LibreOffice, ClamAV and its signature
updates, the Arabic fonts, Node.js and Docker were installed by hand, and a
missing one was found later, one failed document at a time. ``kazma setup system``
(``kazma_cli.setup``) does it the way ``kazma setup postgres`` does
Postgres: check each one, show the plan, ask, run it, record it.

One table per package manager (:data:`PACKAGES`); Postgres is
``kazma setup postgres`` (K1). Windows installs Tesseract without Arabic, so
the Arabic and English models are downloaded from a pinned release and
checked against their SHA-256 (:data:`TESSDATA`). The malware signatures get
a daily refresh of their own: a scheduled task (Windows), a launchd job
(macOS), the distribution's freshclam service (Linux). Nothing is installed
without the owner's yes (``-y`` for a scripted install), and a second run
changes nothing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from kazma_core.postgres_service import Platform, Step

__all__ = ["DEPENDENCIES", "PACKAGES", "TESSDATA", "DepState", "DockerReadiness", "check_all",
           "docker_readiness", "fetch_tessdata", "freshclam_agent_plist", "install_plan", "installs_package",
           "needed_here", "package_is_in", "record_system", "tessdata_folder"]

Runner = Callable[..., subprocess.CompletedProcess]

#: What Kazma needs from the system, in install order. ``vcruntime`` is
#: Windows only, and first: PyTorch and ONNX Runtime load it.
DEPENDENCIES = ("vcruntime", "ocr", "office", "malware", "fonts", "node", "docker")
#: Dependencies that exist on one OS only (the others never list them).
WINDOWS_ONLY = ("vcruntime",)
LABELS = {
    "vcruntime": "Microsoft Visual C++ runtime (PyTorch and ONNX Runtime need it)",
    "ocr": "OCR (Tesseract, Arabic and English)",
    "office": "Office conversion (LibreOffice)",
    "malware": "Malware scan (ClamAV and its daily signature refresh)",
    "fonts": "Arabic fonts",
    "node": "Node.js (MCP servers started with npx)",
    "docker": "Docker (the document container, the sandbox, SearXNG)",
}

#: Each dependency's packages per package manager. ``None``: no package to
#: install there. Kazma's own document font is a step of its own on every OS
#: (``house_font_install``, :func:`_house_font_steps`).
PACKAGES: dict[str, dict[str, tuple[str, ...] | None]] = {
    "winget": {
        "vcruntime": ("Microsoft.VCRedist.2015+.x64",),
        "ocr": ("UB-Mannheim.TesseractOCR",),
        "office": ("TheDocumentFoundation.LibreOffice",),
        "malware": ("Cisco.ClamAV",),
        "fonts": None,
        "node": ("OpenJS.NodeJS.LTS",),
        "docker": ("Docker.DockerDesktop",),
    },
    "brew": {
        "vcruntime": None,
        "ocr": ("tesseract", "tesseract-lang"),
        "office": ("--cask", "libreoffice"),
        "malware": ("clamav",),
        "fonts": None,
        "node": ("node",),
        "docker": ("--cask", "docker"),
    },
    "apt": {
        "vcruntime": None,
        "ocr": ("tesseract-ocr", "tesseract-ocr-ara", "tesseract-ocr-eng"),
        "office": ("libreoffice-writer",),
        "malware": ("clamav", "clamav-freshclam"),
        "fonts": ("fonts-noto-core",),
        "node": ("nodejs", "npm"),
        "docker": ("docker.io",),
    },
    "dnf": {
        "vcruntime": None,
        "ocr": ("tesseract", "tesseract-langpack-ara"),
        "office": ("libreoffice-writer",),
        "malware": ("clamav", "clamav-update"),
        "fonts": ("google-noto-sans-arabic-fonts",),
        "node": ("nodejs", "npm"),
        "docker": ("moby-engine",),
    },
}

#: Tesseract's models from the tessdata_fast 4.1.0 release, checked against
#: these digests before they are kept (Windows: the installer brings English
#: only). Recorded 2026-11-06.
TESSDATA: dict[str, tuple[str, int, str]] = {
    "ara": ("https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/ara.traineddata", 1_432_056,
            "e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729"),
    "eng": ("https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/eng.traineddata", 4_113_088,
            "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"),
}

#: The Windows task that refreshes the malware signatures every day.
FRESHCLAM_TASK = "KazmaFreshclam"
#: The macOS launchd job that does the same.
FRESHCLAM_AGENT = "ai.kazma.freshclam"


@dataclass
class DepState:
    """One dependency as found: ``ok`` True, False, or None (cannot tell)."""

    name: str
    ok: bool | None
    detail: str
    missing: list[str] = field(default_factory=list)


def _out(run: Runner, argv: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                   check=False, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


#: The oldest x64 Visual C++ runtime accepted (major, minor): Visual Studio
#: 2022's. A newer runtime runs what an older toolset built, never the reverse.
VCRUNTIME_MIN = (14, 40)
_VCRUNTIME_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"


def _vcruntime_version() -> tuple[int, int] | None:
    """The x64 Visual C++ runtime installed on this Windows, as (major,
    minor), or None. A fresh Windows has none (Python brings vcruntime140.dll
    only), and PyTorch then fails to load c10.dll: on the clean VM
    (2026-11-08) the meaning-search model could not download. An earlier VM
    had it only because a winget install before it had brought it along."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VCRUNTIME_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            installed = winreg.QueryValueEx(key, "Installed")[0]
            major = winreg.QueryValueEx(key, "Major")[0]
            minor = winreg.QueryValueEx(key, "Minor")[0]
    except OSError:
        return None
    return (int(major), int(minor)) if installed else None


def _check_vcruntime(find: Callable[[], tuple[int, int] | None]) -> DepState:
    version = find()
    if version is None:
        return DepState("vcruntime", False, "the Microsoft Visual C++ runtime is not installed", ["vcruntime"])
    shown = f"{version[0]}.{version[1]}"
    if tuple(version) < VCRUNTIME_MIN:
        return DepState("vcruntime", False, f"the Visual C++ runtime is {shown}, older than "
                        f"{VCRUNTIME_MIN[0]}.{VCRUNTIME_MIN[1]}", ["vcruntime"])
    return DepState("vcruntime", True, f"Microsoft Visual C++ runtime {shown}")


def needed_here(dep: str, platform: Platform) -> bool:
    """Whether *dep* exists on *platform* at all (the Visual C++ runtime is Windows's)."""
    return dep not in WINDOWS_ONLY or platform.os == "windows"


def _tesseract(which: Callable[[str], str | None]) -> str | None:
    found = which("tesseract")
    if found:
        return found
    exe = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
    return str(exe) if os.name == "nt" and exe.is_file() else None


def _check_ocr(run: Runner, which: Callable[[str], str | None]) -> DepState:
    exe = _tesseract(which)
    if not exe:
        return DepState("ocr", False, "tesseract is not installed", ["tesseract"])
    code, text = _out(run, [exe, "--list-langs"])
    if code != 0:
        return DepState("ocr", None, f"tesseract did not list its languages (exit {code})")
    langs = {line.strip() for line in text.splitlines()}
    missing = [lang for lang in ("ara", "eng") if lang not in langs]
    if missing:
        return DepState("ocr", False, f"tesseract has no {' or '.join(missing)} model", missing)
    _, version = _out(run, [exe, "--version"])
    first = (version.splitlines() or [""])[0].strip()
    return DepState("ocr", True, f"{first or 'tesseract'}, Arabic and English")


def _check_office(find: Callable[[], str | None]) -> DepState:
    exe = find()
    if not exe:
        return DepState("office", False, "LibreOffice is not installed", ["libreoffice"])
    return DepState("office", True, f"LibreOffice at {exe}")


def _freshclam_scheduled(os_name: str, run: Runner) -> bool | None:
    if os_name == "windows":
        code, _ = _out(run, ["schtasks", "/Query", "/TN", FRESHCLAM_TASK])
        return code == 0
    if os_name == "macos":
        code, _ = _out(run, ["launchctl", "list", FRESHCLAM_AGENT])
        return code == 0
    service = "clamav-freshclam"
    code, text = _out(run, ["systemctl", "is-enabled", service])
    if code == 127:
        return None  # no systemd (a container, WSL without it): cannot tell
    return code == 0 or "enabled" in text


def _check_malware(os_name: str, run: Runner, which: Callable[[str], str | None]) -> DepState:
    scanner = which("clamscan") or which("clamdscan")
    if not scanner and os_name == "windows":
        candidate = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "ClamAV" / "clamscan.exe"
        scanner = str(candidate) if candidate.is_file() else None
    if not scanner:
        return DepState("malware", False, "ClamAV is not installed", ["clamav"])
    scheduled = _freshclam_scheduled(os_name, run)
    if scheduled is False:
        return DepState("malware", False, "ClamAV is installed, but nothing refreshes its signatures daily",
                        ["freshclam schedule"])
    return DepState("malware", True, "ClamAV" + ("" if scheduled else " (daily refresh: cannot tell here)"))


def _check_fonts(os_name: str, run: Runner, which: Callable[[str], str | None], house: Any) -> DepState:
    """Kazma's document font on this machine (``house_font_install``), and on
    Linux the Noto Arabic faces LibreOffice falls back to. The old check said
    "bundled with Kazma" on Windows and macOS whatever was installed: the
    owner's per-user IBM Plex, which Office's Protected View draws as wrong
    glyphs, passed it (2026-11-12)."""
    own = house.state()
    if os_name in ("windows", "macos"):
        return own
    if not which("fc-list"):
        return DepState("fonts", False, "fontconfig (fc-list) is not installed", ["fonts", *own.missing])
    _, text = _out(run, ["fc-list", ":lang=ar", "family"])
    noto = bool(re.search(r"Noto (Naskh|Sans|Kufi) Arabic", text))
    if noto and own.ok:
        return DepState("fonts", True, f"Noto Arabic fonts; {own.detail}")
    missing = ([] if noto else ["fonts"]) + list(own.missing)
    detail = "; ".join(part for part in ("" if noto else "no Noto Arabic font", "" if own.ok else own.detail) if part)
    return DepState("fonts", False, detail, missing)


#: The oldest Node.js major the MCP servers Kazma ships are run with.
NODE_MIN_MAJOR = 18


def _npx(which: Callable[[str], str | None]) -> str | None:
    found = which("npx")
    if found:
        return found
    # Installed in this run: Windows gives a new PATH to new processes only.
    exe = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "nodejs" / "npx.cmd"
    return str(exe) if os.name == "nt" and exe.is_file() else None


def _check_node(run: Runner, which: Callable[[str], str | None]) -> DepState:
    npx = _npx(which)
    if not npx:
        return DepState("node", False, "Node.js is not installed (no npx)", ["node"])
    node = which("node") or str(Path(npx).with_name("node.exe" if os.name == "nt" else "node"))
    code, text = _out(run, [node, "--version"])
    match = re.match(r"v?(\d+)\.", text.strip())
    if code != 0 or not match:
        return DepState("node", None, f"npx is installed; node did not say its version (exit {code})")
    if int(match.group(1)) < NODE_MIN_MAJOR:
        return DepState("node", False, f"Node.js {text.strip()} is older than {NODE_MIN_MAJOR}", ["node"])
    return DepState("node", True, f"Node.js {text.strip()}")


def _check_docker(run: Runner, find: Callable[[], str | None], os_name: str = "") -> DepState:
    exe = find()
    if not exe:
        return DepState("docker", False, "Docker is not installed", ["docker"])
    code, text = _out(run, [exe, "version", "--format", "{{.Server.Version}}"], timeout=20)
    if code != 0 and os_name in _LINUX and "permission denied" in text.lower():
        # The engine runs; this account may not use its socket (Kazma runs as
        # this account, so every container of Kazma's would fail).
        return DepState("docker", False, "Docker runs, but this account may not use it (not in the docker group)",
                        [DOCKER_GROUP])
    if code != 0:
        return DepState("docker", None, "Docker is installed; its engine does not answer now")
    return DepState("docker", True, f"Docker engine {text.strip()[:40]}")


def check_all(platform: Platform, *, run: Runner = subprocess.run,
              which: Callable[[str], str | None] = shutil.which,
              soffice: Callable[[], str | None] | None = None,
              docker: Callable[[], str | None] | None = None,
              vcruntime: Callable[[], tuple[int, int] | None] | None = None,
              house_font: Any = None) -> list[DepState]:
    """Every dependency as found on this machine. *soffice*, *docker* and
    *vcruntime* find those (Kazma's own finders by default); *house_font*
    checks and installs Kazma's document font (``house_font_install.HouseFont``).
    The Visual C++ runtime is listed on Windows only, first: what loads it
    comes after."""
    if soffice is None:
        from kazma_core.documents.binaries import find_soffice as soffice
    if docker is None:
        from kazma_core.docker_cli import find_docker_cli as docker
    if house_font is None:
        from kazma_core.house_font_install import HouseFont

        house_font = HouseFont(platform.os if platform.os in ("windows", "macos") else "linux")
    first = [_check_vcruntime(vcruntime or _vcruntime_version)] if platform.os == "windows" else []
    return [
        *first,
        _check_ocr(run, which),
        _check_office(soffice),
        _check_malware(platform.os, run, which),
        _check_fonts(platform.os, run, which, house_font),
        _check_node(run, which),
        _check_docker(run, docker, platform.os),
    ]


def _install(platform: Platform, packages: tuple[str, ...]) -> tuple[str, ...]:
    manager = platform.manager
    if manager == "winget":
        # --disable-interactivity: winget asks nothing (it has no keyboard here,
        # kazma_cli.setup._run_step gives it none).
        return ("winget", "install", "--exact", "--silent", "--disable-interactivity",
                "--accept-package-agreements", "--accept-source-agreements", "--id", packages[0])
    if manager == "brew":
        return ("brew", "install", *packages)
    if manager == "apt":
        return ("sudo", "apt-get", "install", "-y", "--no-install-recommends", *packages)
    if manager == "dnf":
        return ("sudo", "dnf", "install", "-y", *packages)
    raise ValueError(f"no package manager for {platform.os}")


#: Platform.os on Linux (``postgres_service.detect_platform``).
_LINUX = ("debian", "fedora", "linux")
#: What ``missing`` names when Docker runs but this account may not use it.
DOCKER_GROUP = "docker group"


def _docker_access_steps(platform: Platform) -> list[Step]:
    """Linux: Docker started at boot (Kazma's engine there is the system's
    Docker service), and this account let use its socket. The group applies
    from the account's next sign-in; Kazma's service, started at boot, has it."""
    if platform.os not in _LINUX:
        return []
    import getpass

    user = getpass.getuser()
    return [Step("start Docker at boot", ("sudo", "systemctl", "enable", "--now", "docker"), dep="docker"),
            Step(f"let {user} use Docker (the docker group; it applies from the next sign-in)",
                 ("sudo", "usermod", "-aG", "docker", user), dep="docker")]


def _freshclam_steps(platform: Platform) -> list[Step]:
    return [replace(step, dep="malware") for step in _freshclam_commands(platform)]


def _freshclam_commands(platform: Platform) -> list[Step]:
    if platform.os == "windows":
        exe = str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "ClamAV" / "freshclam.exe")
        return [Step("refresh the malware signatures daily",
                     ("schtasks", "/Create", "/TN", FRESHCLAM_TASK, "/SC", "DAILY", "/ST", "04:00",
                      "/RL", "HIGHEST", "/TR", f'"{exe}"', "/F"), admin=True)]
    if platform.os == "macos":
        return [Step("refresh the malware signatures daily (launchd)", action="freshclam_agent")]
    service = "clamav-freshclam"
    return [Step("refresh the malware signatures daily", ("sudo", "systemctl", "enable", "--now", service),
                 ok_codes=(0, 1))]


def _house_font_steps(platform: Platform, state: DepState) -> list[Step]:
    """Kazma's document font, from the bundle (``house_font_install``): for all
    users on Windows (administrator rights), the user's font folder elsewhere."""
    from kazma_core.house_font_install import NOT_INSTALLED, PER_USER_COPY

    if NOT_INSTALLED not in state.missing and PER_USER_COPY not in state.missing:
        return []
    windows = platform.os == "windows"
    title = ("install IBM Plex Sans Arabic for all users" + (
        ", retiring the per-user copy" if PER_USER_COPY in state.missing else "")) if windows \
        else "install IBM Plex Sans Arabic"
    shown = ("copy Kazma's bundled IBM Plex Sans Arabic (regular, bold) into the Windows Fonts folder "
             "and list it for all users" + (
                 "; unregister and delete the per-user copy" if PER_USER_COPY in state.missing else "")) \
        if windows else ("copy Kazma's bundled IBM Plex Sans Arabic into " + (
            "~/Library/Fonts" if platform.os == "macos" else "~/.local/share/fonts/kazma, then fc-cache"))
    return [Step(title, action="fonts:house", admin=windows, dep="fonts", shown=shown)]


def install_plan(platform: Platform, states: list[DepState]) -> list[Step]:
    """The steps that put in what *states* found missing, nothing more."""
    if platform.manager is None:
        return []
    table = PACKAGES[platform.manager]
    steps: list[Step] = []
    if platform.manager == "apt" and any(
            s.ok is False and table.get(s.name) and (s.name != "fonts" or "fonts" in s.missing)
            and s.missing != [DOCKER_GROUP] for s in states):
        steps.append(Step("refresh the package lists", ("sudo", "apt-get", "update")))
    for state in states:
        if state.ok is not False:
            continue
        packages = table.get(state.name)
        if state.name == "malware" and state.missing == ["freshclam schedule"]:
            steps.extend(_freshclam_steps(platform))
            continue
        if state.name == "ocr" and platform.manager == "winget" and state.missing != ["tesseract"]:
            steps.extend(Step(f"download Tesseract's {lang} model", action=f"tessdata:{lang}", dep="ocr")
                         for lang in state.missing if lang in TESSDATA)
            continue
        if state.name == "fonts":
            if packages and "fonts" in state.missing:
                steps.append(Step(f"install {LABELS['fonts']}", _install(platform, packages), dep="fonts"))
            steps.extend(_house_font_steps(platform, state))
            continue
        if state.name == "docker" and state.missing == [DOCKER_GROUP]:
            steps.extend(_docker_access_steps(platform))
            continue
        if packages is None:
            continue
        steps.append(Step(f"install {LABELS[state.name]}", _install(platform, packages),
                          admin=platform.manager in ("winget",), dep=state.name))
        if state.name == "docker":
            steps.extend(_docker_access_steps(platform))
        if state.name == "ocr" and platform.manager == "winget":
            steps.extend(Step(f"download Tesseract's {lang} model", action=f"tessdata:{lang}", dep="ocr")
                         for lang in TESSDATA)
        if state.name == "malware":
            steps.extend(_freshclam_steps(platform))
    return steps


#: The name each check puts in ``missing`` while the dependency's own package
#: is not installed (the models and the signature schedule come after it).
_PACKAGE_MISSING = {"vcruntime": "vcruntime", "ocr": "tesseract", "office": "libreoffice", "malware": "clamav",
                    "fonts": "fonts", "node": "node", "docker": "docker"}


def installs_package(step: Step) -> bool:
    """Whether *step* is the one that installs its dependency's package (not a
    model download or the signature schedule that follows it)."""
    return bool(step.dep) and step.title == f"install {LABELS.get(step.dep, step.dep)}"


def package_is_in(state: DepState) -> bool:
    """Whether the dependency's package is installed now, whatever is still
    missing after it. An installer can fail after it installed (winget answers
    a cancelled or interrupted run with an error), so a failed install step is
    judged by a check, never by its exit code alone."""
    return state.ok is not False or _PACKAGE_MISSING.get(state.name, state.name) not in state.missing


def fetch_tessdata(lang: str, folder: Path, *, opener: Callable[..., Any] | None = None) -> Path:
    """Download Tesseract's *lang* model into *folder*, kept only when its
    size and SHA-256 are the pinned ones (:data:`TESSDATA`)."""
    if opener is None:
        from kazma_core.http_tls import open_download as opener
    url, size, digest = TESSDATA[lang]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{lang}.traineddata"
    partial = target.with_suffix(".traineddata.partial")
    hasher = hashlib.sha256()
    got = 0
    with opener(url, timeout=120) as response, open(partial, "wb") as out:  # noqa: S310 -- a pinned https URL
        while chunk := response.read(1 << 16):
            got += len(chunk)
            if got > size:
                break
            hasher.update(chunk)
            out.write(chunk)
    if got != size or hasher.hexdigest() != digest:
        partial.unlink(missing_ok=True)
        raise ValueError(f"{lang}.traineddata did not match its pinned size and SHA-256; nothing was kept")
    os.replace(partial, target)
    return target


def tessdata_folder(which: Callable[[str], str | None] = shutil.which) -> Path | None:
    """Where the Tesseract that is installed reads its models."""
    exe = _tesseract(which)
    return Path(exe).parent / "tessdata" if exe else None


def record_system(states: list[DepState], path: Path | None = None) -> Path:
    """Record what ``kazma setup system`` found (atomically), beside the
    Postgres record: ``{"system": {name: {ok, detail, at}}}``."""
    import json
    import time

    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.postgres_service import _record_path, read_record

    target = path or _record_path()
    data = read_record(target)
    now = time.time()
    data.update({"version": 1, "system": {s.name: {"ok": s.ok, "detail": s.detail, "at": now} for s in states}})
    atomic_write_text(target, json.dumps(data, indent=2))
    return target


def freshclam_agent_plist(freshclam: str) -> str:
    """The macOS launchd job: freshclam every day at 04:00."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{FRESHCLAM_AGENT}</string>
<key>ProgramArguments</key><array><string>{freshclam}</string></array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
</dict></plist>
"""


#: Words in a computer's maker or model that say it is a virtual machine.
_VM_MARKERS = ("virtual machine", "vmware", "virtualbox", "kvm", "qemu", "parallels", "xen")

#: Asks Windows what it is and whether the CPU's virtualization is on, as one
#: JSON line (CIM: no WMIC, which newer Windows no longer ships).
_VIRTUALIZATION_QUERY = (
    "$cs = Get-CimInstance Win32_ComputerSystem; "
    "$p = Get-CimInstance Win32_Processor | Select-Object -First 1; "
    "[pscustomobject]@{model = ($cs.Manufacturer + ' ' + $cs.Model); "
    "hypervisor = [bool]$cs.HypervisorPresent; firmware = [bool]$p.VirtualizationFirmwareEnabled} "
    "| ConvertTo-Json -Compress")


@dataclass
class DockerReadiness:
    """Whether Docker can run here, asked before it is installed: ``ok``
    True, False (it cannot: ``problem`` says why and what to do) or None
    (cannot tell: ``note`` says what to check). ``wsl_missing``: Windows needs
    WSL 2 first, and a restart after it."""

    ok: bool | None
    problem: str = ""
    note: str = ""
    wsl_missing: bool = False


def docker_readiness(platform: Platform, *, run: Runner = subprocess.run) -> DockerReadiness:
    """What stands between this machine and a running Docker engine.

    On Windows Docker Desktop runs its engine in WSL 2, which needs the CPU's
    virtualization: turned on in the firmware, and passed through by the host
    when Windows is itself a virtual machine (the drill VM, 2026-11-07: no
    WSL could start there, and Docker Desktop asked for one at every sign-in).
    Elsewhere Docker runs on the kernel: nothing to ask before the install.
    With a hypervisor running (Hyper-V on), Windows no longer reports the
    firmware's switch, so only a switch reported off is a refusal."""
    if platform.os != "windows":
        return DockerReadiness(True)
    import json

    code, text = _out(run, ["powershell", "-NoProfile", "-NonInteractive", "-Command", _VIRTUALIZATION_QUERY])
    facts: dict[str, Any] = {}
    if code == 0:
        lines = [line for line in text.splitlines() if line.strip().startswith("{")]
        try:
            facts = json.loads(lines[-1]) if lines else {}
        except ValueError:
            facts = {}
    wsl_code, _ = _out(run, ["wsl", "--status"])
    wsl_missing = wsl_code != 0
    model = str(facts.get("model") or "").lower()
    if any(marker in model for marker in _VM_MARKERS):
        return DockerReadiness(None, note=(
            "this Windows is a virtual machine: Docker runs here only when its host passes "
            "virtualization through (Hyper-V, with the VM off: Set-VMProcessor -VMName <name> "
            "-ExposeVirtualizationExtensions $true)"), wsl_missing=wsl_missing)
    if facts and not facts.get("hypervisor") and facts.get("firmware") is False:
        return DockerReadiness(False, problem=(
            "virtualization is turned off in this computer's firmware (BIOS/UEFI): turn on Intel VT-x "
            "or AMD-V there, then run setup again"), wsl_missing=wsl_missing)
    if not facts:
        return DockerReadiness(None, note="Windows did not say whether virtualization is on",
                               wsl_missing=wsl_missing)
    return DockerReadiness(True, wsl_missing=wsl_missing)

