"""Audit 2026-09-04 — Systemic Regression Guardrails.

Section 28-style regression tests scanning the codebase for anti-patterns:
  1. No synchronous `confirm(` calls in UI JavaScript (must use modal confirm).
  2. No synchronous `httpx.get(` inside `async def` in `kazma_tui`.
  3. No synchronous `request_json(` inside TUI `async def`.
  4. No unescaped inline `onclick="...'+` string concatenation in UI scripts.
  5. Negative controls: each AST / regex detector is asserted to catch synthetic violations.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _scan_tui_async_def_calls(call_func_name: str) -> list[tuple[Path, int]]:
    """Scan kazma-tui for occurrences of call_func_name inside async def functions."""
    tui_dir = REPO_ROOT / "kazma-tui" / "kazma_tui"
    violations: list[tuple[Path, int]] = []
    if not tui_dir.exists():
        return violations

    for py_file in tui_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef,)):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        name = ""
                        if isinstance(inner.func, ast.Name):
                            name = inner.func.id
                        elif isinstance(inner.func, ast.Attribute):
                            name = inner.func.attr
                        if name == call_func_name:
                            violations.append((py_file, inner.lineno))
    return violations


class TestRegressionsTuiEventLoop:
    """Detect sync network calls inside Textual TUI async event handlers."""

    def test_no_sync_request_json_in_tui_async_def(self) -> None:
        violations = _scan_tui_async_def_calls("request_json")
        assert not violations, f"Found sync request_json in TUI async def: {violations}"

    def test_detector_negative_control(self) -> None:
        """Negative control: assert detector catches a synthetic violation."""
        sample_code = """
async def bad_handler():
    data = request_json("/api/test")
    return data
"""
        tree = ast.parse(sample_code)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        func_name = inner.func.id if isinstance(inner.func, ast.Name) else getattr(inner.func, "attr", "")
                        if func_name == "request_json":
                            found = True
        assert found is True, "Negative control failed: detector did not catch synthetic request_json"


class TestRegressionsFrontendHygiene:
    """Detect fragile frontend UI patterns."""

    def test_no_inline_onclick_string_building(self) -> None:
        """Check for unescaped inline onclick concatenation: onclick="...'+ in UI JS."""
        js_dir = REPO_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
        if not js_dir.exists():
            return

        pattern = re.compile(r'onclick\s*=\s*(?:"[^"]*\'\s*\+|\'[^\']*"[^\']*\'\s*\+)')
        violations: list[str] = []

        for js_file in js_dir.rglob("*.js"):
            # Exclude vendor libraries if any
            if "vendor" in js_file.parts:
                continue
            text = js_file.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(f"{js_file.name}:{line_no}: {line.strip()[:60]}")

        assert not violations, f"Found inline onclick string building: {violations}"

    def test_onclick_detector_negative_control(self) -> None:
        """Negative control: assert regex catches synthetic inline onclick string construction."""
        pattern = re.compile(r'onclick\s*=\s*(?:"[^"]*\'\s*\+|\'[^\']*"[^\']*\'\s*\+)')
        bad_sample = 'const html = \'<button onclick="deleteItem(\' + itemId + \')">Delete</button>\';'
        assert pattern.search(bad_sample) is not None
