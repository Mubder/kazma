"""``kazma self-check`` — one read-only report of what already needs attention.

It calls the checks Kazma already has, and it does not run at startup. A check
that did not run is reported as not run. It is never counted as a pass, and
the command exits 1 when any check needs attention or did not run.

The lines a called check already wrote (``kazma doctor``, the security report,
the backup drill, a skill's own reason) stay in that check's words. The
verdict of each section is written in English and Arabic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ATTENTION",
    "NOT_RUN",
    "OK",
    "SECTION_ORDER",
    "Item",
    "ReleaseArtifact",
    "Report",
    "Section",
    "collect",
    "render",
    "run",
]

OK = "ok"
ATTENTION = "attention"
NOT_RUN = "not_run"
_STATUSES = frozenset({OK, ATTENTION, NOT_RUN})

SECTION_ORDER = ("health", "security", "backup", "skills", "release", "gaps")

_TITLES: dict[str, tuple[str, str]] = {
    "health": ("Health", "السلامة"),
    "security": ("Security report", "تقرير الأمان"),
    "backup": ("Latest backup", "أحدث نسخة احتياطية"),
    "skills": ("Skill signatures", "تواقيع المهارات"),
    "release": ("Release signature", "توقيع الإصدار"),
    "gaps": ("Known gaps for these settings", "الثغرات المعروفة لهذه الإعدادات"),
}

_STATUS_WORDS: dict[str, tuple[str, str]] = {
    OK: ("ok", "سليم"),
    ATTENTION: ("needs attention", "يحتاج انتباهاً"),
    NOT_RUN: ("not run", "لم يُجرَ"),
}

#: What ``skill_integrity`` returns when its own check raises (catalog.py).
_INTEGRITY_FAILED = "the integrity check failed:"


@dataclass
class Item:
    """One line inside a section."""

    status: str
    title_en: str
    title_ar: str
    detail_en: str = ""
    detail_ar: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "title": {"en": _mask(self.title_en), "ar": _mask(self.title_ar)},
            "detail": {"en": _mask(self.detail_en), "ar": _mask(self.detail_ar)},
        }


@dataclass
class Section:
    """One of the six checks."""

    id: str
    status: str
    title_en: str
    title_ar: str
    detail_en: str
    detail_ar: str
    items: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "title": {"en": _mask(self.title_en), "ar": _mask(self.title_ar)},
            "detail": {"en": _mask(self.detail_en), "ar": _mask(self.detail_ar)},
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class Report:
    """The whole self-check."""

    sections: list[Section]

    @property
    def needs_attention(self) -> bool:
        """True when a check needs a person, or did not run."""
        return any(section.status != OK for section in self.sections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_attention": self.needs_attention,
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(frozen=True)
class ReleaseArtifact:
    """What this install still has of a signed release wheel."""

    kind: str
    version: str = ""
    digest: bytes | None = None
    bundle: bytes | None = None


def _mask(text: str) -> str:
    """A report line with any URL password removed. The doctor's rows already pass this."""
    from kazma_core.security.url_credentials import mask_urls_in_text

    return mask_urls_in_text(text or "")


def _section(
    section_id: str,
    status: str,
    detail_en: str,
    detail_ar: str,
    items: list[Item],
) -> Section:
    title_en, title_ar = _TITLES[section_id]
    return Section(section_id, status, title_en, title_ar, detail_en, detail_ar, items)


def _rollup(items: Sequence[Item], *, empty: str) -> str:
    """The section status. An empty list uses *empty* (not run, or a finished check with nothing in it)."""
    statuses = {item.status for item in items}
    if ATTENTION in statuses:
        return ATTENTION
    if NOT_RUN in statuses:
        return NOT_RUN
    if not items:
        return empty
    return OK


def _not_run(section_id: str, detail_en: str, detail_ar: str) -> Section:
    return _section(section_id, NOT_RUN, detail_en, detail_ar, [])


def _not_run_exc(section_id: str, exc: BaseException) -> Section:
    """A check that raised. The person sees a code and a reference, not the exception's text."""
    from kazma_core.errors import described_error

    info = described_error("This check did not run", exc, level=logging.WARNING)
    return _not_run(
        section_id,
        str(info),
        f"هذا الفحص لم يُجرَ ({info.code}, ref: {info.ref}).",
    )


