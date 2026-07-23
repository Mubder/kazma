#!/usr/bin/env python3
"""Replace UI emoji glyphs with data-icon spans / KazmaIcons calls.

Safe targets: kazma-ui templates + static/js (not minified vendor).
Does not touch box-drawing comment banners or pure ASCII separators.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "kazma-ui/kazma_ui/templates",
    ROOT / "kazma-ui/kazma_ui/static/js",
]

# emoji (possibly with VS16) -> icon name
EMOJI_MAP: list[tuple[str, str]] = [
    ("⚙️", "settings"),
    ("⚙", "settings"),
    ("📊", "bar-chart"),
    ("📝", "file-text"),
    ("🔧", "wrench"),
    ("🛠️", "wrench"),
    ("🛠", "wrench"),
    ("🚀", "rocket"),
    ("📋", "clipboard"),
    ("🔀", "git-branch"),
    ("🐝", "swarm"),
    ("⚠️", "alert"),
    ("⚠", "alert"),
    ("🙈", "eye-off"),
    ("✓", "check"),
    ("✅", "check-circle"),
    ("❌", "x"),
    ("✕", "x"),
    ("✗", "x"),
    ("🧠", "brain"),
    ("👁", "eye"),
    ("👁️", "eye"),
    ("🎯", "target"),
    ("🔑", "key"),
    ("🔐", "lock"),
    ("🔒", "lock"),
    ("💻", "laptop"),
    ("📄", "file"),
    ("▶", "play"),
    ("📭", "inbox"),
    ("💬", "message"),
    ("📜", "scroll"),
    ("🔗", "link"),
    ("📁", "folder"),
    ("📂", "folder-open"),
    ("📦", "package"),
    ("🏁", "flag"),
    ("🔢", "hash"),
    ("🐍", "code"),
    ("💰", "dollar-sign"),
    ("▼", "chevron-down"),
    ("🎨", "palette"),
    ("🎮", "gamepad"),
    ("👷", "bot"),
    ("📤", "upload"),
    ("🗑️", "trash"),
    ("🗑", "trash"),
    ("🌀", "refresh"),
    ("🐙", "github"),
    ("🤖", "bot"),
    ("⚛️", "atom"),
    ("⚛", "atom"),
    ("🖼", "image"),
    ("🖼️", "image"),
    ("🔌", "plug"),
    ("💾", "save"),
    ("🔍", "search"),
    ("🆕", "sparkles"),
    ("✍️", "edit"),
    ("✍", "edit"),
    ("➤", "chevron-right"),
    ("➜", "chevron-right"),
]

# Sort longest first so multi-codepoint emoji match first
EMOJI_MAP.sort(key=lambda x: len(x[0]), reverse=True)


def html_span(name: str) -> str:
    return f'<span class="ki" data-icon="{name}" aria-hidden="true"></span>'


def js_icon(name: str) -> str:
    # Prefer bracket form for kebab names
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        return f"KazmaIcons.{name}()"
    return f'KazmaIcons.get("{name}")'


def replace_in_html(text: str) -> str:
    for emoji, name in EMOJI_MAP:
        if emoji in text:
            text = text.replace(emoji, html_span(name))
    return text


def replace_in_js(text: str) -> str:
    """Replace emoji inside string literals with SVG from KazmaIcons."""
    for emoji, name in EMOJI_MAP:
        if emoji not in text:
            continue
        # Inside single/double-quoted strings: break string and concat icon
        # Simple approach: replace emoji with empty and prefix icon call nearby is hard.
        # Replace 'emoji' / "emoji" as standalone or prefix:
        esc = re.escape(emoji)
        icon = js_icon(name)
        # "🔧 foo" -> KazmaIcons.wrench() + " foo"
        text = re.sub(
            rf'(["\']){esc}\s*',
            lambda m, ic=icon: f"{ic} + {m.group(1)}",
            text,
        )
        # remaining bare emoji in template strings rarely used
        text = text.replace(emoji, f"' + {icon} + '")
    # Clean ugly empty concatenations
    text = re.sub(r"\+\s*['\"]['\"]\s*", "", text)
    text = re.sub(r"['\"]['\"]\s*\+\s*", "", text)
    return text


def should_skip(path: Path) -> bool:
    name = path.name
    if name.endswith(".min.js"):
        return True
    if name == "icons.js":
        return True
    return False


def main() -> None:
    changed = 0
    for base in TARGETS:
        for path in base.rglob("*"):
            if not path.is_file() or should_skip(path):
                continue
            if path.suffix not in {".html", ".js"}:
                continue
            original = path.read_text(encoding="utf-8")
            if path.suffix == ".html":
                updated = replace_in_html(original)
            else:
                updated = replace_in_js(original)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                changed += 1
                print(f"updated {path.relative_to(ROOT)}")
    print(f"files changed: {changed}")


if __name__ == "__main__":
    main()
