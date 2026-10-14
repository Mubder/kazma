"""No Markdown file holds a control character.

2026-10-13: `docs/docs/ops/disaster-recovery.md` (committed) held a form feed
and a split line where a Windows path had been written through an escaping
step: `D:\\fresh-kazma` became FORM FEED + "resh-kazma", and
`\\neo4j_graph.jsonl` became a line break + "eo4j_graph.jsonl". The page
rendered, and the restore commands it gave could not run. Any character below
U+0020 other than tab, carriage return and line feed is such a mangled escape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ALLOWED = {"\t", "\r", "\n"}


def _markdown_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(rel for rel in set(out) if (ROOT / rel).is_file())


def _control_characters(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        for char in line:
            if ord(char) < 32 and char not in _ALLOWED:
                found.append((number, f"U+{ord(char):04X}"))
    return found


def test_no_markdown_file_holds_a_control_character():
    offenders = []
    for rel in _markdown_files():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        offenders += [f"{rel}:{line} {code}" for line, code in _control_characters(text)]
    assert not offenders, "mangled escapes (write the backslash, not the escape): " + ", ".join(offenders)


def test_the_scan_finds_the_mangled_paths():
    # Negative control: the two shapes the disaster-recovery page held.
    mangled = "restore --target D:\x0cresh-kazma\nrestore_graph(r'D:\x0cresh-kazma\r\neo4j_graph.jsonl')"
    assert [code for _, code in _control_characters(mangled)] == ["U+000C", "U+000C"]