def _guard(section_id: str, fn: Callable[[], Section]) -> Section:
    from kazma_cli.known_gap_settings import _FAILURES

    try:
        section = fn()
    except _FAILURES as exc:
        return _not_run_exc(section_id, exc)
    if section.status not in _STATUSES:
        return _not_run(
            section_id,
            "This check returned a status this command does not know, so it was not counted as passed.",
            "أعاد هذا الفحص حالة لا يعرفها هذا الأمر، فلم يُحسب ناجحاً.",
        )
    return section


def _health_from(rows: Sequence[tuple[str, str]]) -> Section:
    """Map ``kazma doctor`` rows. ``info`` is shown and does not need attention.

    No rows means the health check produced nothing: not run, not a pass.
    """
    from kazma_cli.doctor import BAD, INFO, WARN
    from kazma_cli.doctor import OK as DOCTOR_OK

    items: list[Item] = []
    for status, rendered in rows:
        if status in (DOCTOR_OK, INFO):
            item_status = OK
        elif status in (WARN, BAD):
            item_status = ATTENTION
        else:
            item_status = NOT_RUN
        items.append(Item(item_status, rendered, rendered))
    status = _rollup(items, empty=NOT_RUN)
    if status == OK:
        detail_en = "Nothing in the health checks needs attention."
        detail_ar = "لا شيء في فحوص السلامة يحتاج انتباهاً."
    elif status == ATTENTION:
        detail_en = "A health check needs attention."
        detail_ar = "فحص من فحوص السلامة يحتاج انتباهاً."
    else:
        detail_en = "The health checks did not run."
        detail_ar = "فحوص السلامة لم تُجرَ."
    if items:
        detail_en += "\nThe lines below are kazma doctor's own words."
        detail_ar += "\nالأسطر التالية هي كلام kazma doctor كما هو."
    return _section("health", status, detail_en, detail_ar, items)


def _health() -> Section:
    from kazma_cli.doctor import report_sections

    rows = [row for _title, group in report_sections() for row in group]
    return _health_from(rows)


def _security_from(report: object) -> Section:
    """Map a :class:`HardeningReport`. A check with ``ran`` false is not run, even if ``passed`` is true."""
    checks = list(getattr(report, "checks", None) or [])
    items: list[Item] = []
    for check in checks:
        ran = bool(getattr(check, "ran", True))
        if not ran:
            item_status = NOT_RUN
        elif bool(getattr(check, "passed", False)):
            item_status = OK
        else:
            item_status = ATTENTION
        name = str(getattr(check, "name", "") or "check")
        message = str(getattr(check, "message", "") or "")
        items.append(Item(item_status, name, name, message, message))
    status = _rollup(items, empty=NOT_RUN)
    if status == OK:
        detail_en = "The security report found nothing that needs attention."
        detail_ar = "تقرير الأمان لم يجد ما يحتاج انتباهاً."
    elif status == ATTENTION:
        detail_en = "The security report needs attention."
        detail_ar = "تقرير الأمان يحتاج انتباهاً."
    else:
        detail_en = "A security check did not run, so this is not a pass."
        detail_ar = "فحص أمان لم يُجرَ، فهذه ليست نتيجة نجاح."
    if items:
        detail_en += "\nThe lines below are the security report's own words."
        detail_ar += "\nالأسطر التالية هي كلام تقرير الأمان كما هو."
    return _section("security", status, detail_en, detail_ar, items)


def _security() -> Section:
    import asyncio

    from kazma_core.paths import get_project_root, installed_project_root
    from kazma_core.security.hardening import SecurityHardeningRunner

    root = installed_project_root() or get_project_root()
    report = asyncio.run(SecurityHardeningRunner(root).run_all_checks())
    return _security_from(report)


