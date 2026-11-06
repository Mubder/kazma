"""A script's JSON on the console survives any console encoding.

A Windows console, or a pipe Python writes to without ``PYTHONUTF8``, takes
cp1252. ``print(json.dumps(..., ensure_ascii=False))`` of a report holding
one character outside it raised UnicodeEncodeError after the work was done:
the Scheduled soak's 7-day run passed and then crashed printing its report
on "⏰" (2026-11-06), so its exit code said failed. JSON escaped to ASCII
reads back to the same value everywhere; the report files a script writes
keep UTF-8.

The gate: no ``print`` in ``scripts/`` sends ``json.dumps(...,
ensure_ascii=False)`` to the console (stdout or stderr), unless the script
first reconfigures its stream to UTF-8 (``sys.stdout.reconfigure``). A print
to a file it opened is left alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _console_prints_of_unescaped_json(source: str) -> list[int]:
    tree = ast.parse(source)
    if "reconfigure(" in source and "utf-8" in source.lower():
        return []
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        stream = next((k.value for k in node.keywords if k.arg == "file"), None)
        if stream is not None and not (isinstance(stream, ast.Attribute) and stream.attr in ("stdout", "stderr")):
            continue  # a file the script opened
        for arg in node.args:
            for sub in ast.walk(arg):
                if (isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "dumps"
                        and any(k.arg == "ensure_ascii" and isinstance(k.value, ast.Constant)
                                and k.value.value is False for k in sub.keywords)):
                    found.append(node.lineno)
    return found


def test_no_script_prints_unescaped_json_to_the_console():
    found = {}
    for path in sorted((REPO / "scripts").rglob("*.py")):
        lines = _console_prints_of_unescaped_json(path.read_text(encoding="utf-8"))
        if lines:
            found[path.relative_to(REPO).as_posix()] = lines
    assert found == {}, found


def test_negative_control_the_soaks_old_print_is_caught():
    old = ("import json, sys\n"
           "print(json.dumps({'a': 1}, indent=2, ensure_ascii=False))\n"
           "print('x' + json.dumps({}, ensure_ascii=False), file=sys.stderr)\n"
           "with open('r.json', 'w', encoding='utf-8') as fh:\n"
           "    print(json.dumps({}, ensure_ascii=False), file=fh)\n")
    assert _console_prints_of_unescaped_json(old) == [2, 3]
