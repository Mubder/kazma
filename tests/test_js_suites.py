"""Every JavaScript test file runs with the suite.

CI ran seven of them by name (the unified-turn job) and a few Python tests ran
a handful more; the other 24 -- among them the gates AGENTS.md names for the
SSE parser (§31), the login redirect check (AUD-019), Web Push arming
(AUD-026), the modal store, the settings mixins and saves, locale formatting,
the namespace members every page reads, and the MCP page -- ran only when
someone started node by hand (found 2026-09-30; all 45 passed that day, in
9 s). This runs each file, enumerated from the tree, so a new one runs
without being listed; CI's ``js-check`` job runs the same files under the
node it sets up.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def js_test_files() -> list[Path]:
    """Every JavaScript test file: ``tests/js/test_*.js`` and ``tests/test_*.js``."""
    return sorted({*REPO.glob("tests/js/test_*.js"), *REPO.glob("tests/test_*.js")})


def run_js(path: Path) -> subprocess.CompletedProcess[str]:
    # Node writes UTF-8. Decoded with the Windows code page, a test that
    # prints Arabic (test_block_direction.js) lost its whole output to a
    # UnicodeDecodeError in the reader thread -- a failure there would have
    # been reported with nothing to read.
    return subprocess.run(
        ["node", str(path)], cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240, check=False,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", js_test_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_javascript_test_file_passes(path: Path) -> None:
    result = run_js(path)
    assert result.returncode == 0, (
        f"{path.relative_to(REPO)} failed (exit {result.returncode}):\n"
        f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    )


def test_the_list_holds_the_files_that_never_ran() -> None:
    """The files found unrun on 2026-09-30 are in the list (the glob sees both folders)."""
    names = {p.name for p in js_test_files()}
    for name in ("test_sse_parser.js", "test_safe_next.js", "test_push_arming.js", "test_mcp_add_server.js"):
        assert name in names, name


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_failing_file_fails(tmp_path: Path) -> None:
    """Negative control: a file that exits non-zero is a failure, not a pass."""
    bad = tmp_path / "test_bad.js"
    bad.write_text("console.log('x'); process.exit(3);\n", encoding="utf-8")
    thrown = tmp_path / "test_throws.js"
    thrown.write_text("throw new Error('boom');\n", encoding="utf-8")
    assert run_js(bad).returncode == 3
    assert run_js(thrown).returncode != 0


def test_ci_runs_every_javascript_test_file() -> None:
    """The js-check job runs the same globs, under the node it installs."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "tests/js/test_*.js tests/test_*.js" in ci
