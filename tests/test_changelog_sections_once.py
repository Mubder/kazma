"""Each CHANGELOG section appears once.

2026-11-26: a merge that took both sides of CHANGELOG.md kept a section
twice. The branch had merged main, so the Spaces "whose each database
statement is, counted" entry stood on both sides of the conflict, and the
union kept both copies. A reader sees the same news twice, and the website's
release notes would too.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def _repeated_headings(text: str) -> list[str]:
    heads = re.findall(r"^## [^\n]*$", text.replace("\r\n", "\n"), re.M)
    return sorted(h for h, n in collections.Counter(heads).items() if n > 1)


def test_no_changelog_section_appears_twice():
    assert _repeated_headings(CHANGELOG.read_text(encoding="utf-8")) == []


def test_control_a_section_kept_twice_is_found():
    twice = (
        "# CHANGELOG\n\n## Spaces: counted (2026-11-26)\n\n- a\n\n"
        "## Adding a provider (2026-11-25)\n\n- b\n\n## Spaces: counted (2026-11-26)\n\n- a\n"
    )
    assert _repeated_headings(twice) == ["## Spaces: counted (2026-11-26)"]
    assert _repeated_headings(twice.replace("\n", "\r\n")) == ["## Spaces: counted (2026-11-26)"]
