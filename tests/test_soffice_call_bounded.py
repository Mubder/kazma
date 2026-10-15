"""A LibreOffice call that times out returns, whatever its children hold.

2026-10-13. The full test run hung in the document certification: the
parser registry's 8 s ``soffice --version`` probe timed out under load, and
``subprocess.run(timeout=)`` then killed only the process it had started and
waited WITHOUT a limit for the output pipes -- which soffice.com's own child,
soffice.bin, still held. ``binaries.run_soffice_cli`` now kills the whole
tree and bounds the last wait. The stand-in "soffice" here is a Python
process whose child holds the pipes for 20 s. Negative control: the old call
waits for that child.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from kazma_core.documents import binaries

_HOLDER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
    " stdout=sys.stdout, stderr=sys.stderr)\n"
    "time.sleep(20)\n"
)


def test_a_timed_out_call_returns_while_a_child_holds_its_pipes(monkeypatch):
    monkeypatch.setattr(binaries, "find_soffice", lambda: sys.executable)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        binaries.run_soffice_cli(["-c", _HOLDER], timeout=1)
    assert time.monotonic() - started < 1 + binaries._DRAIN_SECONDS + 2


def test_a_finished_call_returns_its_output(monkeypatch):
    monkeypatch.setattr(binaries, "find_soffice", lambda: sys.executable)
    result = binaries.run_soffice_cli(["-c", "print('LibreOffice 26.2')"], timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == "LibreOffice 26.2"


def _stand_in_soffice(tmp_path):
    """An executable that takes LibreOffice's arguments and runs _HOLDER."""
    script = tmp_path / "holder.py"
    script.write_text(_HOLDER, encoding="utf-8")
    if sys.platform == "win32":
        exe = tmp_path / "soffice.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "soffice"
        exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n", encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


def test_the_legacy_office_parser_is_bounded_too(tmp_path):
    """The .doc/.xls/.ppt parser ran LibreOffice with subprocess.run(timeout=)
    itself, so its timeout waited for the pipes soffice's children held (Q04
    defensive check C11, 2026-10-15). It goes through run_soffice_cli now."""
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.parsers.common import ParseContext
    from kazma_core.documents.parsers.ooxml import LegacyOfficeParser

    source = tmp_path / "old.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0legacy")
    config = DocumentConfig(storage_root=tmp_path / "store", worker_timeout_seconds=6)
    context = ParseContext(config=config, source_sha256="0" * 64, mime_type="application/msword",
                           extension=".doc", parser_id="legacy", parser_version="1")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        LegacyOfficeParser(_stand_in_soffice(tmp_path)).parse(source, context)
    assert time.monotonic() - started < 5 + binaries._DRAIN_SECONDS + 4


#: Functions that name LibreOffice and start another program directly, with
#: why. A declaration fails when its function no longer does.
_OTHER_PROGRAMS = {
    ("kazma-core/kazma_core/documents/registry.py", "_binary_versions"):
        "probes non-LibreOffice binaries (Tesseract...) in its else branch; "
        "its LibreOffice branch calls run_soffice_cli",
    ("kazma-core/kazma_core/documents/renderers/__init__.py", "_probe_binary_version"):
        "the same: run_soffice_cli for LibreOffice, a plain version probe for other renderers",
}


def _direct_process_calls(source: str) -> list[tuple[str, int]]:
    """(enclosing function, line) of every process the subprocess module
    starts directly."""
    import ast

    tree = ast.parse(source)
    named = {alias.asname or alias.name for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom) and node.module == "subprocess" for alias in node.names}
    starters = {"run", "Popen", "call", "check_call", "check_output"}
    found = []

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call):
                func = child.func
                if ((isinstance(func, ast.Attribute) and func.attr in starters
                     and isinstance(func.value, ast.Name) and func.value.id == "subprocess")
                        or (isinstance(func, ast.Name) and func.id in starters and func.id in named)):
                    found.append((function, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def test_every_libreoffice_call_goes_through_the_runner():
    """A product module that names LibreOffice starts no process itself:
    binaries.run_soffice_cli is the one place that does, so every call gets
    its tree kill and its bounded wait."""
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    names = re.compile(r"soffice|libreoffice", re.IGNORECASE)
    allowed = {repo / "kazma-core/kazma_core/documents/binaries.py"}
    offenders, declared_seen = [], set()
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for path in (repo / package).rglob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not names.search(text):
                continue
            rel = path.relative_to(repo).as_posix()
            for function, line in _direct_process_calls(text):
                if (rel, function) in _OTHER_PROGRAMS:
                    declared_seen.add((rel, function))
                else:
                    offenders.append(f"{rel}:{line} ({function})")
    assert offenders == [], offenders
    assert declared_seen == set(_OTHER_PROGRAMS), set(_OTHER_PROGRAMS) - declared_seen

    # Negative control: the legacy parser as it was before 2026-10-15.
    old = subprocess.run(["git", "-C", str(repo), "show",
                          "83a89b4f:kazma-core/kazma_core/documents/parsers/ooxml.py"],
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode == 0:
        assert names.search(old.stdout) and _direct_process_calls(old.stdout)


def test_the_old_call_waits_for_the_child():
    # Negative control: what the probe used to do.
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c", _HOLDER], capture_output=True, timeout=1,
                       text=True, stdin=subprocess.DEVNULL)
    assert time.monotonic() - started >= 15
