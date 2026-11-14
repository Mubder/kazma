"""Every JavaScript file in the repository parses (scripts/check_js_syntax.py).

CI checked the web UI's scripts only, and ``loadtests/k6_swarm.js`` began with
eight lines of Python imports: k6 could not run it, and CodeQL reported it as a
file it could not read on every analysis (2026-11-14).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_js_syntax.py"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _module():
    spec = importlib.util.spec_from_file_location("check_js_syntax", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_tracked_javascript_file_parses():
    assert _module().main(["--node", NODE]) == 0


def test_the_enumeration_reaches_beyond_the_web_ui():
    names = {p.relative_to(REPO).as_posix() for p in _module().tracked_js()}
    assert "loadtests/k6_swarm.js" in names
    assert any(n.startswith("deploy/observer/") for n in names)
    assert not any("node_modules/" in n for n in names)


def test_control_the_old_k6_script_does_not_parse(tmp_path):
    old_head = ("import http\nimport ws\nimport json\nimport time\nimport random\n"
                "from k6 import check, sleep, fail\n")
    body = (REPO / "loadtests" / "k6_swarm.js").read_text(encoding="utf-8")
    old = tmp_path / "k6_swarm.js"
    old.write_text(old_head + body.split("\n", 3)[3], encoding="utf-8")
    assert _module().parse_error(NODE, old)


def test_a_module_written_as_js_is_read_as_a_module(tmp_path):
    k6_style = tmp_path / "load.js"
    k6_style.write_text("import { check } from 'k6';\nexport default function () { check(1, {}); }\n",
                        encoding="utf-8")
    assert _module().parse_error(NODE, k6_style) is None


def test_ci_runs_the_whole_repository_check():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    syntax_step = ci[ci.index("  js-check:"):ci.index("Run every JavaScript test file")]
    assert "scripts/check_js_syntax.py" in syntax_step
    assert "find kazma-ui/kazma_ui/static/js" not in syntax_step
