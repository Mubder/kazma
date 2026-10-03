"""Soft-nav must run every script a page needs, not a name whitelist.

First click on Memory (and other sidebar pages) used to swap the shell
without loading companions like memory_console.js. Second click on the
same link is a full document load — so the page suddenly filled in.

The same class again on 2026-10-03: soft-nav skipped every classic script
under /static/js/modules/, and Chat keeps its turn machinery there. A Chat
reached from another page painted no turn until a reload. A script a page
includes is now one of three kinds, and none is dropped: the base shell's
(run once by the page load), a shared one in modules/ (loaded once per
version), or the page's own (run on every visit).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_NAV = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js"
_TEMPLATES = _ROOT / "kazma-ui" / "kazma_ui" / "templates"

_BASE = _TEMPLATES / "base.html"
_CLASSIC_SCRIPT = re.compile(r'<script(?![^>]*type="(?:module|importmap)")[^>]+src="([^"]+)"')


def _eval_gate(srcs: list[str], fn: str = "isSoftNavPageScript") -> list:
    uri = _NAV.resolve().as_uri()
    script = (
        "import { " + fn + " } from "
        + json.dumps(uri)
        + "; "
        + "const srcs = "
        + json.dumps(srcs)
        + "; "
        + "process.stdout.write(JSON.stringify(srcs.map((s) => " + fn + "(s))));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _template_script_srcs() -> list[str]:
    found: list[str] = []
    for html in _TEMPLATES.rglob("*.html"):
        text = html.read_text(encoding="utf-8")
        for m in re.finditer(r'<script[^>]+src="([^"]+)"', text):
            found.append(m.group(1))
    return found


def _classic_srcs(html: Path) -> list[str]:
    """The classic (non-module) scripts a template loads by src, as written."""
    return [m.group(1) for m in _CLASSIC_SCRIPT.finditer(html.read_text(encoding="utf-8"))]


def _base_shell_paths() -> set[str]:
    """Every script base.html loads (ES modules too): run by the page load."""
    text = _BASE.read_text(encoding="utf-8")
    return {m.group(1).split("?")[0] for m in re.finditer(r'<script[^>]+src="([^"]+)"', text)}


def _dropped(srcs: list[str], page: list, shared: list, shell: set[str]) -> list[str]:
    """Scripts soft-nav would neither run nor find already run."""
    return [
        src for src, is_page, is_shared in zip(srcs, page, shared)
        if not is_page and not is_shared and src.split("?")[0] not in shell
    ]


def test_memory_console_and_companions_are_page_scripts() -> None:
    flags = _eval_gate(
        [
            "/static/js/memory_console.js",
            "/static/js/memory.js",
            "/static/js/dash_lists.js",
            "/static/js/voice.js",
            "/static/js/stores/agentStore.js",
            "/static/js/mermaid.min.js",
            "/static/vendor/codemirror/codemirror.bundle.js",
        ]
    )
    assert flags == [True, True, True, True, True, True, True]


def test_global_and_module_scripts_are_not_reinjected() -> None:
    srcs = [
        "/static/js/app.js",
        "/static/js/alpine.min.js",
        "/static/js/modules/stores.js",
        "/static/js/auth-guard.js",
        "/static/js/locale_format.js",
        "/static/js/bidi.js",
    ]
    assert _eval_gate(srcs) == [False] * len(srcs)


def test_every_base_shell_script_is_global() -> None:
    """A base.html script runs once with the page load; soft-nav must never
    run it again. locale_format.js was not on the list, so every page switch
    fetched and ran it once more."""
    srcs = _classic_srcs(_BASE)
    assert any("locale_format.js" in s for s in srcs)
    page = _eval_gate(srcs)
    shared = _eval_gate(srcs, "isSoftNavSharedScript")
    assert [s for s, a, b in zip(srcs, page, shared) if a or b] == []
    # Negative control: a shell script nobody listed would be re-run.
    assert _eval_gate(["/static/js/new_shell_script.js?v=1"]) == [True]


def test_chat_turn_modules_are_shared_scripts() -> None:
    srcs = [s for s in _classic_srcs(_TEMPLATES / "chat.html") if "/static/js/modules/" in s]
    assert any("turn_document.js" in s for s in srcs), "chat.html no longer loads its turn modules?"
    assert _eval_gate(srcs, "isSoftNavSharedScript") == [True] * len(srcs)
    assert _eval_gate(srcs) == [False] * len(srcs)


def test_no_template_script_is_dropped_by_soft_nav() -> None:
    """Every classic script every page loads is run by soft-nav, or was run
    by the page load (the shell's). Enumerated from the templates."""
    shell = _base_shell_paths()
    found = 0
    for html in sorted(_TEMPLATES.rglob("*.html")):
        if html == _BASE:
            continue
        srcs = _classic_srcs(html)
        if not srcs:
            continue
        found += len(srcs)
        dropped = _dropped(srcs, _eval_gate(srcs), _eval_gate(srcs, "isSoftNavSharedScript"), shell)
        assert dropped == [], f"{html.name}: soft-nav would never run {dropped}"
    assert found > 20, "the template walk found almost nothing"


def test_the_drop_gate_catches_the_old_rule() -> None:
    """Negative control: the rule before 2026-10-03 shared nothing, so
    chat.html's turn modules were dropped."""
    shell = _base_shell_paths()
    srcs = _classic_srcs(_TEMPLATES / "chat.html")
    dropped = _dropped(srcs, _eval_gate(srcs), [False] * len(srcs), shell)
    assert any("turn_view.js" in s for s in dropped)


def test_script_key_drops_only_the_soft_nav_cache_buster() -> None:
    keys = _eval_gate(
        [
            "/static/js/modules/turn_view.js?v=12&_sn=99",
            "/static/js/modules/turn_view.js?_sn=99&v=12",
            "/static/js/modules/turn_view.js?_sn=99",
            "/static/js/modules/turn_view.js",
            "/static/js/modules/turn_view.js?v=13",
        ],
        "scriptKey",
    )
    assert keys == [
        "/static/js/modules/turn_view.js?v=12",
        "/static/js/modules/turn_view.js?v=12",
        "/static/js/modules/turn_view.js",
        "/static/js/modules/turn_view.js",
        "/static/js/modules/turn_view.js?v=13",
    ]


def test_every_template_script_is_classified() -> None:
    srcs = _template_script_srcs()
    assert any("memory_console.js" in s for s in srcs)
    page = _eval_gate(srcs)
    shared = _eval_gate(srcs, "isSoftNavSharedScript")
    shell = _base_shell_paths()
    for src, is_page, is_shared in zip(srcs, page, shared):
        path = src.split("?")[0]
        if path in shell:
            assert not is_page and not is_shared, src
        elif "/static/js/modules/" in path:
            assert is_shared and not is_page, src
        elif "/static/js/" in path or "codemirror" in path.lower():
            assert is_page and not is_shared, src


def test_soft_nav_calls_head_merge_and_body_inlines() -> None:
    nav = _NAV.read_text(encoding="utf-8")
    assert "function mergePageHead(" in nav
    assert "function runIncomingBodyScripts(" in nav
    assert "isSoftNavPageScript(src)" in nav
    assert "PAGE_SCRIPT_RE" not in nav


def test_soft_nav_pauses_alpine_mutations_across_swap() -> None:
    """<html x-data> makes Alpine init swapped nodes before page scripts.

    That binds settingsApp() as {} and stamps _x_marker, so a later
    initTree is a no-op — first click stuck on "Loading settings…",
    second click (full document load) works.
    """
    nav = _NAV.read_text(encoding="utf-8")
    assert "function pauseAlpineMutations(" in nav
    assert "Alpine.stopObservingMutations" in nav
    assert "Alpine.startObservingMutations" in nav
    assert "function rebindAlpineRoot(" in nav
    # P0-4: bare `x-data` roots are legitimately bound — the old
    # isEmptyAlpineBind gate treated them as unbound and re-introduced the
    # reload loop. isAlpineBound is the current bound-state check.
    assert "function isAlpineBound(" in nav
    assert "function isEmptyAlpineBind(" not in nav
    # Call sites (trailing ;) — not the function declarations.
    pause_at = nav.index("pauseAlpineMutations();")
    swap_at = nav.index("oldBody.innerHTML = newBody.innerHTML")
    bind_at = nav.index("await bindPageAlpine(oldBody, gen)")
    resume_at = nav.index("resumeAlpineMutations();")
    assert pause_at < swap_at < bind_at
    assert pause_at < resume_at
    assert "rebindAlpineRoot(root)" in nav
    assert "HARD_RELOAD_ALWAYS" in nav
    # Factory / loading timeouts must not throw → full reload.
    assert "page factories not ready:" in nav
    assert "page component init still loading" in nav
    assert "page component init stuck (loading)" not in nav
    assert "throw new Error('page factories not ready:" not in nav
