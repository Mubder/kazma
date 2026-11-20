"""The restart drill's judging (``scripts/restart_drill.py``): pure, so it is
held here; the drill itself runs on the drill VM.

The shapes are the 2026-11-20 restart's: the boot before at 12:03, the
restart asked at 13:22:43, Windows' event log stopped at 13:23:20.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import restart_drill as drill  # noqa: E402

BOOT = 1795176193.4
ASKED = 1795180963.0
STOPPED = 1795181000.0
NEW_BOOT = 1795181196.9


def _line(at: float, message: str) -> str:
    from datetime import UTC, datetime

    return json.dumps({"timestamp": datetime.fromtimestamp(at, UTC).isoformat(), "level": "INFO",
                       "message": message})


def _evidence(*, stop_line: bool = True, told: bool = True) -> dict:
    log = [_line(ASKED + 1.4, "[shutdown-notice] Windows is shutting down: Kazma stops now")]
    if stop_line:
        log.append(_line(ASKED + 6.1, "[shutdown] the stop took 4.6 s of its 50 s budget"))
    service = [{"at": ASKED + 1.2, "text": "Windows is shutting down: stopping Kazma (1 server)"}]
    if told:
        service.append({"at": ASKED + 6.9, "text": "Kazma stopped after 5.7 s"})
    return {"system": [{"id": 1074, "at": ASKED}, {"id": 6006, "at": STOPPED}, {"id": 6005, "at": STOPPED + 50}],
            "service": service, "log": log}


def test_a_stop_recorded_in_time_is_clean() -> None:
    verdict = drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": NEW_BOOT, "stop": ASKED + 5.9}, _evidence())
    assert verdict.verdict == "clean", verdict.reasons
    assert verdict.held_s == round(STOPPED - ASKED, 1)
    assert any("Kazma stopped after" in r for r in verdict.reasons)


def test_control_the_restart_of_2026_11_20_is_unclean() -> None:
    """What live recorded: the stamp is the reload's of 12:02, before the
    boot; the server logged only "Shutdown signal sent" at the very end."""
    evidence = _evidence(stop_line=False, told=False)
    evidence["log"] = [_line(STOPPED, "Shutdown signal sent — terminating all streams")]
    verdict = drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": NEW_BOOT, "stop": BOOT - 17.0}, evidence)
    assert verdict.verdict == "unclean", verdict.reasons


def test_a_stamp_without_the_servers_own_stop_line_is_not_clean() -> None:
    verdict = drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": NEW_BOOT, "stop": ASKED + 5.9},
                          _evidence(stop_line=False))
    assert verdict.verdict == "unclean"


def test_no_restart_or_no_new_boot_is_invalid() -> None:
    no_restart = _evidence()
    no_restart["system"] = [{"id": 1074, "at": ASKED}]
    assert drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": NEW_BOOT, "stop": None}, no_restart).verdict == \
        "invalid"
    assert drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": BOOT, "stop": ASKED + 5.9}, _evidence()).verdict == \
        "invalid"


def test_the_install_leaves_out_every_express_part_but_the_service() -> None:
    from kazma_core.install_components import express_ids

    assert set(express_ids("windows")) - set(drill.WITHOUT) == {"service"}
    filled = drill._fill(drill.RUN_INSTALL)
    assert "--with postgres" in filled and "--mode custom" in filled
    assert all(f"--without {part}" in filled for part in drill.WITHOUT)


def test_the_report_says_the_verdict() -> None:
    verdict = drill.judge({"boot": BOOT, "asked": ASKED}, {"boot": NEW_BOOT, "stop": ASKED + 5.9}, _evidence())
    text = drill.render_report(label="fix", verdict=verdict, meta={"commit": "a" * 40, "finished": "now"},
                               evidence_name="e.json")
    assert "**clean**" in text and "e.json" in text
