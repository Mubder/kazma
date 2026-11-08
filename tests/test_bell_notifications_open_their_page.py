"""A notification in the bell opens the page it is about (2026-11-08, the
owner: "if there is a notification, clicking on that notification takes you
to the related page").

The bell's rows were text. Some alerts carried a page (a model fallback, a
research or crawl result, a run incident: ``callback_id`` ``link:/...``), most
did not (memory, RAM and autoscaler health, a model failover). Now every item
``/api/alerts/recent`` returns has ``link``, a path of this site
(``observability.alerts.alert_link``): its own, else its subsystem's page,
else the Dashboard, whose open-problems card lists every alert.

The gate: every product call that puts an alert in the bell names a
subsystem with a page of its own, or passes a link. A new subsystem with
neither fails here, and the planted one is the control.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRODUCERS = {"trigger_system_alert", "post_banner", "broadcast_alert"}

#: Calls whose subsystem is not a literal, with why they are covered.
DECLARED = {
    ("kazma-core/kazma_core/observability/model_fallback.py", "_announce"):
        "passes on its callers' llm-fallback:/llm-rebind:/llm-failover: subsystems, "
        "each in SUBSYSTEM_PREFIX_PAGES (test_model_fallback_banners_open_their_page)",
    ("kazma-core/kazma_core/observability/alerts.py", "trigger_system_alert"):
        "the module's wrapper: passes its caller's subsystem on, and its callers are checked here",
}


@pytest.mark.parametrize("item,page", [
    ({"callback_id": "link:/research?session=s1", "subsystem": "Acquisition"}, "/research?session=s1"),
    ({"callback_id": "link:/dashboard?incident=inc_0123456789ab"}, "/dashboard?incident=inc_0123456789ab"),
    ({"subsystem": "Memory"}, "/memory"),
    ({"subsystem": "AutoScaler"}, "/swarm"),
    ({"subsystem": "llm-failover:deepseek"}, "/settings?tab=agent"),
    ({"subsystem": "llm-fallback:groq"}, "/settings?tab=providers_connectors"),
    ({"callback_id": "sentence-transformers", "subsystem": "Memory"}, "/settings?tab=packages"),
    ({"subsystem": "Something new"}, "/dashboard"),
    ({"callback_id": "link://evil.example/x", "subsystem": "Memory"}, "/memory"),
    ({"callback_id": "link:https://evil.example/", "subsystem": "Mystery"}, "/dashboard"),
    ({"callback_id": "link:/\\evil.example"}, "/dashboard"),
])
def test_each_notification_has_a_page_of_this_site(item, page):
    from kazma_core.observability.alerts import alert_link

    assert alert_link(item) == page


def test_an_alert_object_has_its_page_too():
    from kazma_core.observability.alerts import AlertDispatcher, alert_link

    AlertDispatcher.reset_state()
    try:
        banner = AlertDispatcher.post_banner(subsystem="Memory", title="t", reason="r")
        assert alert_link(banner) == "/memory"
        linked = AlertDispatcher.post_banner(subsystem="x", title="t", reason="r", link="/knowledge")
        assert alert_link(linked) == "/knowledge"
    finally:
        AlertDispatcher.reset_state()


def test_model_fallback_banners_open_their_page(monkeypatch):
    """The real producers of the model banners, each to its page."""
    from kazma_core.observability import model_fallback, ops_alerts
    from kazma_core.observability.alerts import AlertDispatcher, alert_link

    monkeypatch.setattr(ops_alerts, "alert", lambda *a, **k: True)
    monkeypatch.setattr(model_fallback, "_keep_mark", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(model_fallback, "_suppressed", lambda: False)
    AlertDispatcher.reset_state()
    try:
        model_fallback.report_failover(model="deepseek-flash", used_model="google/gemini-2.5-flash", reason="x")
        model_fallback.report_substitution(provider="groq", model="m", used_provider="deepseek", used_model="d")
        pages = {a.subsystem.split(":")[0]: alert_link(a) for a in AlertDispatcher.get_recent_alerts()}
    finally:
        AlertDispatcher.reset_state()
    assert pages.get("llm-failover") == "/settings?tab=agent", pages
    assert pages.get("llm-fallback") == "/settings?tab=providers_connectors", pages


def _product_files() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "--", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [ROOT / p for p in listed if (ROOT / p).is_file()]


def _subsystem_prefix(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value)
    return None


def _unmapped(source: str, rel: str) -> list[str]:
    from kazma_core.observability.alerts import SUBSYSTEM_PAGES, SUBSYSTEM_PREFIX_PAGES

    found: list[str] = []
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in PRODUCERS:
            continue
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        link = kwargs.get("link") or kwargs.get("callback_id")
        if link is not None and not (isinstance(link, ast.Constant) and not link.value):
            continue
        if "subsystem" not in kwargs:
            subsystem: str | None = "Memory" if name == "trigger_system_alert" else None
        else:
            subsystem = _subsystem_prefix(kwargs["subsystem"])
        owner = node
        while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents[owner]
        where = (rel, getattr(owner, "name", ""))
        if subsystem is None:
            if where not in DECLARED:
                found.append(f"{rel}:{node.lineno} {name}(subsystem=<not a literal>)")
            continue
        low = subsystem.lower()
        if low in SUBSYSTEM_PAGES or any(low.startswith(p) for p, _ in SUBSYSTEM_PREFIX_PAGES):
            continue
        found.append(f"{rel}:{node.lineno} {name}(subsystem={subsystem!r})")
    return found


def test_every_bell_producer_names_a_page():
    found: list[str] = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8")
        if not any(p in text for p in PRODUCERS):
            continue
        found += _unmapped(text, path.relative_to(ROOT).as_posix())
    assert not found, (
        "These put a notification in the bell with no page to open; add the subsystem to "
        "kazma_core.observability.alerts.SUBSYSTEM_PAGES or pass link=:\n  " + "\n  ".join(found))


def test_control_a_new_subsystem_with_no_page_is_caught():
    planted = (
        "async def f():\n"
        "    await trigger_system_alert(subsystem='Mystery', status='DEGRADED', message='x')\n"
        "    AlertDispatcher.post_banner(subsystem='Memory', title='t', reason='r')\n"
        "    AlertDispatcher.post_banner(subsystem='Other', title='t', reason='r', link='/x')\n"
    )
    assert _unmapped(planted, "planted.py") == ["planted.py:2 trigger_system_alert(subsystem='Mystery')"]
