"""Every GitHub Action a workflow uses is pinned to a commit (2026-11, FA-209).

A tag (``actions/checkout@v7``) is a name its owner can move: a compromised
action account could point it at other code, and every workflow here --
the release one, which signs and publishes, among them -- would run it with
the repository's token. Each ``uses:`` names a 40-hex commit, the tag kept
as a comment for Dependabot and readers. A planted tag is the negative
control.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)
_PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _unpinned(text: str) -> list[str]:
    return [ref for ref in _USES.findall(text) if not ref.startswith("./") and not _PINNED.match(ref)]


def test_every_action_is_pinned_to_a_commit():
    found = {}
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        refs = _unpinned(workflow.read_text(encoding="utf-8"))
        if refs:
            found[workflow.name] = refs
    assert not found, f"pin these by commit SHA (keep the tag as a comment): {found}"


def test_negative_control_a_tag_is_found():
    planted = "steps:\n  - uses: actions/checkout@v7\n  - uses: actions/setup-python@" + "a" * 40 + " # v7\n"
    assert _unpinned(planted) == ["actions/checkout@v7"]
