"""A dialog typed into is not closed by a click outside it without asking.

"Add a provider" closed on any click outside and lost what was typed (the
owner, 2026-11-22); the connector, profile, MCP, scheduled and workspace
dialogs did the same. Every overlay a click outside closes asks through
``KazmaDialogGuard`` (static/js/modules/dialog_guard.js), or is declared here
with its reason. The behaviour: tests/e2e/test_provider_setup_browser.py.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = "kazma-ui/kazma_ui/templates"

#: Overlays a click outside closes as before, by template and a mark of the
#: overlay, with why.
DECLARED = {
    ("components/modal.html", "$store.modal.dismiss()"):
        "the shared question: one with something typed in it stays open (the store's dismiss, tests/js/test_modal_store.js)",
    ("components/provider_setup.html", "tidy.open = false"): "ticks only; nothing is typed",
    ("swarm.html", "closeTaskDetail"): "shows a task; nothing is typed",
    ("swarm.html", "closeLogs"): "shows a log; nothing is typed",
    ("swarm.html", "edit-worker-modal"): "the swarm's own dialog (plain script, not Alpine): the swarm work's to change",
}

_OVERLAY = re.compile(r"<div\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*\bclass=\"[^\"]*\bmodal-overlay\b[^\"]*\"(?:[^>\"']|\"[^\"]*\"|'[^']*')*>")
_COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}", re.S)


def _templates() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", TEMPLATES], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {name.removeprefix(TEMPLATES + "/"): (ROOT / name).read_text(encoding="utf-8")
            for name in listed.split() if name.endswith(".html")}


def _overlays(text: str) -> list[str]:
    return _OVERLAY.findall(_COMMENT.sub("", text))


def _problems(name: str, text: str) -> list[str]:
    out = []
    for tag in _overlays(text):
        if "@click.self" not in tag and "onclick" not in tag:
            continue  # closed by its own buttons only
        if any(t == name and mark in tag for t, mark in DECLARED):
            continue
        guarded = "KazmaDialogGuard.close($el" in tag or "closeProviderSetup()" in tag
        if not guarded:
            out.append(f"{name}: a click outside closes it without asking: {tag[:160]}")
        elif "KazmaDialogGuard" in tag and "@keydown.escape" not in tag:
            out.append(f"{name}: Escape does nothing here: {tag[:160]}")
        elif "KazmaDialogGuard.close" in tag and ("data-dialog-guard" not in tag or "KazmaDialogGuard.opened($el)" not in tag):
            out.append(f"{name}: what is typed is not noticed, or is never forgotten: {tag[:160]}")
    return out


def test_every_dialog_a_click_outside_closes_asks_first():
    pages = _templates()
    found = [p for name, text in pages.items() for p in _problems(name, text)]
    assert found == []
    # The gate sees the dialogs: the settings page's four and the setup's two.
    assert len(_overlays(pages["settings.html"])) >= 4 and len(_overlays(pages["components/provider_setup.html"])) == 2


def test_every_declared_dialog_is_still_there():
    pages = _templates()
    for (name, mark), _why in DECLARED.items():
        assert any(mark in tag for tag in _overlays(pages[name])), (name, mark)


def test_the_gate_sees_the_old_dialog():
    old = '<div x-show="hubProviderModal" x-cloak class="modal-overlay" @click.self="hubProviderModal = false">'
    assert _problems("settings.html", old)
    half = ('<div class="modal-overlay" data-dialog-guard @click.self="KazmaDialogGuard.close($el, () => open = false)" '
            '@keydown.escape.window="open">')
    assert _problems("settings.html", half)


def test_the_guard_is_loaded_by_the_page_shell():
    app = (ROOT / "kazma-ui/kazma_ui/static/js/app.js").read_text(encoding="utf-8")
    assert "installDialogGuard();" in app
