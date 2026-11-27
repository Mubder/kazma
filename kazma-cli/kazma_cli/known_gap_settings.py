"""Which open entries in ``docs/KNOWN_GAPS.md`` match this install's settings.

The page is prose. Each rule quotes an open entry and asks the function Kazma
already uses for that setting. A quote that is missing, or that sits only
inside a closed (struck-through) entry, is not a pass and not a gap that
applies: the check did not confirm an open entry. A closed entry is never
reported as applying.

A setting the open page names is either a rule's ``settings`` or an
``EXEMPTIONS`` reason. The reasons are for the gate. They are not shown.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "APPLIES",
    "EXEMPTIONS",
    "NOT_RUN",
    "GapJudgement",
    "GapRule",
    "RULES",
    "gaps_path",
    "judge_rules",
    "open_gap_settings",
]

APPLIES = "attention"
NOT_RUN = "not_run"

_KAZMA_SETTING = re.compile(r"KAZMA_[A-Z0-9_]+")
_STRIKE = re.compile(r"~~.*?~~", re.DOTALL)


def _setting(suffix: str) -> str:
    """The gap page's name for one variable.

    The prefix is joined here. An exact ``KAZMA_*`` string in this file would
    be a second reader in the generated env index, and this file asks the
    function that already reads the variable.
    """
    return "KAZMA_" + suffix


def _settings(*suffixes: str) -> frozenset[str]:
    return frozenset(_setting(suffix) for suffix in suffixes)


def _guarded(fn: Callable[[], object]) -> tuple[object, BaseException | None]:
    """Run ``fn``. One declared catch: a failed check is reported, and the report continues.

    ``except Exception`` is the catch. A list of builtin types let psycopg,
    httpx and sigstore errors end the whole report.
    """
    try:
        return fn(), None
    except Exception as exc:
        logger.warning("[self-check] a check could not finish", exc_info=True)
        return None, exc


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
    settings: frozenset[str] = frozenset()


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
    """The gap applies when Postgres is the store and the data folder was moved.

    ``is_postgres`` and :func:`kazma_core.paths.data_dir_is_relocated` are the
    answers. A Postgres install whose data folder was not moved does not meet it.
    """
    from kazma_core.db.backend import is_postgres
    from kazma_core.paths import data_dir_is_relocated

    return bool(is_postgres()) and data_dir_is_relocated()


def _python_exec_on_the_host() -> bool:
    """The gap applies when host ``python_exec`` is still allowed.

    :func:`kazma_core.safety.deployment_policy.container_required` is the
    settings answer: a failed read raises, so the gap is not run. The docker
    switch is :func:`kazma_core.tools.code_exec.docker_exec_forced`.
    ``local_exec_forbidden`` is not asked: it swallows a failed read and
    treats an unbound space as forbidden.
    """
    from kazma_core.safety.deployment_policy import container_required
    from kazma_core.tools.code_exec import docker_exec_forced

    if container_required():
        return False
    return not docker_exec_forced()


def _trusted_mcp_skips_the_gate() -> bool:
    """The gap applies when a configured server is ``trust: trusted`` and the mark is honored.

    ``get_config_store().get`` is asked first so a failed settings read raises.
    ``list_mcp_servers`` swallows that failure and would otherwise look like
    "no server".
    """
    from kazma_core.config_store import get_config_store
    from kazma_core.mcp.manager import AsyncMCPManager
    from kazma_core.mcp_servers_store import list_mcp_servers

    get_config_store().get("mcp.servers", None)
    if not AsyncMCPManager._trusted_allowed_in_prod():
        return False
    for server in list_mcp_servers():
        if str(server.get("trust") or "").strip().lower() == "trusted":
            return True
    return False


def _safe_allowlist_names_a_tool() -> bool:
    """The gap applies when the operator's allowlist names a tool.

    :func:`kazma_core.mcp.manager.mcp_safe_allowlist_names_a_tool` is the one
    reader of ``KAZMA_MCP_SAFE_ALLOWLIST``.
    """
    from kazma_core.mcp.manager import mcp_safe_allowlist_names_a_tool

    return mcp_safe_allowlist_names_a_tool()


RULES: tuple[GapRule, ...] = (
    GapRule(
        id="postgres-data-dir",
        quote="`KAZMA_DATA_DIR` does not isolate a Postgres-backed",
        title_en="KAZMA_DATA_DIR does not isolate a Postgres settings store",
        title_ar="مجلد البيانات لا يعزل مخزن إعدادات Postgres",
        detail_en=(
            "This install uses Postgres and its data folder was moved. "
            "KAZMA_DATA_DIR moves files only; settings stay on the database "
            "named by KAZMA_DATABASE_URL. Known gaps: the accepted Postgres data-dir entry."
        ),
        detail_ar=(
            "هذا التثبيت يستخدم Postgres ومجلد بياناته نُقل. مجلد البيانات ينقل الملفات فقط، "
            "والإعدادات تبقى على قاعدة البيانات التي يسميها KAZMA_DATABASE_URL. "
            "الثغرات المعروفة: بند مجلد البيانات المقبول."
        ),
        applies=_postgres_data_dir,
        settings=_settings("DATA_DIR"),
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
            "تنفيذ بايثون بعد الموافقة ليس مجبراً على الحاوية في هذا التثبيت. "
            "الثغرات المعروفة: حد الجهاز لمشغّل واحد."
        ),
        applies=_python_exec_on_the_host,
        settings=_settings("CODE_EXEC_DOCKER"),
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
        settings=_settings("MCP_TRUSTED_IN_PROD"),
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
            "قائمة هذا التثبيت تسمي أداة MCP واحدة على الأقل، فتعمل دون بطاقة موافقة. "
            "الثغرات المعروفة: جسر MCP."
        ),
        applies=_safe_allowlist_names_a_tool,
        settings=_settings("MCP_SAFE_ALLOWLIST"),
    ),
)


#: Open mentions that are not a setting-conditional gap of their own.
#: Developer-facing. Not shown in the report.
EXEMPTIONS: dict[str, str] = {
    _setting("CALENDAR_PROVIDER"): (
        "Named in the closed findings table. The calendar provider bug is fixed "
        "and gated by test_calendar_connector.py."
    ),
    _setting("DATABASE_URL"): (
        "Named inside the accepted Postgres data-dir gap as the setting that "
        "selects the database. The postgres-data-dir rule fires when that folder "
        "was moved, not because this variable is set."
    ),
    _setting("DB_BACKEND"): (
        "Named inside the accepted Postgres data-dir gap as the setting that "
        "selects the database, and in the by-design note that the suite reaches "
        "real Postgres only through KAZMA_TEST_ALLOW_REAL_DB."
    ),
    _setting("DIVISION_ENFORCE"): (
        "Named in the closed findings table. Division enforcement is fixed and "
        "gated by test_still_not_doing.py."
    ),
    _setting("HOST"): (
        "Named in a closed bug: serve honors KAZMA_HOST. Gated by test_serve_env.py."
    ),
    _setting("MCP_INHERIT_ENV"): (
        "Named among the switches now listed in SECURITY_ENV_NAMES. The visibility gap is closed."
    ),
    _setting("OPS_ALERTS"): (
        "Named where the test suite turns alerts off. That is a test note, not an open gap."
    ),
    _setting("PGVECTOR"): (
        "Named in a closed Memory-form bug. A saved choice is no longer undone by KAZMA_PGVECTOR=0."
    ),
    _setting("PG_RESTORE_REHEARSAL"): (
        "The restore-rehearsal entry is closed (on by default). The name survives "
        "in the sentence that says how to turn it off."
    ),
    _setting("PRODUCTION"): (
        "The condition of the trusted-MCP rule, and a closed boot-key note. Not a gap of its own."
    ),
    _setting("PROJECT_ROOT"): (
        "Named in a test-isolation note, not an open setting-conditional gap."
    ),
    _setting("TEST_ALLOW_REAL_DB"): (
        "By design: the one switch that lets the suite reach real Postgres. Not an open gap."
    ),
    _setting("TRUSTED_PROXIES"): (
        "Named in a closed bug. The proxy list is honored. Gated by test_forwarded_headers_peer.py."
    ),
}


def gaps_path():
    """``docs/KNOWN_GAPS.md`` in the install that contains this code, else the project from the working directory."""
    from pathlib import Path

    from kazma_core.paths import get_project_root, installed_project_root

    root = installed_project_root() or get_project_root()
    return Path(root) / "docs" / "KNOWN_GAPS.md"


def open_gap_settings(text: str) -> set[str]:
    """``KAZMA_*`` names in *text* outside a ``~~strike~~``."""
    visible = _STRIKE.sub("", text)
    return set(_KAZMA_SETTING.findall(visible))


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
        applies, exc = _guarded(rule.applies)
        if exc is not None:
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
