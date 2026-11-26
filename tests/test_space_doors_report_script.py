"""``scripts/space_doors_report.py`` reads a door report beside the lists
(Spaces S4-01, step 1): a finding the lists know is ``known`` with its step,
any other is new, and ``--strict`` fails on a new one."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests import _space_door_lists

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "space_doors_report.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("space_doors_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "space_doors.json"
    path.write_text(json.dumps({"version": 1, "since": "2026-11-26T00:00:00+00:00",
                                "updated": "2026-11-26T01:00:00+00:00", "checkouts": 9, "dropped": 0,
                                "failures": 0, "entries": list(entries)}), encoding="utf-8")
    return path


def _entry(door: str, callers: list[str], how: str = "nothing", reason: str = "", count: int = 1,
           pool: str = "shared") -> dict:
    return {"pool": pool, "door": door, "how": how, "reason": reason, "callers": callers, "count": count,
            "first": "2026-11-26T00:00:00+00:00", "last": "2026-11-26T00:30:00+00:00"}


OPEN = "kazma-ui/kazma_ui/session_manager.py::SessionManager._load_all_from_primary"
UNKNOWN = "kazma-core/kazma_core/documents/jobs_pg.py::PostgresDocumentJobRepository.tenant_load"


def test_a_known_finding_and_a_new_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(_space_door_lists, "KNOWN_OPEN", {OPEN: ("the whole table at a start", "step 3")})
    path = _report(tmp_path, _entry("none", [OPEN, "kazma-ui/kazma_ui/app.py::KazmaApp._on_startup"]),
                   _entry("none", [UNKNOWN]),
                   _entry("install", ["kazma-ui/kazma_ui/health.py::check_database"], "statements", "a ping"),
                   _entry("space", ["kazma-ui/kazma_ui/session_manager.py::SessionManager._upsert_db"], "scope"))
    script = _script()
    assert script.main(["--file", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "known, step 3" in printed and "NEW" in printed and "a ping" in printed
    assert script.main(["--file", str(path), "--strict"]) == 1, "the control: a new finding fails --strict"
    summary = script.summarise(json.loads(path.read_text(encoding="utf-8")))
    assert [f["callers"][0] for f in summary["new"]] == [UNKNOWN]
    assert "kazma-ui/kazma_ui/health.py::check_database" not in summary["not_seen"]
    assert "kazma-core/kazma_core/swarm/task_store.py::TaskStore.get_task" in summary["not_seen"]


def test_only_known_findings_pass_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_space_door_lists, "KNOWN_OPEN", {OPEN: ("the whole table at a start", "step 3")})
    path = _report(tmp_path, _entry("none", [OPEN]))
    assert _script().main(["--file", str(path), "--strict", "--json"]) == 0


def test_a_caller_the_lists_lack_is_named(tmp_path: Path) -> None:
    path = _report(tmp_path, _entry("space", ["kazma-core/kazma_core/planted.py::Store.read"], "scope"))
    summary = _script().summarise(json.loads(path.read_text(encoding="utf-8")))
    assert summary["not_listed"] == ["kazma-core/kazma_core/planted.py::Store.read"]


def test_no_report_and_an_unreadable_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _script()
    assert script.main(["--file", str(tmp_path / "none.json")]) == 0
    assert "no door report" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert script.main(["--file", str(bad)]) == 1
