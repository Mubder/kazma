"""A graph store set to Neo4j with no driver says so once (2026-10-28).

The ``neo4j`` driver was in no extra, so a fresh install set to Neo4j never
had it. Every dual-write then failed inside ``_get_driver``'s
``except Exception`` at DEBUG, and the graph stayed in SQLite with no line
above DEBUG naming the cause. Now the driver is the ``neo4j`` extra, a
missing one is ONE warning per process naming the install command (at boot
and on first use), the Settings capability says ``driver_missing``, and the
generic "neo4j unavailable" line is not repeated on top of it.
"""

from __future__ import annotations

import logging
import sys
import tomllib
from pathlib import Path

import pytest
from kazma_core.memory import graph_backend
from kazma_core.memory.graph_backend import Neo4jGraphBackend

_ROOT = Path(__file__).resolve().parents[1]
_LOGGER = "kazma_core.memory.graph_backend"


@pytest.fixture()
def no_driver(monkeypatch):
    """The neo4j package absent: its import raises, find_spec finds nothing."""
    monkeypatch.setitem(sys.modules, "neo4j", None)
    monkeypatch.setattr(graph_backend, "_neo4j_driver_installed", lambda: False)
    monkeypatch.setattr(graph_backend, "_driver_missing_warned", False)
    graph_backend.reset_graph_backend_cache()
    yield
    graph_backend.reset_graph_backend_cache()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and r.levelno >= logging.WARNING]


def _neo4j_cfg(monkeypatch, provider: str = "neo4j") -> None:
    from kazma_core.memory import backends

    cfg = {"graph": {"provider": provider, "url": "bolt://127.0.0.1:1", "user": "neo4j", "password": ""}}
    monkeypatch.setattr(backends, "get_backends_cfg", lambda: cfg)


def test_a_missing_driver_is_one_warning_naming_the_extra(no_driver, monkeypatch, caplog):
    _neo4j_cfg(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    for _ in range(3):
        assert not Neo4jGraphBackend(url="bolt://127.0.0.1:1").available
        graph_backend.reset_graph_backend_cache()
        assert isinstance(graph_backend.get_graph_backend(), graph_backend.SqliteGraphBackend)
        graph_backend.upsert_belief_edge(subject="user", predicate="p", obj="x")
    warned = _warnings(caplog)
    assert len(warned) == 1, warned
    assert '.[neo4j]' in warned[0] and "not installed" in warned[0]


def test_negative_control_the_old_driver_lookup_said_nothing_above_debug(no_driver, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    log = logging.getLogger(_LOGGER)

    def old_get_driver(url: str):
        try:
            from neo4j import GraphDatabase  # noqa: F401

            return GraphDatabase
        except Exception:
            log.debug("[graph_backend] neo4j driver unavailable", exc_info=True)
            return None

    for _ in range(3):
        assert old_get_driver("bolt://127.0.0.1:1") is None
    assert _warnings(caplog) == []


def test_boot_check_warns_only_when_neo4j_is_chosen(no_driver, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    _neo4j_cfg(monkeypatch, provider="sqlite")
    assert graph_backend.report_graph_driver() is True
    assert _warnings(caplog) == []
    _neo4j_cfg(monkeypatch)
    assert graph_backend.report_graph_driver() is False
    assert graph_backend.report_graph_driver() is False
    assert len(_warnings(caplog)) == 1


def test_boot_check_is_quiet_with_the_driver(monkeypatch, caplog):
    monkeypatch.setattr(graph_backend, "_neo4j_driver_installed", lambda: True)
    monkeypatch.setattr(graph_backend, "_driver_missing_warned", False)
    _neo4j_cfg(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    assert graph_backend.report_graph_driver() is True
    assert _warnings(caplog) == []


def test_settings_capability_says_the_driver_is_missing(no_driver):
    cap = graph_backend.graph_capability({"graph": {"provider": "neo4j", "url": "bolt://x:7687"}})
    assert cap["status"] == "driver_missing" and cap["write_ready"] is False
    assert ".[neo4j]" in cap["detail"]


def test_settings_test_button_names_the_extra(no_driver):
    out = graph_backend.test_neo4j_connection({"graph": {"url": "bolt://x:7687"}})
    assert out["ok"] is False and ".[neo4j]" in out["error"]


def test_the_driver_is_the_neo4j_extra_and_part_of_all():
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert any(req.startswith("neo4j>=") for req in extras["neo4j"])
    assert "neo4j" in extras["all"][0].split("[", 1)[1]
    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert '[[package]]\nname = "neo4j"' in lock


def test_the_updater_knows_the_extra():
    from kazma_cli import update

    assert "neo4j" in update._KNOWN_EXTRAS and update._EXTRA_MARKERS["neo4j"] == ("neo4j",)


def test_boot_runs_the_check_off_the_loop():
    src = (_ROOT / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")
    assert "_aio.to_thread(report_graph_driver)" in src
