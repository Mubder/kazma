"""Soft-nav must re-inject every page-owned script, not a name whitelist.

First click on Memory (and other sidebar pages) used to swap the shell
without loading companions like memory_console.js. Second click on the
same link is a full document load — so the page suddenly filled in.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_NAV = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js"
_TEMPLATES = _ROOT / "kazma-ui" / "kazma_ui" / "templates"

_GLOBAL = {
    "/static/js/app.js",
    "/static/js/htmx.min.js",
    "/static/js/alpine.min.js",
    "/static/js/icons.js",
    "/static/js/auth-guard.js",
    "/static/js/bidi.js",
}


def _eval_gate(srcs: list[str]) -> list[bool]:
    uri = _NAV.resolve().as_uri()
    script = (
        "import { isSoftNavPageScript } from "
        + json.dumps(uri)
        + "; "
        + "const srcs = "
        + json.dumps(srcs)
        + "; "
        + "process.stdout.write(JSON.stringify(srcs.map(isSoftNavPageScript)));"
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


def test_memory_console_and_companions_are_page_scripts() -> None:
    flags = _eval_gate(
        [
            "/static/js/memory_console.js",
            "/static/js/memory.js",
            "/static/js/dash_lists.js",
            "/static/js/voice.js",
            "/static/js/stores/agentStore.js",
            "/static/js/mermaid.min.js",
            "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js",
        ]
    )
    assert flags == [True, True, True, True, True, True, True]


def test_global_and_module_scripts_are_not_reinjected() -> None:
    flags = _eval_gate(
        [
            "/static/js/app.js",
            "/static/js/alpine.min.js",
            "/static/js/modules/stores.js",
            "/static/js/auth-guard.js",
            "/static/js/bidi.js",
        ]
    )
    assert flags == [False, False, False, False, False]


def test_every_template_script_is_classified() -> None:
    srcs = _template_script_srcs()
    assert any("memory_console.js" in s for s in srcs)
    flags = _eval_gate(srcs)
    for src, keep in zip(srcs, flags):
        path = src.split("?")[0]
        if any(path.endswith(g) for g in _GLOBAL) or "/static/js/modules/" in path:
            assert keep is False, src
        elif "/static/js/" in path or "codemirror" in path.lower():
            assert keep is True, src


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
