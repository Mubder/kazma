"""The six UI follow-ups: nav groups, bottom bar, search labels, mobile rails."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kazma_ui.i18n import TRANSLATIONS

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = _ROOT / "kazma-ui" / "kazma_ui" / "templates"
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
_CSS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"


def _sidebar_hrefs_by_section() -> dict[str, list[str]]:
    html = (_TEMPLATES / "components" / "sidebar.html").read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current = "_none"
    for line in html.splitlines():
        if "nav.primary" in line:
            current = "work"
            sections.setdefault(current, [])
        elif "nav.activity" in line:
            current = "activity"
            sections.setdefault(current, [])
        elif "nav.configuration" in line:
            current = "settings"
            sections.setdefault(current, [])
        if 'href="/' in line and "nav-link" in line:
            start = line.index('href="') + 6
            end = line.index('"', start)
            sections.setdefault(current, []).append(line[start:end])
    return sections


def test_sidebar_groups_match_the_six_improvements() -> None:
    groups = _sidebar_hrefs_by_section()
    assert "/dashboard" in groups["work"]
    assert "/agents" in groups["work"]
    assert "/replay" in groups["activity"]
    assert "/dashboard" not in groups["settings"]
    assert "/agents" not in groups["settings"]
    assert "/replay" not in groups["settings"]
    assert groups["settings"] == ["/settings", "/skills", "/mcp"]


def test_bottom_nav_uses_dashboard_not_memory() -> None:
    base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    nav = base.split('class="bottom-nav"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/dashboard"' in nav
    assert 'href="/memory"' not in nav
    assert "t('nav.more')" in nav
    assert "t('nav.dashboard')" in nav


def test_nav_more_i18n_exists() -> None:
    assert TRANSLATIONS["nav.more"]["en"] == "More"
    assert TRANSLATIONS["nav.more"]["ar"]


def test_search_pages_match_sidebar_names() -> None:
    pages_js = _JS / "modules" / "search_pages.js"
    uri = pages_js.resolve().as_uri()
    script = (
        "import { KAZMA_SEARCH_PAGES } from "
        + json.dumps(uri)
        + "; "
        + "const byHref = Object.fromEntries(KAZMA_SEARCH_PAGES.map(p => [p.href, p.title])); "
        + "if (byHref['/dashboard'] !== 'Dashboard') process.exit(2); "
        + "if (byHref['/'] ) process.exit(3); "
        + "if (Object.values(byHref).includes('Analytics')) process.exit(4); "
        + "if (byHref['/chat'] !== 'Chat') process.exit(5); "
        + "process.stdout.write(JSON.stringify(KAZMA_SEARCH_PAGES));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    pages = json.loads(proc.stdout)
    hrefs = {p["href"] for p in pages}
    assert "/dashboard" in hrefs
    assert "/" not in hrefs
    titles = {p["title"] for p in pages}
    assert "Analytics" not in titles
    assert "Dashboard" in titles


def test_dash_lists_session_card_is_the_shipped_builder() -> None:
    src = _JS / "dash_lists.js"
    runner = (
        "const fs=require('fs');const vm=require('vm');"
        "const ctx={};ctx.globalThis=ctx;vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
        "const html=ctx.KazmaDashLists.buildSessionCard({"
        "thread_id:'gw-t-1',platform:'telegram',display_name:'Ali',"
        "message_count:4,context_tokens:12,created_at:'2026-08-17T12:00:00Z'});"
        "if(!html.includes('telegram')) process.exit(2);"
        "if(!html.includes('gw-t-1')) process.exit(3);"
        "if(!html.includes('Ali')) process.exit(4);"
        "if(!html.includes('dash-session-delete')) process.exit(5);"
        "process.stdout.write(html);"
    )
    proc = subprocess.run(
        ["node", "-e", runner, str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    html = proc.stdout
    assert 'data-thread-id="gw-t-1"' in html
    assert "telegram" in html
    assert "Ali" in html


def test_settings_and_swarm_tabs_are_nowrap_rails() -> None:
    css = _CSS.read_text(encoding="utf-8")
    assert "flex-wrap: nowrap" in css
    assert "scroll-snap-type: x proximity" in css
    assert ".dash-card-list" in css
    assert ".dash-table-wrap { display: none !important; }" in css
    assert ".ws-action-btn {\n  min-width: 0;" in css or "min-width: 0;" in css
    assert "grid-template-columns: 1fr 1fr !important" in css


def test_dashboard_js_fills_session_cards() -> None:
    js = (_JS / "dashboard.js").read_text(encoding="utf-8")
    assert "sessions-cards" in js
    assert "KazmaDashLists" in js
    assert "buildSessionCard" in js


def test_dashboard_template_has_card_lists() -> None:
    html = (_TEMPLATES / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="sessions-cards"' in html
    assert 'id="traces-cards"' in html
    assert "dash_lists.js" in html


def test_ide_stacks_on_phone() -> None:
    html = (_TEMPLATES / "ide.html").read_text(encoding="utf-8")
    assert "max-width: 768px" in html
    assert ".ide-tree { max-height: 36vh; }" in html
