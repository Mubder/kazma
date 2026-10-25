"""Every running state Kazma keeps is declared to the run supervisor.

Phase 0 of ``docs/plans/RUN_SUPERVISION_PLAN.md``. The supervisor can only
watch what it knows about: a new store holding a "running" status that no
adapter reads is work nobody is told about when it hangs. So:

* every product table with a ``status`` or ``state`` column is the source of a
  declared kind (``KindSpec.tables``) or in ``NOT_RUNS`` with the reason;
* each adapter maps EVERY native status of its kind, read from the kind's own
  enum or from the status words its own module writes -- never from a list
  kept beside the adapter;
* every declaration resolves to a real adapter.

Each rule has its negative control: a planted table, a planted status word, a
mapping with one word removed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from kazma_core.runs.contract import RunState
from kazma_core.runs.kinds import KINDS, NOT_RUNS
from kazma_core.runs.registry import adapter_for

REPO = Path(__file__).resolve().parents[1]

_TABLE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*[;\"']",
                    re.S | re.I)
_STATUS_COLUMN = re.compile(r"(^|[\s,(])(status|state)\s+(TEXT|VARCHAR|INTEGER)", re.I)

_WORDS = (
    re.compile(r"""\b(?:status|state)\s*(?:=|==|!=)\s*['"]([a-z_]+)['"]"""),
    re.compile(r"""\b(?:status|state)\s+(?:NOT\s+)?IN\s*\(([^)]*)\)""", re.I),
    re.compile(r"""['"](?:status|state)['"]\s*[:=]\s*['"]([a-z_]+)['"]"""),
    re.compile(r"""\b(?:status|state)\s+in\s*[\(\{\[]([^)\]\}]*)[\)\]\}]"""),
)


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8", errors="ignore")
            for rel in listed if "/tests/" not in rel and (REPO / rel).exists()}


def status_tables(sources: dict[str, str]) -> set[str]:
    """Tables whose CREATE TABLE has a status or state column."""
    found: set[str] = set()
    for text in sources.values():
        for match in _TABLE.finditer(text):
            if _STATUS_COLUMN.search(match.group(2)):
                found.add(match.group(1).lower())
    return found


def undeclared_tables(tables: set[str]) -> set[str]:
    declared = {t for spec in KINDS.values() for t in spec.tables} | set(NOT_RUNS)
    return tables - declared


def status_words(text: str) -> set[str]:
    """The status words a module writes or compares."""
    out: set[str] = set()
    for pattern in _WORDS:
        for match in pattern.finditer(text):
            group = match.group(1)
            if "'" in group or '"' in group:
                out |= set(re.findall(r"""['"]([a-z_]+)['"]""", group))
            else:
                out.add(group)
    return out


def _module_text(module: str) -> str:
    return (REPO / module).read_text(encoding="utf-8")


def native_states(kind: str) -> set[str]:
    """Every status word of *kind*, from its own source."""
    if kind == "swarm_task":
        from kazma_core.swarm.task import TaskStatus

        return {s.value for s in TaskStatus}
    if kind == "scheduled_job":
        from kazma_core.cron.store import JobStatus

        return {s.value for s in JobStatus}
    if kind == "document_job":
        from kazma_core.documents.models import DocumentJobState

        return {s.value for s in DocumentJobState}
    if kind == "crawl":
        from kazma_core.web_acquire.crawl_jobs import _TERMINAL

        return set(_TERMINAL) | {"running"}
    if kind == "research_session":
        return status_words(_module_text("kazma-core/kazma_core/tools/research_session.py"))
    if kind == "x_post":
        return status_words(_module_text("kazma-core/kazma_core/x_api/publication_store.py"))
    if kind == "memory_task":
        return status_words(_module_text("kazma-core/kazma_core/memory/task_queue.py"))
    # Derived by the adapter itself (no stored status of its own): the chat
    # turn's three cases and the maintenance sweep's three outcomes.
    return set(adapter_for(kind).STATE_MAP)


def unmapped(kind: str, state_map: dict[str, str]) -> set[str]:
    return native_states(kind) - set(state_map)


def test_every_status_table_is_a_kind_or_says_why_not():
    tables = status_tables(_product_sources())
    assert not undeclared_tables(tables), (
        "A table holds a status the run supervisor does not watch. Declare the kind that "
        "reads it in kazma_core/runs/kinds.py, or add it to NOT_RUNS with the reason: "
        f"{sorted(undeclared_tables(tables))}")


def test_a_planted_status_table_is_caught():
    planted = {"x.py": 'SQL = """CREATE TABLE IF NOT EXISTS zombie_jobs (id TEXT, status TEXT NOT NULL);"""'}
    assert undeclared_tables(status_tables(planted)) == {"zombie_jobs"}


def test_not_runs_names_only_real_tables():
    tables = status_tables(_product_sources())
    stale = set(NOT_RUNS) - tables
    assert not stale, f"NOT_RUNS names tables no product code creates with a status: {sorted(stale)}"


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_native_state_is_mapped(kind):
    adapter = adapter_for(kind)
    missing = unmapped(kind, adapter.STATE_MAP)
    assert not missing, f"{kind}: native states with no RunState: {sorted(missing)}"
    assert {RunState(v) for v in adapter.STATE_MAP.values()}  # every value is a RunState


@pytest.mark.parametrize("kind", ["swarm_task", "document_job", "research_session", "x_post"])
def test_a_mapping_missing_a_state_is_caught(kind):
    state_map = dict(adapter_for(kind).STATE_MAP)
    dropped = sorted(native_states(kind))[0]
    state_map.pop(dropped)
    assert unmapped(kind, state_map) == {dropped}


def test_a_planted_status_word_is_read_from_source():
    assert "zombie" in status_words("row.update(status='zombie')")
    assert {"a", "b"} <= status_words("WHERE status IN ('a', 'b')")


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_declaration_resolves_to_an_adapter(kind):
    adapter = adapter_for(kind)
    assert callable(getattr(adapter, "list_runs", None))
    assert callable(getattr(adapter, "act", None))
    spec = KINDS[kind]
    assert spec.label and spec.label_ar and spec.source