def _backup_from(result: object) -> Section:
    """Map a :class:`DrillResult`. ``UNVERIFIED`` and an empty result are not run, never a pass."""
    from kazma_core.backup.restore_drill import DrillResult

    if not isinstance(result, DrillResult) or not result.checks:
        return _not_run(
            "backup",
            "The backup drill recorded nothing, so this is not a pass.",
            "فحص النسخة الاحتياطية لم يسجّل شيئاً، فهذه ليست نتيجة نجاح.",
        )
    evidence = result.summary()
    verdict = result.verdict
    if verdict == "PASS":
        status = OK
        detail_en = "The latest backup can be read back. The drill's own words follow."
        detail_ar = "أحدث نسخة احتياطية يمكن قراءة محتواها. كلام الفحص كما هو يلي."
    elif verdict == "FAIL":
        status = ATTENTION
        detail_en = "The latest backup cannot be restored from what the drill read. The drill's own words follow."
        detail_ar = "أحدث نسخة احتياطية لا تُستعاد مما قرأه الفحص. كلام الفحص كما هو يلي."
    else:
        status = NOT_RUN
        detail_en = "The backup drill did not finish every check, so this is not a pass. The drill's own words follow."
        detail_ar = "فحص النسخة الاحتياطية لم يُكمل كل بنوده، فهذه ليست نتيجة نجاح. كلام الفحص كما هو يلي."
    return _section("backup", status, detail_en, detail_ar, [Item(status, evidence, evidence)])


def _backup() -> Section:
    from kazma_core.backup.restore_drill import run_drill

    return _backup_from(run_drill())


def _skill_status(state: str, reason: str) -> str:
    if state == "verified":
        return OK
    if state == "refused":
        return ATTENTION
    if state == "unsigned" and str(reason or "").startswith(_INTEGRITY_FAILED):
        return NOT_RUN
    if state == "unsigned":
        return ATTENTION
    return NOT_RUN


def _skill_title(name: str, state: str, status: str) -> tuple[str, str]:
    if status == NOT_RUN:
        return (f"{name}: the signature check did not run", f"{name}: فحص التوقيع لم يُجرَ")
    if state == "verified":
        return (f"{name}: signed and unchanged", f"{name}: موقّعة ولم تتغيّر")
    if state == "refused":
        return (f"{name}: signature refused", f"{name}: التوقيع مرفوض")
    return (f"{name}: no signature", f"{name}: بلا توقيع")


def _skills_from(rows: Sequence[tuple[str, str, str]]) -> Section:
    """Map ``(name, state, reason)`` from :func:`skill_integrity`.

    No installed skills is a finished check: nothing was counted as signed.
    """
    items = [
        Item(_skill_status(state, reason), *_skill_title(name, state, _skill_status(state, reason)), reason, reason)
        for name, state, reason in rows
    ]
    status = _rollup(items, empty=OK)
    if status == OK and not items:
        detail_en = "No agent skills are installed. Nothing was signed, and nothing was counted as signed."
        detail_ar = "لا مهارات مثبّتة. لم يُوقَّع شيء، ولم يُحسب شيء على أنه موقّع."
    elif status == OK:
        detail_en = "Every installed skill's signature holds."
        detail_ar = "توقيع كل مهارة مثبّتة ثابت."
    elif status == ATTENTION:
        detail_en = "A skill has no signature, or its signature was refused."
        detail_ar = "مهارة بلا توقيع، أو رُفض توقيعها."
    else:
        detail_en = "A skill's signature check did not run, so this is not a pass."
        detail_ar = "فحص توقيع مهارة لم يُجرَ، فهذه ليست نتيجة نجاح."
    return _section("skills", status, detail_en, detail_ar, items)


def _skills() -> Section:
    from kazma_core.agent_skills.catalog import skill_integrity
    from kazma_core.agent_skills.discovery import discover_skills

    skills = discover_skills(include_disabled=True)
    rows = []
    for skill in sorted(skills.values(), key=lambda item: item.name):
        state, reason = skill_integrity(skill)
        rows.append((skill.name, state, reason))
    return _skills_from(rows)


def _sigstore_present() -> bool:
    import importlib.util

    return importlib.util.find_spec("sigstore") is not None


