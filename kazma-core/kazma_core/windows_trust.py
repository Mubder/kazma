"""Kazma and Windows' file check (Smart App Control, App Control for Business).

Live 2026-11-26, from 10:56 UTC: Windows began refusing unsigned programs and
libraries on the owner's PC (``WinError 4551``, "An Application Control
policy has blocked this file"): Kazma's own engine's ``docker.exe``, and at
the 11:30 reload psycopg's bundled client library, so Kazma started
read-only for thirty minutes. Smart App Control has no per-file exceptions,
and a user cannot be asked to turn a protection off. Kazma works WITH it:
each native part it loads or starts is checked, a signed alternative is used
where one exists (``libpq_reach``, ``docker_cli``), and the guard checks the
new code before a reload swaps servers (``kazma_guard.run_reload_preflight``).

This module is the one place that knows, quietly:

* :func:`smart_app_control` -- Windows' own setting
  (``HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Policy``
  ``VerifiedAndReputablePolicyState``: 0 off, 1 on, 2 evaluation).
* :func:`check` -- every native part Kazma itself installs, loads or starts
  (:data:`PARTS`), each ``allowed``, ``alternative`` (Windows did not allow
  it; Kazma uses a signed alternative), ``waits`` (no alternative here: the
  feature waits for a signed version), or ``absent`` (not on this machine).
* :func:`lines` -- what ``kazma doctor``, ``kazma setup`` and the security
  report show, in English or Arabic (:data:`WORDS`, equal in tone).
* :func:`report_at_boot` -- one INFO line at boot, and the one alert
  (``windows.feature_waits``) only when a feature has no signed alternative.

The owner's rule for every word here (2026-11-26): never suggest turning a
Windows protection off, never show it as an obstacle. Say what Windows
checked, what Kazma used instead, that nothing is wrong with the PC, and,
when a feature truly cannot run, which feature and that an update brings a
signed version. Smart App Control being on is good news, never a finding.

Every binary Kazma installs itself is a part here or declared in
:data:`NOT_PROBED` with why (``tests/test_windows_trust.py`` reads the engine
setup's downloads).
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - read-only queries (sc.exe) through the engine setup's runner
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "ALERT_KEY",
    "NOT_PROBED",
    "PARTS",
    "WORDS",
    "PartState",
    "TrustReport",
    "check",
    "lines",
    "notes",
    "report_at_boot",
    "smart_app_control",
]

#: The one alert: a feature has no signed alternative on this PC.
ALERT_KEY = "windows.feature_waits"

ALLOWED = "allowed"
ALTERNATIVE = "alternative"
#: While Smart App Control checks every file, the signed copy was chosen up
#: front and the unsigned one never tried (``libpq_reach``, ``docker_cli``).
PREFERRED = "preferred"
WAITS = "waits"
ABSENT = "absent"

#: Every phrase shown, in English and Arabic, equal in tone (``{part}``,
#: ``{alt}``, ``{feature}`` filled in by :func:`lines`).
WORDS: dict[str, dict[str, str]] = {
    "state.on": {
        "en": "Smart App Control is on: Windows checks each program and library before it runs, "
              "and Kazma works with it.",
        "ar": "التحكم الذكي في التطبيقات مفعّل: يفحص Windows كل برنامج ومكتبة قبل تشغيلها، "
              "ويعمل كاظمه معه.",
    },
    "state.evaluation": {
        "en": "Smart App Control is learning how this PC is used before it starts checking files; "
              "Kazma works with it either way.",
        "ar": "التحكم الذكي في التطبيقات يتعرّف على طريقة استخدام هذا الجهاز قبل أن يبدأ فحص الملفات، "
              "ويعمل كاظمه معه في الحالتين.",
    },
    "state.off": {
        "en": "Smart App Control is not checking files on this PC.",
        "ar": "التحكم الذكي في التطبيقات لا يفحص الملفات على هذا الجهاز.",
    },
    ALLOWED: {
        "en": "{part}: Windows allows it.",
        "ar": "{part}: يسمح به Windows.",
    },
    ALTERNATIVE: {
        "en": "{part}: Windows checked it and did not allow it on this PC, so Kazma uses the signed "
              "alternative ({alt}). Everything works.",
        "ar": "{part}: فحصه Windows ولم يسمح به على هذا الجهاز، فاستخدم كاظمه البديل الموقَّع "
              "({alt}). كل شيء يعمل.",
    },
    PREFERRED: {
        "en": "{part}: while Windows checks each file, Kazma uses the signed one ({alt}). Everything works.",
        "ar": "{part}: بينما يفحص Windows كل ملف، يستخدم كاظمه النسخة الموقَّعة ({alt}). كل شيء يعمل.",
    },
    WAITS: {
        "en": "{part}: Windows checked it and did not allow it on this PC, and no signed alternative "
              "is installed here. {feature} waits until an update brings a signed version; "
              "everything else keeps working, and nothing is wrong with the PC.",
        "ar": "{part}: فحصه Windows ولم يسمح به على هذا الجهاز، ولا يوجد هنا بديل موقَّع. "
              "تنتظر ميزة «{feature}» تحديثًا يأتي بنسخة موقَّعة؛ ويستمر كل ما عداها في العمل، "
              "ولا خلل في الجهاز.",
    },
    "alert.detail": {
        "en": "{part}: Windows checked it and did not allow it on this PC, and no signed alternative "
              "is installed here. Waiting for an update with a signed version: {feature}. "
              "Everything else keeps working, and nothing is wrong with the PC.",
        "ar": "{part}: فحصه Windows ولم يسمح به على هذا الجهاز، ولا يوجد هنا بديل موقَّع. "
              "ميزة تنتظر تحديثًا يأتي بنسخة موقَّعة: «{feature}». ويستمر كل ما عداها في العمل، "
              "ولا خلل في الجهاز.",
    },
}


@dataclass(frozen=True)
class Part:
    """A native part Kazma installs, loads or starts itself."""

    id: str
    #: What it is, as the owner reads it (en, ar).
    name: tuple[str, str]
    #: What waits when Windows does not allow it and nothing else can stand in.
    feature: tuple[str, str]


PARTS: tuple[Part, ...] = (
    Part("database_client",
         ("The database client library", "مكتبة الاتصال بقاعدة البيانات"),
         ("Reaching the database", "الوصول إلى قاعدة البيانات")),
    Part("docker_cli",
         ("Kazma's Docker command-line tool", "أداة Docker لسطر الأوامر الخاصة بكاظمه"),
         ("Running code and containers", "تشغيل الشيفرة والحاويات")),
    Part("compose",
         ("Docker Compose", "Docker Compose"),
         ("Setting up Kazma's container services", "إعداد خدمات الحاويات في كاظمه")),
    Part("engine_shutdown",
         ("The engine's shutdown service", "خدمة إيقاف المحرك"),
         ("A clean stop of the container engine when Windows shuts down",
          "إيقاف محرك الحاويات بسلام عند إيقاف تشغيل Windows")),
)
_BY_ID = {part.id: part for part in PARTS}

#: What Kazma's engine setup installs that is not probed here, and why.
#: Keyed by ``container_engine_setup.DOWNLOADS`` keys.
NOT_PROBED: dict[str, str] = {
    "wsl": "Microsoft's own WSL package, signed by Microsoft",
    "ubuntu": "a Linux image the engine runs inside WSL; Windows never runs it as a program",
}
#: The downloads each part stands for (``docker_cli``: the static zip's docker.exe).
DOWNLOAD_PARTS: dict[str, str] = {"docker_cli": "docker_cli", "compose": "compose"}


@dataclass(frozen=True)
class PartState:
    """One part as found: its status, the files Windows checked, the
    alternative in use, and the operator's detail."""

    id: str
    status: str
    files: tuple[str, ...] = ()
    alternative: str = ""
    detail: str = ""


