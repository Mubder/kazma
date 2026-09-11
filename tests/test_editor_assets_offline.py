"""Guards for the IDE editor: vendored, offline-capable, no dead engine.

Three regressions this file exists to stop, all of them found by audit rather
than by a failing test — which is the reason they are tests now:

1. The IDE fetched CodeMirror (and Monaco before it) from a public CDN. A
   self-hosted agent sold to people with vaults and air gaps must not need the
   open internet to show a file.
2. Swapping Monaco for CodeMirror left ~150 lines calling ``monaco.*`` behind
   ``if (!window.monaco) return`` guards. Nothing threw; completion, hover and
   diagnostics were simply dead while the UI still offered them.
3. Config and manifest files were read with the platform locale encoding, so a
   non-UTF-8 host silently mis-decoded the Arabic and em-dashes in
   ``kazma.yaml`` and seeded the mojibake into ConfigStore.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _ROOT / "kazma-ui" / "kazma_ui" / "templates"
_IDE_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "ide.js"
_VENDOR = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "vendor" / "codemirror"

# Packages that ship to users. Test trees are exempt.
_SHIPPED = [
    _ROOT / "kazma-core" / "kazma_core",
    _ROOT / "kazma-ui" / "kazma_ui",
    _ROOT / "kazma-cli" / "kazma_cli",
    _ROOT / "kazma-tui" / "kazma_tui",
]

_EXTERNAL_ASSET = re.compile(
    r"""<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*["'](https?:)?//([^"']+)["']""",
    re.IGNORECASE,
)


def _templates() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def test_vendored_codemirror_bundle_is_present() -> None:
    """The bundle is committed, not fetched. Rebuild: scripts/vendor_codemirror.py."""
    js = _VENDOR / "codemirror.bundle.js"
    css = _VENDOR / "codemirror.bundle.css"
    assert js.is_file(), f"missing {js} - run python scripts/vendor_codemirror.py"
    assert css.is_file(), f"missing {css} - run python scripts/vendor_codemirror.py"
    # Upstream is MIT; shipping the licence with the code is the obligation.
    assert (_VENDOR / "LICENSE").is_file()
    assert (_VENDOR / "VERSION").is_file()
    # A truncated or empty bundle would still satisfy is_file().
    assert js.stat().st_size > 200_000, "bundle looks truncated"


def test_ide_loads_codemirror_from_local_vendor_dir() -> None:
    html = (_TEMPLATES / "ide.html").read_text(encoding="utf-8")
    assert "/static/vendor/codemirror/codemirror.bundle.js" in html
    assert "/static/vendor/codemirror/codemirror.bundle.css" in html


def test_no_template_loads_a_script_or_stylesheet_from_the_internet() -> None:
    """No CDN, anywhere. The whole UI must render with the network unplugged."""
    offenders: list[str] = []
    for tpl in _templates():
        for match in _EXTERNAL_ASSET.finditer(tpl.read_text(encoding="utf-8")):
            offenders.append(f"{tpl.relative_to(_ROOT).as_posix()}: {match.group(2)}")
    assert not offenders, "templates must not load remote assets:\n" + "\n".join(offenders)


def test_ide_js_has_no_monaco_references() -> None:
    """The editor is CodeMirror. A `monaco.` call here is unreachable by
    construction, so it is dead code advertising a feature that does nothing."""
    js = _IDE_JS.read_text(encoding="utf-8")
    # Property access / globals only. Prose mentioning the old editor in a
    # comment is history, not a call site.
    call_site = re.compile(r"\bmonaco\s*\.|\bwindow\.monaco\b|['\"]monaco['\"]")
    hits = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(js.splitlines(), 1)
        if call_site.search(line) and not line.lstrip().startswith(("*", "//", "/*"))
    ]
    assert not hits, "ide.js still references Monaco:\n" + "\n".join(hits)


def test_ide_editor_keeps_the_textarea_fallback() -> None:
    """If the bundle fails to load, the file must still be readable."""
    html = (_TEMPLATES / "ide.html").read_text(encoding="utf-8")
    js = _IDE_JS.read_text(encoding="utf-8")
    assert 'id="ide-fallback"' in html
    assert "fromTextArea" in js
    # Bail out to the plain textarea rather than throwing.
    assert "if (!ta || !window.CodeMirror) return;" in js


@pytest.mark.parametrize(
    "option",
    [
        "lineNumbers",
        "foldGutter",
        "matchBrackets",
        "autoCloseBrackets",
        "styleActiveLine",
        "highlightSelectionMatches",
        "rulers",
        "keyMap",
    ],
)
def test_editor_configures_professional_option(option: str) -> None:
    """The editor is meant to read like an IDE, not a <textarea> with stripes."""
    assert option in _IDE_JS.read_text(encoding="utf-8")


def test_indent_guides_are_installed() -> None:
    js = _IDE_JS.read_text(encoding="utf-8")
    html = (_TEMPLATES / "ide.html").read_text(encoding="utf-8")
    assert "_installIndentGuides" in js
    assert "cm-indent-guide" in js
    assert ".cm-indent-guide" in html, "guides are drawn by JS but styled in CSS"


# Same spelling, different object — neither is pathlib, so neither takes an
# encoding kwarg. Keyed by "<path>:<line>" to stay honest if the code moves.
_NOT_PATHLIB = {
    # ImportBundle.read_text(name) reads a member out of the migration bundle.
    "kazma-core/kazma_core/migration/importer.py:241",
}


def _calls_without_encoding(path: Path) -> list[str]:
    """Find .read_text(/.write_text( calls whose argument list omits encoding."""
    src = path.read_text(encoding="utf-8")
    found: list[str] = []
    for match in re.finditer(r"\.(read_text|write_text)\s*\(", src):
        # A call named inside a comment is documentation, not a call.
        line_start = src.rfind("\n", 0, match.start()) + 1
        if src[line_start : match.start()].lstrip().startswith("#"):
            continue
        open_paren = match.end() - 1
        depth = 0
        call = ""
        for j in range(open_paren, min(open_paren + 900, len(src))):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    call = src[open_paren : j + 1]
                    break
        if call and "encoding" not in call:
            line = src[: match.start()].count("\n") + 1
            ref = f"{path.relative_to(_ROOT).as_posix()}:{line}"
            if ref in _NOT_PATHLIB:
                continue
            found.append(f"{ref} {match.group(1)}")
    return found


def test_shipped_code_never_reads_or_writes_in_the_platform_locale() -> None:
    """``Path.read_text()`` with no encoding uses the platform locale.

    On a cp1252 Windows box that silently mis-decodes the UTF-8 in kazma.yaml
    (em-dashes, Arabic) instead of raising — and ConfigStore never overwrites a
    key it already seeded, so the corruption is permanent.
    """
    offenders: list[str] = []
    for root in _SHIPPED:
        for py in root.rglob("*.py"):
            if "tests" in py.parts or py.name.startswith("test_"):
                continue
            offenders.extend(_calls_without_encoding(py))
    assert not offenders, (
        "pass encoding='utf-8' explicitly:\n" + "\n".join(offenders)
    )