def _release_from(artifact: ReleaseArtifact, checker: Callable[[bytes, bytes], str]) -> Section:
    """Check a saved wheel bundle with ``checker`` (``release_signature.check_bundle``).

    A source checkout, a missing bundle, or a missing sigstore package did not
    run the check. None of those is a pass. ``checker`` is not called then.
    """
    from kazma_cli.known_gap_settings import _FAILURES
    from kazma_cli.release_signature import SIGNED_FROM, SignatureRefused, must_be_signed

    signed_from = ".".join(str(part) for part in SIGNED_FROM)
    older = ""
    older_ar = ""
    if artifact.version and not must_be_signed(artifact.version):
        older = f" This version is older than {signed_from}, the first release that must be signed."
        older_ar = f" هذا الإصدار أقدم من {signed_from}، وهو أول إصدار يجب أن يكون موقّعاً."
    if artifact.kind == "missing":
        return _not_run(
            "release",
            "Kazma's installed distribution could not be read, so the release signature was not checked." + older,
            "تعذّر قراءة حزمة كاظمه المثبّتة، فلم يُفحص توقيع الإصدار." + older_ar,
        )
    if artifact.kind == "editable":
        return _not_run(
            "release",
            "This install is a source checkout, not a signed release wheel, so the release signature was not checked."
            + older,
            "هذا التثبيت نسخة مصدر وليست حزمة إصدار موقّعة، فلم يُفحص توقيع الإصدار." + older_ar,
        )
    if artifact.kind != "ready" or artifact.digest is None or artifact.bundle is None:
        return _not_run(
            "release",
            "This install has no saved signature bundle, so the release signature was not checked again." + older,
            "هذا التثبيت لا يحتفظ بحزمة التوقيع، فلم يُفحص توقيع الإصدار مرة أخرى." + older_ar,
        )
    if not _sigstore_present():
        return _not_run(
            "release",
            "The sigstore package is not installed here, so the release signature was not checked.",
            "حزمة sigstore غير مثبّتة هنا، فلم يُفحص توقيع الإصدار.",
        )
    try:
        identity = checker(artifact.digest, artifact.bundle)
    except SignatureRefused as exc:
        return _section(
            "release",
            ATTENTION,
            f"The release signature does not hold. The checker's own words: {exc}",
            f"توقيع الإصدار غير ثابت. كلام الفحص كما هو: {exc}",
            [],
        )
    except _FAILURES as exc:
        return _not_run_exc("release", exc)
    return _section(
        "release",
        OK,
        f"The release signature holds. Signed by: {identity}",
        f"توقيع الإصدار ثابت. الموقّع: {identity}",
        [],
    )


def _distribution() -> object:
    from importlib.metadata import distribution

    return distribution("kazma")


