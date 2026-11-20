"""A memory problem says what to do and where (2026-11-20).

The Memory page's "Needs attention" listed what was wrong -- "Neo4j does not
answer (the sign-in was refused); 17 changes waiting for it" -- and never how
to fix it or where. Every memory health check that can warn or fail now
carries ``fix`` (what to do, one sentence) and ``link`` (the page of Kazma
where it is done), and the page shows both.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kazma_core.memory import health

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "kazma-core" / "kazma_core" / "memory" / "health.py"


def _checks_without_a_fix(source: str) -> list[int]:
    """Lines of ``_comp(...)`` calls whose status can be warn or error (a
    literal naming one, or a variable) and that pass no ``fix=``."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_comp"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        status = kw.get("status")
        if status is None:
            continue
        text = ast.unparse(status)
        can_fail = "warn" in text or "error" in text or isinstance(status, ast.Name)
        if can_fail and "fix" not in kw:
            out.append(node.lineno)
    return out


def test_every_check_that_can_fail_says_how_to_fix_it():
    missing = _checks_without_a_fix(HEALTH.read_text(encoding="utf-8"))
    assert missing == [], f"memory health checks that can warn or fail with no fix=: lines {missing}"


def test_the_gate_finds_a_planted_check():
    planted = (
        "def f(ok, s):\n"
        "    _comp('x', 'X', ok=ok, status='ok' if ok else 'warn', detail='broken')\n"
        "    _comp('y', 'Y', ok=ok, status=s, detail='broken')\n"
        "    _comp('z', 'Z', ok=ok, status='off', detail='optional')\n"
    )
    assert _checks_without_a_fix(planted) == [2, 3]


def test_every_link_is_a_page_of_this_site():
    links = [node.value for node in ast.walk(ast.parse(HEALTH.read_text(encoding="utf-8")))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and node.value.startswith(("/settings", "/memory", "/dashboard"))]
    assert links and all(link.startswith("/") and not link.startswith("//") for link in links)


def test_a_refused_neo4j_sign_in_says_what_to_do_and_where():
    """The live problem of 2026-11-20."""
    comp = health._graph_copy_component(
        {"status": "unreachable", "reason": "the sign-in was refused", "pending_changes": 17},
        {}, lambda: None)
    assert comp["status"] == "warn"
    assert "Test Neo4j" in comp["fix"] and "KAZMA_NEO4J_PASSWORD" in comp["fix"]
    assert comp["link"] == "/settings?tab=memory"


def test_a_missing_driver_points_at_the_packages():
    comp = health._graph_copy_component({"status": "driver_missing"}, {}, lambda: None)
    assert "kazma update --reinstall -y" in comp["fix"] and comp["link"] == "/settings?tab=packages"


def test_a_healthy_check_carries_no_fix():
    comp = health._comp("x", "X", ok=True, status="ok", detail="fine", fix="do this", link="/settings")
    assert comp["fix"] == "" and comp["link"] == ""


def test_the_health_answer_lists_problems_with_their_fixes(monkeypatch):
    monkeypatch.setattr(health, "recall_degraded",
                        lambda: {"fail_count": 2, "last_failure_epoch": 0, "last_reason": "no embedder"})
    out = health.build_memory_health()
    recall = next(p for p in out["problems"] if p["id"] == "recall_failures")
    assert recall["fix"] and "no embedder" in recall["detail"]
    assert len(out["problems"]) == len(out["issues"]) or len(out["problems"]) == 12


# ── the Dashboard's open problems ─────────────────────────────────────────


def _panel_sources() -> list[str]:
    import re

    js = (ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "problems_panel.js").read_text(encoding="utf-8")
    block = js[js.index("var SOURCES = {"):]
    block = block[:block.index("};")]
    return re.findall(r"^\s*([a-z]+):\s*\[", block, re.M)


def test_every_kind_of_problem_says_what_to_do_in_both_languages():
    from kazma_ui.i18n import TRANSLATIONS

    sources = _panel_sources()
    assert {"alert", "incident", "acquisition", "requirements", "advisory", "observer"} <= set(sources)
    missing = [s for s in sources
               if not all((TRANSLATIONS.get(f"problems.todo.{s}") or {}).get(lang) for lang in ("en", "ar"))]
    assert missing == [], f"problems.todo.<source> missing for {missing}"


def test_an_incident_in_the_bell_says_why(monkeypatch):
    from kazma_core.runs import notify

    row = {"id": "inc_1", "run_id": "memory_task:1", "kind": "memory_task", "condition": "failed",
           "title": "native_pg_offsite", "opened_at": 1.0,
           "detail": "The Postgres dump was not copied into: local."}

    class _Store:
        def live(self):
            return [row]

    from kazma_core.runs import incidents

    monkeypatch.setattr(incidents, "get_incident_store", lambda: _Store())
    reason = notify.bell_alerts("en")[0]["reason"]
    assert reason == "native_pg_offsite: The Postgres dump was not copied into: local."
    row["detail"] = ""
    assert notify.bell_alerts("en")[0]["reason"] == "native_pg_offsite"