@dataclass(frozen=True)
class TrustReport:
    """Windows' setting and every part's state."""

    smart_app_control: str | None
    parts: tuple[PartState, ...] = field(default_factory=tuple)

    def waiting(self) -> list[PartState]:
        return [p for p in self.parts if p.status == WAITS]

    def as_dict(self) -> dict[str, object]:
        return {"smart_app_control": self.smart_app_control,
                "parts": [{"id": p.id, "status": p.status, "files": list(p.files),
                           "alternative": p.alternative, "detail": p.detail} for p in self.parts]}


def _on_windows() -> bool:
    return os.name == "nt"


def smart_app_control() -> str | None:
    """``on``, ``off`` or ``evaluation`` from Windows' own setting
    (:func:`kazma_core.windows_policy.smart_app_control`, the one reader)."""
    from kazma_core.windows_policy import smart_app_control as read

    return read()


# ── each part ────────────────────────────────────────────────────────────


def _database_client() -> PartState:
    from kazma_core import libpq_reach

    files = libpq_reach.bundled_files()
    if not files:
        return PartState("database_client", ABSENT)
    folder = libpq_reach.added_folder()
    if libpq_reach.preferred() and folder is not None:
        return PartState("database_client", PREFERRED, tuple(f.name for f in files), str(folder / "libpq.dll"))
    refused = [(f, code) for f in files if (code := libpq_reach.load_error(f)) in libpq_reach.REFUSALS]
    if not refused:
        return PartState("database_client", ALLOWED, tuple(f.name for f in files))
    names = tuple(f.name for f, _ in refused)
    detail = "; ".join(f"{f.name}: WinError {code}" for f, code in refused)
    candidates = ([libpq_reach.added_folder()] if libpq_reach.added_folder() else []) + [
        d for d in (Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
        if (d / "libpq.dll").is_file()] + libpq_reach.postgres_libpq_folders()
    for folder in candidates:
        if folder is not None and libpq_reach.load_error(folder / "libpq.dll") is None:
            return PartState("database_client", ALTERNATIVE, names, str(folder / "libpq.dll"), detail)
    return PartState("database_client", WAITS, names, detail=detail)


def _docker_cli() -> PartState:
    from kazma_core import docker_cli

    own = docker_cli.kazma_engine_dir() / "bin" / "docker.exe"
    if not own.is_file():
        return PartState("docker_cli", ABSENT)
    if _checks_files():
        chosen = docker_cli.find_docker_cli()
        if chosen and Path(chosen) != own:
            return PartState("docker_cli", PREFERRED, (own.name,), chosen)
    why = docker_cli.refused_by_os(own)
    if why is None:
        return PartState("docker_cli", ALLOWED, (own.name,))
    other = docker_cli.find_docker_cli()
    if other and Path(other) != own:
        return PartState("docker_cli", ALTERNATIVE, (own.name,), other, why)
    return PartState("docker_cli", WAITS, (own.name,), detail=why)


def _checks_files() -> bool:
    from kazma_core.windows_policy import checks_files

    return checks_files()


def _compose() -> PartState:
    from kazma_core import docker_cli
    from kazma_core.container_engine_setup import compose_plugin

    plugin = compose_plugin()
    if not plugin.is_file():
        return PartState("compose", ABSENT)
    why = docker_cli.refused_by_os(plugin)
    if why is None:
        return PartState("compose", ALLOWED, (plugin.name,))
    return PartState("compose", WAITS, (plugin.name,), detail=why)


def _engine_shutdown(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> PartState:
    from kazma_core.container_engine_setup import shutdown_service_program, shutdown_service_refusal

    program = shutdown_service_program()
    if not program.is_file():
        return PartState("engine_shutdown", ABSENT)
    code = shutdown_service_refusal(run)
    if code is None:
        return PartState("engine_shutdown", ALLOWED, (program.name,))
    return PartState("engine_shutdown", WAITS, (program.name,), detail=f"WinError {code} (the service's last start)")


_PROBES: dict[str, Callable[[], PartState]] = {
    "database_client": _database_client,
    "docker_cli": _docker_cli,
    "compose": _compose,
    "engine_shutdown": _engine_shutdown,
}


def check() -> TrustReport:
    """Windows' setting and every part's state. Blocking (it loads libraries
    and starts ``--version`` of a CLI): off the event loop. Elsewhere than
    Windows nothing is probed. A part whose probe fails is left out and
    said at DEBUG: a check that cannot run says nothing it does not know."""
    if not _on_windows():
        return TrustReport(None)
    states: list[PartState] = []
    for part in PARTS:
        try:
            states.append(_PROBES[part.id]())
        except (OSError, ImportError, ValueError, subprocess.SubprocessError):
            logger.debug("[windows] could not check %s", part.id, exc_info=True)
    return TrustReport(smart_app_control(), tuple(states))


def _say(key: str, lang: str, **fill: str) -> str:
    return WORDS[key]["ar" if lang == "ar" else "en"].format(**fill)


def lines(report: TrustReport, lang: str = "en") -> list[tuple[str, str]]:
    """What to show: ``(status, text)`` per line, ``status`` being ``state``,
    ``allowed``, ``alternative`` or ``waits``. Parts not on this machine are
    left out; a machine with no Smart App Control setting shows the parts
    alone."""
    index = 1 if lang == "ar" else 0
    out: list[tuple[str, str]] = []
    if report.smart_app_control:
        out.append(("state", _say(f"state.{report.smart_app_control}", lang)))
    for state in report.parts:
        if state.status == ABSENT:
            continue
        part = _BY_ID[state.id]
        out.append((state.status, _say(state.status, lang, part=part.name[index], alt=state.alternative,
                                       feature=part.feature[index])))
    return out


def notes(report: TrustReport, lang: str = "en") -> list[str]:
    """What setup mentions, as notes: Smart App Control when it checks files
    (or is learning to), and each part Windows did not allow, with what Kazma
    uses instead or which feature waits. Nothing on a PC where every part is
    allowed and nothing is checked: setup has nothing to say then."""
    checking = report.smart_app_control in ("on", "evaluation")
    return [text for status, text in lines(report, lang)
            if status in (ALTERNATIVE, PREFERRED, WAITS) or (status == "state" and checking)]


def _alert_detail(state: PartState, lang: str = "en") -> str:
    """The alert's words for a part that waits."""
    part = _BY_ID[state.id]
    index = 1 if lang == "ar" else 0
    return _say("alert.detail", lang, part=part.name[index], feature=part.feature[index])


def report_at_boot() -> TrustReport:
    """Run :func:`check` once at boot: one INFO line for what Windows checked
    and what Kazma used instead; the one alert only for a feature with no
    signed alternative (at WARNING, naming it). Blocking: run in a worker
    thread."""
    report = check()
    if not report.parts and report.smart_app_control is None:
        return report
    shown = [f"{p.id}={p.status}" + (f" ({p.alternative})" if p.alternative else "")
             for p in report.parts if p.status != ABSENT]
    logger.info("[windows] Smart App Control: %s; Kazma's native parts: %s",
                report.smart_app_control or "not on this Windows", ", ".join(shown) or "none here")
    waiting = report.waiting()
    if waiting:
        from kazma_core.observability.ops_alerts import alert

        details = [_alert_detail(state) + (f" Detail: {state.detail}" if state.detail else "")
                   for state in waiting]
        for text in details:
            logger.warning("[windows] %s", text)
        alert("windows.feature_waits", "Kazma could not keep a feature working on this PC",
              "\n".join(details), severity="warn")
    return report