def _local_wheel(url: str) -> Path | None:
    """A ``file:`` URL of a wheel on this machine. Any other URL is not fetched."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    raw = unquote(parsed.path)
    if os.name == "nt" and raw.startswith("/") and len(raw) >= 4 and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw)
    if path.suffix != ".whl":
        return None
    return path


def _find_release_artifact() -> ReleaseArtifact:
    """The installed ``kazma`` distribution's wheel and Sigstore bundle, if both are still on disk.

    ``kazma update`` checks the bundle and does not keep it. A source checkout
    and a wheel install without the bundle come back as not ready.
    """
    from importlib.metadata import PackageNotFoundError

    try:
        dist = _distribution()
    except PackageNotFoundError:
        return ReleaseArtifact("missing")
    version = str(getattr(dist, "version", "") or "")
    read_text = getattr(dist, "read_text", None)
    raw = None
    if read_text is not None:
        try:
            raw = read_text("direct_url.json")
        except (OSError, FileNotFoundError, TypeError, ValueError):
            raw = None
    if not raw:
        return ReleaseArtifact("no_bundle", version)
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return ReleaseArtifact("no_bundle", version)
    if not isinstance(info, dict):
        return ReleaseArtifact("no_bundle", version)
    if isinstance(info.get("dir_info"), dict):
        return ReleaseArtifact("editable", version)
    wheel = _local_wheel(str(info.get("url") or ""))
    if wheel is None or not wheel.is_file():
        return ReleaseArtifact("no_bundle", version)
    bundle_path = Path(str(wheel) + ".sigstore.json")
    if not bundle_path.is_file():
        return ReleaseArtifact("no_bundle", version)
    try:
        digest = hashlib.sha256(wheel.read_bytes()).digest()
        bundle = bundle_path.read_bytes()
    except OSError:
        return ReleaseArtifact("no_bundle", version)
    if not bundle:
        return ReleaseArtifact("no_bundle", version)
    return ReleaseArtifact("ready", version, digest, bundle)


def _release() -> Section:
    from kazma_cli.release_signature import check_bundle

    return _release_from(_find_release_artifact(), check_bundle)


def _gaps_from(judgements: Sequence[object]) -> Section:
    """Map :func:`judge_rules`. No applicable gap, and none unjudged, is the clear result."""
    items = [
        Item(
            str(getattr(item, "status", NOT_RUN)),
            str(getattr(item, "title_en", "")),
            str(getattr(item, "title_ar", "")),
            str(getattr(item, "detail_en", "")),
            str(getattr(item, "detail_ar", "")),
        )
        for item in judgements
    ]
    for item in items:
        if item.status not in _STATUSES or item.status == OK:
            item.status = NOT_RUN
    if any(item.status == ATTENTION for item in items):
        status = ATTENTION
        detail_en = "At least one known gap applies to this install's settings."
        detail_ar = "ثغرة معروفة واحدة على الأقل تنطبق على إعدادات هذا التثبيت."
    elif any(item.status == NOT_RUN for item in items):
        status = NOT_RUN
        detail_en = "A known gap could not be judged, so this is not a pass."
        detail_ar = "تعذّر الحكم على ثغرة معروفة، فهذه ليست نتيجة نجاح."
    else:
        status = OK
        detail_en = "None of the setting-conditional known gaps apply to this install."
        detail_ar = "لا تنطبق على هذا التثبيت أي ثغرة معروفة مربوطة بإعداد."
    return _section("gaps", status, detail_en, detail_ar, items)


def _gaps() -> Section:
    from kazma_cli.known_gap_settings import gaps_path, judge_rules

    path = gaps_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        info_section = _not_run_exc("gaps", exc)
        return _not_run(
            "gaps",
            "The known-gaps page could not be read, so none were judged. " + info_section.detail_en,
            "تعذّرت قراءة صفحة الثغرات المعروفة، فلم يُحكم على أي منها. " + info_section.detail_ar,
        )
    return _gaps_from(judge_rules(text))


def _collect() -> Report:
    builders = {
        "health": _health,
        "security": _security,
        "backup": _backup,
        "skills": _skills,
        "release": _release,
        "gaps": _gaps,
    }
    return Report([_guard(name, builders[name]) for name in SECTION_ORDER])


def collect() -> Report:
    """The report, inside the read-only diagnostic. Writes to stores are refused."""
    from kazma_core.diagnostic_scope import read_only_diagnostic

    with read_only_diagnostic("kazma self-check"):
        return _collect()


def render(report: Report) -> str:
    """The report for a person, English and Arabic."""
    lines = ["Kazma self-check", "فحص كاظمه", ""]
    for section in report.sections:
        word_en, word_ar = _STATUS_WORDS[section.status]
        lines.append(f"{_mask(section.title_en)} — {word_en}")
        lines.append(f"{_mask(section.title_ar)} — {word_ar}")
        if section.detail_en:
            lines.append(_mask(section.detail_en))
        if section.detail_ar:
            lines.append(_mask(section.detail_ar))
        for item in section.items:
            item_en, item_ar = _STATUS_WORDS[item.status]
            lines.append(f"  {item_en} — {_mask(item.title_en)}")
            if item.title_ar != item.title_en:
                lines.append(f"  {item_ar} — {_mask(item.title_ar)}")
            if item.detail_en:
                lines.append(f"    {_mask(item.detail_en)}")
            if item.detail_ar and item.detail_ar != item.detail_en:
                lines.append(f"    {_mask(item.detail_ar)}")
        lines.append("")
    if report.needs_attention:
        lines.append("Something needs attention. This command exits 1.")
        lines.append("شيء يحتاج انتباهاً. هذا الأمر يخرج بالرمز 1.")
    else:
        lines.append("Nothing needs attention.")
        lines.append("لا شيء يحتاج انتباهاً.")
    return "\n".join(lines)


def run(argv: list[str] | None = None) -> int:
    """Print the report. Exit 1 when something needs attention or a check did not run."""
    from kazma_cli.main import _print_utf8

    _print_utf8()
    parser = argparse.ArgumentParser(
        prog="kazma self-check",
        description="Read-only report of health, the security report, the latest backup, "
        "skill signatures, the release signature, and known gaps that match this install's settings.",
    )
    parser.add_argument(
        "--json", action="store_true", help="print one JSON object (English and Arabic) and nothing else"
    )
    try:
        args = parser.parse_args([] if argv is None else argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    report = collect()
    if args.json:
        json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(report))
    return 1 if report.needs_attention else 0
