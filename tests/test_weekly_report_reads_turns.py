"""The weekly report counts the turns Kazma finished (2026-11-11).

The weekly resilience report's "Turns that succeed" target read "unknown"
every week it ran, over weeks of turns: ``close_turn`` writes
``failed=yes|no`` and the targets' own reader matched ``failed=True|False``.
Its test passed because its fixture wrote ``True``/``False`` too -- a line no
code writes. The daily digest had a reader of its own that agreed with the
writer. Now there is one reader (``observability.turn_line``); these tests
feed it the line the real ``close_turn`` writes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from kazma_core.observability.turn_line import read_turn_line
from kazma_core.observability.weekly_targets import measure_targets

REPO = Path(__file__).resolve().parents[1]
_TURN = [{"role": "user", "content": "book the table"},
         {"role": "assistant", "content": "Booked for 8."}]
#: The targets' reader before 2026-11-11.
_OLD_PATTERN = re.compile(r"\[turn\] Turn finished: platform=\S* key=(\S+) failed=(True|False)")


class _Graph:
    def __init__(self, values):
        self.snap = SimpleNamespace(values=values, next=(), tasks=[])

    async def aget_state(self, config):
        return self.snap


def _real_lines(caplog, monkeypatch, tmp_path) -> list[str]:
    """One finished and one failed turn, closed by the real ``close_turn``."""
    from kazma_ui.turn_runtime import close_turn

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.INFO, logger="kazma_ui.turn_runtime")
    for failed in (False, True):
        thread = f"web-{uuid.uuid4().hex[:8]}"
        values = {"messages": list(_TURN), "_gateway": {"platform": "web"}, "turn_failed": failed}
        asyncio.run(close_turn(_Graph(values), {"configurable": {"thread_id": thread}}, thread_id=thread))
    return [r.getMessage() for r in caplog.records
            if r.name == "kazma_ui.turn_runtime" and "Turn finished" in r.getMessage()]


def _as_app_log(messages: list[str], at: float) -> list[str]:
    import json

    stamp = datetime.fromtimestamp(at, UTC).isoformat()
    return [json.dumps({"timestamp": stamp, "level": "INFO", "message": m}) for m in messages]


def test_the_targets_count_the_turns_close_turn_writes(caplog, monkeypatch, tmp_path):
    lines = _real_lines(caplog, monkeypatch, tmp_path)
    assert len(lines) == 2, lines
    assert [read_turn_line(m)[2] for m in lines] == [False, True]
    now = datetime.now(UTC).timestamp()
    targets = {t.name: t for t in measure_targets(_as_app_log(lines, now - 3600), [], now=now)}
    assert targets["Turns that succeed"].shown == "50.0%"
    assert targets["Turns that succeed"].met is False


def test_the_old_reader_counted_none_of_them(caplog, monkeypatch, tmp_path):
    """Negative control: the pattern the targets used finds no real line."""
    lines = _real_lines(caplog, monkeypatch, tmp_path)
    assert lines and not any(_OLD_PATTERN.search(m) for m in lines)


# -- one reader -------------------------------------------------------------


def _parsers_of_the_turn_line(sources: dict[str, str]) -> list[str]:
    """Product files that build their own pattern for the finished-turn line."""
    return sorted(path for path, text in sources.items()
                  if not path.endswith("observability/turn_line.py")
                  and re.search(r"re\.compile\([^)]*Turn finished", text))


def test_only_turn_line_reads_the_finished_turn_line():
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/*.py", "kazma-*/kazma_*/**/*.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    sources = {p: (REPO / p).read_text(encoding="utf-8", errors="replace") for p in listed}
    assert len(sources) > 500, "the source walk found almost nothing"
    assert _parsers_of_the_turn_line(sources) == []
    # Negative control: a second reader is found.
    planted = {"kazma-core/kazma_core/x.py": 'R = re.compile(r"\\[turn\\] Turn finished: (\\S+)")'}
    assert _parsers_of_the_turn_line(planted) == ["kazma-core/kazma_core/x.py"]
