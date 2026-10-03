"""Every line the product code can log, read from the source.

Shared by the gates that check a log reader against its writers: the weekly
firing ledger's signatures (tests/test_backup_silent_failures.py) and the
daily digest's markers (tests/test_daily_digest.py). A reader that watches a
line no code writes counts nothing, forever, and looks like a quiet day.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
#: Where a result summary is spliced into a rendered format string.
SLOT = "\x00"


def render(node, fill: str = "1") -> str | None:
    """A logger format string as it would print, placeholders filled with
    ``fill`` ("1" satisfies both \\d and \\w in a pattern)."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.sub(r"%[-#0 +]*\d*(?:\.\d+)?[sdrfix]", fill, node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else fill for v in node.values
        )
    return None


def real_summaries() -> list[str]:
    """What the result objects logged as ``"[tag] %s", res.summary()`` print."""
    from kazma_core.backup.restore import RestoreResult
    from kazma_core.backup.restore_drill import DrillResult

    out = []
    for status in (True, False, None):
        d = DrillResult(backup_dir="(deep)")
        d.add("check", status, "detail")
        out.append(d.summary())
    for ok in (True, False):
        r = RestoreResult(ok=ok, target="t", generation=1)
        r.add("step", ok)
        out.append(r.summary())
    return out


def func_name(node) -> str:
    import ast

    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def emitted_app_lines(roots) -> list[str]:
    """Every line the code can log: logger format strings, ops-alert keys
    (alert() logs "[ops_alert] <key> | <title>"), and result summaries."""
    import ast

    summaries = real_summaries()
    lines: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "_tests" in path.parts or "tests" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = func_name(node)
                if name in LOG_METHODS and isinstance(node.func, ast.Attribute):
                    text = render(node.args[0])
                    if text:
                        lines.append(text)
                    if any(
                        isinstance(a, ast.Call) and func_name(a) == "summary"
                        for a in node.args[1:]
                    ):
                        slotted = render(node.args[0], fill=SLOT) or ""
                        lines += [slotted.replace(SLOT, s, 1).replace(SLOT, "1") for s in summaries]
                elif name in ("alert", "_alert"):
                    key = node.args[0]
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        lines.append(f"[ops_alert] {key.value} | 1")
    return lines


def guard_events() -> set[str]:
    import ast

    tree = ast.parse((REPO / "scripts" / "service" / "kazma_guard.py").read_text(encoding="utf-8"))
    events: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and re.fullmatch(r"[a-z_]+\.[a-z_]+", node.args[1].value)
        ):
            events.add(node.args[1].value)
    return events


def product_roots() -> list[Path]:
    """The package folders and ``scripts/`` -- where emitting code lives."""
    return [p for p in REPO.glob("kazma-*/kazma_*") if p.is_dir()] + [REPO / "scripts"]
