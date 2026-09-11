#!/usr/bin/env python
"""Vendor CodeMirror 5 into ``kazma-ui/kazma_ui/static/vendor/codemirror/``.

The IDE must work on a box with no route to the public internet — that is the
whole promise of a self-hosted agent. So CodeMirror ships with Kazma instead of
being fetched from a CDN at page load.

This script pulls the exact pinned version from npm, concatenates the pieces we
actually use into one JS bundle and one CSS bundle, and writes them next to the
upstream LICENSE. Two files, two ``<script>``/``<link>`` tags, no network at
runtime.

Run it only when bumping ``CM_VERSION``; the output is committed.

    python scripts/vendor_codemirror.py

Order in the lists below is load order and it matters:
``lib/codemirror.js`` defines the global, ``xml-fold`` must precede
``closetag``, ``annotatescrollbar`` must precede ``matchesonscrollbar``, and a
mode that embeds another (``htmlmixed``, ``php``, ``jsx``, ``markdown``) must
come after the modes it embeds.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

CM_VERSION = "5.65.16"

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "kazma-ui" / "kazma_ui" / "static" / "vendor" / "codemirror"

CSS_FILES = [
    "lib/codemirror.css",
    "addon/fold/foldgutter.css",
    "addon/lint/lint.css",
    "addon/dialog/dialog.css",
    "addon/hint/show-hint.css",
    "addon/scroll/simplescrollbars.css",
    "addon/search/matchesonscrollbar.css",
    "theme/nord.css",
    "theme/eclipse.css",
]

JS_FILES = [
    # Core must be first — everything else attaches to the global it defines.
    "lib/codemirror.js",
    # Folding. xml-fold also supplies the tag scanner that closetag needs.
    "addon/fold/foldcode.js",
    "addon/fold/foldgutter.js",
    "addon/fold/brace-fold.js",
    "addon/fold/xml-fold.js",
    "addon/fold/indent-fold.js",
    "addon/fold/comment-fold.js",
    "addon/fold/markdown-fold.js",
    # Editing behaviour.
    "addon/edit/matchbrackets.js",
    "addon/edit/closebrackets.js",
    "addon/edit/closetag.js",
    "addon/edit/continuelist.js",
    "addon/edit/trailingspace.js",
    "addon/comment/comment.js",
    # Diagnostics gutter — backs the /api/ide/lsp diagnostics feed.
    "addon/lint/lint.js",
    # Selection rendering.
    "addon/selection/active-line.js",
    "addon/selection/mark-selection.js",
    # Search. annotatescrollbar backs matchesonscrollbar, which backs the
    # match highlighter's scrollbar ticks.
    "addon/dialog/dialog.js",
    "addon/search/searchcursor.js",
    "addon/search/search.js",
    "addon/search/jump-to-line.js",
    "addon/scroll/annotatescrollbar.js",
    "addon/search/matchesonscrollbar.js",
    "addon/search/match-highlighter.js",
    # Display.
    "addon/scroll/simplescrollbars.js",
    "addon/display/rulers.js",
    "addon/display/placeholder.js",
    # Completion.
    "addon/hint/show-hint.js",
    "addon/hint/anyword-hint.js",
    # Sublime bindings are what make it feel like an editor people already know
    # (Ctrl-D multi-select, Ctrl-/ comment, Alt-Up/Down move line).
    "keymap/sublime.js",
    # Modes. meta.js gives findModeByExtension / findModeByName.
    "mode/meta.js",
    "mode/xml/xml.js",
    "mode/javascript/javascript.js",
    "mode/css/css.js",
    "mode/htmlmixed/htmlmixed.js",
    "mode/clike/clike.js",
    "mode/php/php.js",
    "mode/jsx/jsx.js",
    "mode/python/python.js",
    "mode/markdown/markdown.js",
    "mode/yaml/yaml.js",
    "mode/shell/shell.js",
    "mode/sql/sql.js",
    "mode/rust/rust.js",
    "mode/go/go.js",
    "mode/ruby/ruby.js",
    "mode/dockerfile/dockerfile.js",
    "mode/toml/toml.js",
    "mode/diff/diff.js",
    "mode/powershell/powershell.js",
    "mode/properties/properties.js",
    "mode/lua/lua.js",
]


def _fetch(workdir: Path) -> Path:
    """npm pack the pinned version and return the extracted package dir."""
    print(f"npm pack codemirror@{CM_VERSION} ...")
    proc = subprocess.run(
        ["npm", "pack", f"codemirror@{CM_VERSION}", "--silent"],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        shell=(sys.platform == "win32"),
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"npm pack failed:\n{proc.stdout}\n{proc.stderr}")

    tarballs = list(workdir.glob("codemirror-*.tgz"))
    if not tarballs:
        raise SystemExit("npm pack produced no tarball")
    with tarfile.open(tarballs[0]) as tf:
        tf.extractall(workdir, filter="data")  # noqa: S202 - npm registry tarball
    pkg = workdir / "package"
    if not pkg.is_dir():
        raise SystemExit("unexpected tarball layout: no package/ dir")
    return pkg


def _minify_js(text: str, workdir: Path) -> str:
    """Run the bundle through terser. Falls back to the raw text if absent.

    The UI serves static files without compression, so an unminified bundle is
    ~900 KB on the wire. Minifying is not cosmetic here.
    """
    src = workdir / "bundle.js"
    src.write_text(text, encoding="utf-8")
    out = workdir / "bundle.min.js"
    proc = subprocess.run(
        ["npx", "--yes", "terser@5", str(src), "-c", "-m", "-o", str(out)],
        capture_output=True,
        text=True,
        shell=(sys.platform == "win32"),
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        print("  terser unavailable — shipping unminified")
        print(f"  {proc.stderr.strip()[:200]}")
        return text
    minified = out.read_text(encoding="utf-8")
    print(f"  minified: {len(text) // 1024} KB -> {len(minified) // 1024} KB")
    return minified


def _bundle(pkg: Path, names: list[str], kind: str) -> str:
    parts = [
        f"/*! CodeMirror {CM_VERSION} — vendored for Kazma (MIT). "
        f"Rebuild: python scripts/vendor_codemirror.py */\n"
    ]
    for name in names:
        src = pkg / name
        if not src.is_file():
            raise SystemExit(f"missing from upstream package: {name}")
        parts.append(f"\n/* ---- {name} ---- */\n")
        parts.append(src.read_text(encoding="utf-8"))
        parts.append("\n")
    text = "".join(parts)
    print(f"  {kind}: {len(names)} files, {len(text) // 1024} KB")
    return text


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _fetch(Path(tmp))
        js = _minify_js(_bundle(pkg, JS_FILES, "js "), Path(tmp))
        css = _bundle(pkg, CSS_FILES, "css")
        license_text = (pkg / "LICENSE").read_text(encoding="utf-8")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "codemirror.bundle.js").write_text(js, encoding="utf-8")
        (OUT_DIR / "codemirror.bundle.css").write_text(css, encoding="utf-8")
        (OUT_DIR / "LICENSE").write_text(license_text, encoding="utf-8")
        (OUT_DIR / "VERSION").write_text(CM_VERSION + "\n", encoding="utf-8")

    print(f"wrote {OUT_DIR.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
