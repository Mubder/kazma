"""The CodeMirror build refuses a bundle whose files load before what they need.

The vendored bundle is one script. On 2026-09-26 its Rust and Dockerfile modes
called ``CodeMirror.defineSimpleMode`` from ``addon/mode/simple.js``, which was
not in the build list: the throw at load stopped the script, so every mode
after Rust never registered (Go, Ruby, Dockerfile, TOML, diff, PowerShell,
properties, Lua) and every IDE load logged "defineSimpleMode is not a
function". ``scripts/vendor_codemirror.py`` now reads each file's
``require()`` header and fails the build instead. The committed bundle's
outcome (every listed mode registered) is checked in a browser by
``tests/e2e/test_pages_load_clean.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vendor_codemirror.py"


@pytest.fixture(scope="module")
def vendor():
    spec = importlib.util.spec_from_file_location("vendor_codemirror", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _umd(*requires: str) -> str:
    """A UMD header as CodeMirror 5 writes one."""
    reqs = ", ".join(f'require("{r}")' for r in requires)
    return (
        "(function(mod) {\n"
        '  if (typeof exports == "object" && typeof module == "object")\n'
        f"    mod({reqs});\n"
        "})(function(CodeMirror) {});\n"
    )


SOURCES = {
    "lib/codemirror.js": "(function (global, factory) { factory(); })(this, function () {});",
    "addon/mode/simple.js": _umd("../../lib/codemirror"),
    "mode/meta.js": _umd("../lib/codemirror"),
    "mode/xml/xml.js": _umd("../../lib/codemirror"),
    "mode/markdown/markdown.js": _umd("../../lib/codemirror", "../xml/xml", "../meta"),
    "mode/rust/rust.js": _umd("../../lib/codemirror", "../../addon/mode/simple"),
}


def test_requires_resolve_to_package_paths(vendor) -> None:
    assert vendor.module_dependencies("mode/rust/rust.js", SOURCES["mode/rust/rust.js"]) == [
        "lib/codemirror.js",
        "addon/mode/simple.js",
    ]
    assert "mode/meta.js" in vendor.module_dependencies(
        "mode/markdown/markdown.js", SOURCES["mode/markdown/markdown.js"]
    )
    # A bare module name is not a file of the package.
    assert vendor.module_dependencies("x/y.js", 'require("codemirror")') == []


def test_a_well_ordered_list_passes(vendor) -> None:
    names = ["lib/codemirror.js", "addon/mode/simple.js", "mode/meta.js",
             "mode/xml/xml.js", "mode/markdown/markdown.js", "mode/rust/rust.js"]
    assert vendor.dependency_problems(names, SOURCES) == []


def test_negative_control_the_list_that_shipped_is_refused(vendor) -> None:
    shipped = ["lib/codemirror.js", "mode/meta.js", "mode/xml/xml.js",
               "mode/markdown/markdown.js", "mode/rust/rust.js"]
    assert vendor.dependency_problems(shipped, SOURCES) == [
        "mode/rust/rust.js requires addon/mode/simple.js (not bundled)"
    ]


def test_a_dependency_listed_after_its_user_is_refused(vendor) -> None:
    late = ["lib/codemirror.js", "mode/meta.js", "mode/markdown/markdown.js",
            "mode/xml/xml.js"]
    assert vendor.dependency_problems(late, SOURCES) == [
        "mode/markdown/markdown.js requires mode/xml/xml.js (after it)"
    ]


def test_the_build_list_carries_the_simple_mode_addon_before_its_users(vendor) -> None:
    files = vendor.JS_FILES
    simple = files.index("addon/mode/simple.js")
    assert simple < files.index("mode/rust/rust.js")
    assert simple < files.index("mode/dockerfile/dockerfile.js")
