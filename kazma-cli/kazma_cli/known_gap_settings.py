"""Which open entries in ``docs/KNOWN_GAPS.md`` match this install's settings.

The page is prose. Each rule quotes an open entry and asks the function Kazma
already uses for that setting. A quote that is missing, or that sits only
inside a closed (struck-through) entry, is not a pass and not a gap that
applies: the check did not confirm an open entry. A closed entry is never
reported as applying.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "APPLIES",
    "NOT_RUN",
    "GapJudgement",
    "GapRule",
    "RULES",
    "gaps_path",
    "judge_rules",
]

APPLIES = "attention"
NOT_RUN = "not_run"

#: What a check is allowed to report as "did not run". ``except Exception`` is
#: debt the ratchet will not take (tests/test_debt_ratchet.py). These are the
#: failures the called code raises: builtin errors, SQLite, and a process.
_FAILURES = (
    ArithmeticError,
    AssertionError,
    AttributeError,
    ImportError,
    LookupError,
    MemoryError,
    NameError,
    OSError,
    ReferenceError,
    RuntimeError,
    StopIteration,
    SyntaxError,
    SystemError,
    TypeError,
    ValueError,
    sqlite3.Error,
    subprocess.SubprocessError,
)
try:
    from cryptography.x509.extensions import ExtensionNotFound as _CertExtensionMissing
except ImportError:
    _CertExtensionMissing = None
if _CertExtensionMissing is not None:
    _FAILURES = (*_FAILURES, _CertExtensionMissing)


@dataclass(frozen=True)
class GapRule:
    """One open known gap, and the setting question that selects it."""

    id: str
    quote: str
    title_en: str
    title_ar: str
    detail_en: str
    detail_ar: str
    applies: Callable[[], bool]


@dataclass(frozen=True)
class GapJudgement:
    """A rule that applies, or a rule this install could not judge."""

    id: str
    status: str
    title_en: str
    title_ar: str
    detail_en: str
    detail_ar: str


def _postgres_data_dir() -> bool:
    """The known gap applies on a Postgres install. ``is_postgres`` is the one answer."""
    from kazma_core.db.backend import is_postgres

    return bool(is_postgres())


def _python_exec_on_the_host() -> bool:
    """The known gap applies when host ``python_exec`` is still allowed.

    :func:`kazma_core.tools.code_exec.local_exec_forbidden` is the one answer.
    """
    from kazma_core.tools.code_exec import local_exec_forbidden

    return not local_exec_forbidden()


def _trusted_mcp_skips_the_gate() -> bool:
    """The known gap applies when a ``trust: trusted`` server may skip approval.

    :meth:`AsyncMCPManager._trusted_allowed_in_prod` is the one answer: outside
    production the mark is honored, and in production only with the opt-in.
    """
    from kazma_core.mcp.manager import AsyncMCPManager

    return bool(AsyncMCPManager._trusted_allowed_in_prod())


def _safe_allowlist_names_a_tool() -> bool:
    """The known gap applies when the operator's allowlist names a tool.

    Membership is :func:`kazma_core.mcp.manager.mcp_safe_allowlisted`. The
    names are the variable's own tokens, handed to that function.
    """
    from kazma_core.mcp.manager import mcp_safe_allowlisted

    raw = os.environ.get("KAZMA_MCP_SAFE_ALLOWLIST") or ""
    return any(mcp_safe_allowlisted(part.strip()) for part in raw.split(",") if part.strip())


RULES: tuple[GapRule, ...] = (
    GapRule(
        id="postgres-data-dir",
        quote="`KAZMA_DATA_DIR` does not isolate a Postgres-backed",
        title_en="KAZMA_DATA_DIR does not isolate a Postgres settings store",
        title_ar="مجلد البيانات لا يعزل مخزن إعدادات Postgres",
        detail_en=(
            "This install uses Postgres. KAZMA_DATA_DIR moves files only; "
            "settings stay on the database named by KAZMA_DATABASE_URL. "
            "Known gaps: the accepted Postgres data-dir entry."
        ),
        detail_ar=(
            "هذا التثبيت يستخدم Postgres. مجلد البيانات ينقل الملفات فقط، "
            "والإعدادات تبقى على قاعدة البيانات التي يسميها KAZMA_DATABASE_URL. "
            "الثغرات المعروفة: بند مجلد البيانات المقبول."
        ),
        applies=_postgres_data_dir,
    ),
    GapRule(
        id="python-exec-on-the-host",
        quote="`python_exec` is sandboxed only when `KAZMA_CODE_EXEC_DOCKER=force`",
        title_en="python_exec can run on this machine",
        title_ar="تنفيذ بايثون يمكن أن يعمل على هذا الجهاز",
        detail_en=(
            "Approved python_exec is not forced into a container on this install. "
            "Known gaps: the single-operator host boundary."
        ),
        detail_ar=(
            "تنفيذ بايثون بعد الموافقة ليس مجبراً على الحاوية في هذا التثبيت. الثغرات المعروفة: حد الجهاز لمشغّل واحد."
        ),
        applies=_python_exec_on_the_host,
    ),
    GapRule(
        id="mcp-trusted-skips-the-gate",
        quote="production ignores that mark unless",
        title_en="A trusted MCP server can skip the approval gate",
        title_ar="خادم MCP موثوق يمكنه تخطي بوابة الموافقة",
        detail_en=(
            "On this install a server marked trust: trusted skips the executor gate. "
            "Production ignores that mark unless KAZMA_MCP_TRUSTED_IN_PROD is set. "
            "Known gaps: the MCP bridge."
        ),
        detail_ar=(
            "في هذا التثبيت، خادم معلّم بأنه موثوق يتخطى بوابة التنفيذ. "
            "وضع الإنتاج يتجاهل هذه العلامة ما لم يُضبط KAZMA_MCP_TRUSTED_IN_PROD. "
            "الثغرات المعروفة: جسر MCP."
        ),
        applies=_trusted_mcp_skips_the_gate,
    ),
    GapRule(
        id="mcp-safe-allowlist",
        quote="`KAZMA_MCP_SAFE_ALLOWLIST` names the specific tools",
        title_en="KAZMA_MCP_SAFE_ALLOWLIST names tools that run unattended",
        title_ar="قائمة الأدوات الآمنة تسمي أدوات تعمل دون حضور",
        detail_en=(
            "This install's allowlist names at least one MCP tool, so that tool "
            "runs without an approval card. Known gaps: the MCP bridge."
        ),
        detail_ar=(
            "قائمة هذا التثبيت تسمي أداة MCP واحدة على الأقل، فتعمل دون بطاقة موافقة. الثغرات المعروفة: جسر MCP."
        ),
        applies=_safe_allowlist_names_a_tool,
    ),
)


def gaps_path() -> Path:
    """``docs/KNOWN_GAPS.md`` in the install that contains this code, else the project from the working directory."""
    from kazma_core.paths import get_project_root, installed_project_root

    root = installed_project_root() or get_project_root()
    return root / "docs" / "KNOWN_GAPS.md"


def _quote_is_open(text: str, quote: str) -> bool | None:
    """True when *quote* sits outside a ``~~strike~~``, False when every copy is struck, None when absent.

    A closed known-gap entry is wrapped in ``~~``. Pairs before an open entry
    leave the count even, so a later open quote is not inside a strike.
    """
    if not quote:
        return None
    found = False
    start = 0
    while True:
        at = text.find(quote, start)
        if at < 0:
            break
        found = True
        if text[:at].count("~~") % 2 == 0:
            return True
        start = at + len(quote)
    return False if found else None


def _could_not_judge(rule: GapRule, detail_en: str, detail_ar: str) -> GapJudgement:
    return GapJudgement(
        id=rule.id,
        status=NOT_RUN,
        title_en=rule.title_en,
        title_ar=rule.title_ar,
        detail_en=detail_en,
        detail_ar=detail_ar,
    )


def judge_rules(text: str, rules: Sequence[GapRule] = RULES) -> list[GapJudgement]:
    """Judge *rules* against *text* and this install's settings.

    A rule whose quote is not an open entry, or whose setting question
    raises, is ``not_run``. A rule that does not apply is omitted. Nothing
    here is a pass.
    """
    found: list[GapJudgement] = []
    for rule in rules:
        state = _quote_is_open(text, rule.quote)
        if state is None:
            found.append(
                _could_not_judge(
                    rule,
                    "This quote is not in the known-gaps page, so the gap was not judged.",
                    "هذا الاقتباس ليس في صفحة الثغرات المعروفة، فلم يُحكم على الثغرة.",
                )
            )
            continue
        if state is False:
            found.append(
                _could_not_judge(
                    rule,
                    "This quote is only in a closed known-gap entry, so it was not reported as applying.",
                    "هذا الاقتباس في بند مغلق فقط، فلم يُذكر على أنه ينطبق.",
                )
            )
            continue
        try:
            applies = bool(rule.applies())
        except _FAILURES as exc:
            logger.warning("[self-check] known gap %s could not be judged", rule.id, exc_info=True)
            from kazma_core.errors import described_error

            info = described_error(
                "The setting for this known gap could not be read",
                exc,
                level=logging.WARNING,
            )
            found.append(
                _could_not_judge(
                    rule,
                    str(info),
                    f"تعذّرت قراءة الإعداد لهذه الثغرة المعروفة ({info.code}, ref: {info.ref}).",
                )
            )
            continue
        if not applies:
            continue
        found.append(
            GapJudgement(
                id=rule.id,
                status=APPLIES,
                title_en=rule.title_en,
                title_ar=rule.title_ar,
                detail_en=rule.detail_en,
                detail_ar=rule.detail_ar,
            )
        )
    return found
