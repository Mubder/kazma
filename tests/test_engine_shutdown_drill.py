"""The shutdown drill's judging: what it reads, and the verdict it gives.

``scripts/engine_shutdown_drill.py`` runs on the host against the drill VM (the
engine set up, a writer container, Windows restarted; AGENTS.md section 63).
The VM part cannot run here; what it concludes can. ``clean`` only when every
round's writer got its stop signal, finished its stop and left no NUL byte,
and came back after the boot; the first measurement (no clean stop: NUL
bytes, no TERM), a stop cut short, a writer that never came back and a round
with nothing to read each give another verdict.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


drill = _load("engine_shutdown_drill_under_test", ROOT / "scripts" / "engine_shutdown_drill.py")


def _file(*, nul=0, term=True, done=True, last="DONE 1794539708"):
    return drill.WriterFile("w-boot.log", 1794539708, 9000, nul, term, done, last)


def test_the_reader_lines_become_files_oldest_first():
    text = ("noise\n"
            "FILE 1794539709 w-b.log 8652 0 1 1 DONE 1794539708\n"
            "FILE 1794535636 w-a.log 15238 760 0 0 line 303 1794535635\n"
            "FILE bad line\n")
    files = drill.parse_files(text)
    assert [f.name for f in files] == ["w-a.log", "w-b.log"]
    assert files[0].nul_bytes == 760 and not files[0].term and not files[0].done
    assert files[1].term and files[1].done and files[1].last_line == "DONE 1794539708"


def test_a_clean_restart_is_clean():
    rounds = [drill.Round(1, _file(), True), drill.Round(2, _file(), True)]
    assert drill.judge(rounds).verdict == "clean"


def test_the_first_measurement_is_unclean():
    """2026-11-13, before the fix: WSL's VM ended like a power cut; the writer
    never heard TERM and lost 760 bytes to NULs."""
    verdict = drill.judge([drill.Round(1, _file(nul=760, term=False, done=False, last="line 303"), True)])
    assert verdict.verdict == "unclean"
    assert any("760 NUL bytes" in r for r in verdict.reasons)
    assert any("never got its stop signal" in r for r in verdict.reasons)


def test_a_stop_cut_short_is_unclean():
    verdict = drill.judge([drill.Round(1, _file(done=False, last="stopping 31"), True)])
    assert verdict.verdict == "unclean" and "cut off while stopping" in verdict.reasons[0]


def test_a_writer_that_never_came_back_is_unclean():
    """A `docker stop` keeps an unless-stopped container down at the next
    boot; the daemon's own shutdown does not."""
    verdict = drill.judge([drill.Round(1, _file(), False)])
    assert verdict.verdict == "unclean" and "did not start again" in verdict.reasons[0]


def test_nothing_to_read_is_no_verdict():
    assert drill.judge([]).verdict == "invalid"
    assert drill.judge([drill.Round(1, None, True)]).verdict == "invalid"


def test_how_long_the_shutdown_took_is_read_from_windows_own_events():
    windows = ["SYS 2026-11-13T03:14:58.2570934+00:00 1074",
               "SVC 2026-11-13T03:14:59.3804261+00:00 Windows is shutting down: stopping Kazma's engine",
               "SYS 2026-11-13T03:15:08.4421135+00:00 6006",
               "SYS 2026-11-13T03:15:15.5424499+00:00 6005"]
    assert round(drill.held_seconds(windows), 1) == 10.2
    assert drill.held_seconds(windows[1:]) is None, "no start, no length"


def test_the_report_says_the_verdict_and_each_round():
    rounds = [drill.Round(1, _file(nul=760, term=False, done=False), True,
                          ["SVC 2026-11-13T03:14:59Z Windows is shutting down: stopping Kazma's engine"], 2.1)]
    text = drill.render_report(label="control", control=True, verdict=drill.judge(rounds), rounds=rounds,
                               meta={"commit": "a8df8cc46", "vm": "KazmaVMs", "checkpoint": "clean"},
                               evidence_name="x.json")
    assert "**unclean**" in text and "must be `unclean`" in text and "| 1 | `w-boot.log`" in text
    assert "Windows is shutting down" in text


def test_the_writer_stops_as_slowly_as_neo4j_did():
    assert drill.STOP_TENTHS >= 50, "a stop of several seconds, as Neo4j's was"
    assert "trap term TERM" in drill.WRITER_SCRIPT and "sleep 0.1" in drill.WRITER_SCRIPT
    assert "\r" not in drill.WRITER_SCRIPT + drill.READ_SCRIPT
