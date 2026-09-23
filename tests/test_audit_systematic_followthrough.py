"""Follow-through checks for the 2026-09-22 systematic audit plan."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "kazma-core/kazma_core/",
    "kazma-gateway/kazma_gateway/",
    "kazma-ui/kazma_ui/",
    "kazma-cli/kazma_cli/",
    "kazma-tui/kazma_tui/",
    "kazma-skills/kazma_skills/",
)


def test_browser_egress_blocks_private_and_odd_schemes() -> None:
    from kazma_core.security.browser_egress import browser_request_allowed

    assert browser_request_allowed("http://127.0.0.1/secret")[0] is False
    assert browser_request_allowed("http://[::1]/secret")[0] is False
    assert browser_request_allowed("file:///etc/passwd")[0] is False
    assert browser_request_allowed("http://8.8.8.8/")[0] is True
    assert browser_request_allowed("data:text/plain,hi")[0] is True


def test_sync_browser_route_aborts_before_continue() -> None:
    from kazma_core.security.browser_egress import install_sync_browser_egress

    class _Route:
        def __init__(self, url: str) -> None:
            self.request = type("R", (), {"url": url})()
            self.action = ""

        def abort(self, _error: str) -> None:
            self.action = "abort"

        def continue_(self) -> None:
            self.action = "continue"

    class _Context:
        def route(self, _pattern, handler) -> None:
            self.handler = handler

    ctx = _Context()
    install_sync_browser_egress(ctx)
    blocked = _Route("http://127.0.0.1/")
    ctx.handler(blocked)
    assert blocked.action == "abort"
    allowed = _Route("http://8.8.8.8/")
    ctx.handler(allowed)
    assert allowed.action == "continue"


def test_cost_breaker_setting_precedence(monkeypatch) -> None:
    from kazma_core.cost_breaker import CostCircuitBreaker

    class _Store:
        def __init__(self, value) -> None:
            self.value = value

        def get(self, key, default=None):
            if key == "cost.breaker_enabled":
                return self.value
            return default

    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store",
        lambda: _Store(False),
    )
    breaker = CostCircuitBreaker(max_cost=0.01, hard_max_cost=0.01)
    breaker.record_cost(1)
    assert breaker.should_halt() is False

    monkeypatch.setenv("KAZMA_DISABLE_COST_BREAKER", "1")
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store",
        lambda: _Store(True),
    )
    assert breaker.should_halt() is False


def test_pg_offsite_failure_is_queued_not_reported_as_dump_success(monkeypatch, tmp_path) -> None:
    from kazma_core.memory import worker_bootstrap as wb

    dump = tmp_path / "pg.dump"
    dump.write_bytes(b"PGDMP")
    queued: list[tuple[str, dict]] = []
    monkeypatch.setattr("kazma_core.db.pg_backup.pg_backup_enabled", lambda: True)
    monkeypatch.setattr("kazma_core.db.pg_backup.perform_pg_backup", lambda: dump)
    monkeypatch.setattr(wb, "_snapshot_pg_to_restic", lambda _path: ["remote"])
    monkeypatch.setattr(
        "kazma_core.memory.task_queue.enqueue_task",
        lambda kind, payload, **_k: queued.append((kind, payload)) or "t1",
    )

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    async def _run():
        return await wb._handle_native_pg_backup({})

    assert asyncio.run(_run()) is True
    assert queued and queued[0][0] == "native_pg_offsite"
    assert queued[0][1]["path"] == str(dump)


def test_pg_offsite_handler_stays_failed_until_the_copy_works(monkeypatch, tmp_path) -> None:
    from kazma_core.memory import worker_bootstrap as wb

    dump = tmp_path / "pg.dump"
    dump.write_bytes(b"PGDMP")
    monkeypatch.setattr(wb, "_snapshot_pg_to_restic", lambda _path: ["remote"])

    async def _to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    assert asyncio.run(wb._handle_native_pg_offsite({"path": str(dump)})) is False
    monkeypatch.setattr(wb, "_snapshot_pg_to_restic", lambda _path: [])
    assert asyncio.run(wb._handle_native_pg_offsite({"path": str(dump)})) is True


def test_document_and_tui_extras_are_base_aliases() -> None:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = {item.split(">=")[0].split("[")[0].strip() for item in data["project"]["dependencies"]}
    extras = data["project"]["optional-dependencies"]
    for name in ("tui", "document"):
        for item in extras[name]:
            pkg = item.split(">=")[0].split("[")[0].strip()
            assert pkg in base, f"{name} extra package {pkg} is not already a base dependency"


def test_shipped_install_job_uses_the_lockfile_and_the_wheel() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = text.split("shipped-install:", 1)[1].split("\n  tests:", 1)[0]
    assert "uv export --frozen" in job
    assert "uv build --wheel" in job
    assert "dist/*.whl" in job
    assert "import kazma_core, kazma_gateway, kazma_ui, kazma_cli, kazma_tui, kazma_skills" in job


def test_lint_gate_covers_every_product_package() -> None:
    import re

    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    # The gating step is found by its GATE marker, not its full name: the
    # name gains a clause whenever a rule joins the gate (2026-09-23).
    match = re.search(r"name: Run Ruff \([^)\n]*GATE\)", text)
    assert match, "no gating Ruff step in ci.yml"
    gate = text[match.end():].split("Run Ruff (full", 1)[0]
    for package in PACKAGES:
        assert package in gate
    for rule in ("E9", "F82", "F841", "B033"):
        assert rule in gate, f"{rule} left the gating Ruff selection"


def test_isolated_rehearsal_refuses_the_live_database() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "restore_rehearsal", ROOT / "scripts" / "restore_rehearsal.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rehearsal_targets_live_database = module.rehearsal_targets_live_database

    live = "postgresql://kazma:secret@127.0.0.1:5432/kazma"
    assert rehearsal_targets_live_database(live, live) is True
    other = "postgresql://kazma:secret@127.0.0.1:5433/kazma_rehearsal"
    assert rehearsal_targets_live_database(live, other) is False
    assert rehearsal_targets_live_database(live, "") is True


def test_memory_off_copy_does_not_promise_a_legacy_reader() -> None:
    text = (ROOT / "kazma-ui/kazma_ui/static/js/memory_console.js").read_text(encoding="utf-8")
    assert "legacy RRF" not in text
    assert "There is no legacy reader" in text


@pytest.mark.asyncio
async def test_file_stamp_yields_the_loop(tmp_path, monkeypatch) -> None:
    import importlib

    fr = importlib.import_module("kazma_core.tools.file_read")

    target = tmp_path / "slow.txt"
    target.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_core.workspace.path_policy.check_path_access",
        lambda *_a, **_k: type("A", (), {"allowed": True})(),
    )

    def _slow_stamp(path):
        time.sleep(0.2)
        return (1, 1, b"abc")

    monkeypatch.setattr(fr, "_stat_stamp", _slow_stamp)
    ticks: list[float] = []

    async def _tick() -> None:
        await asyncio.sleep(0.05)
        ticks.append(time.monotonic())

    task = asyncio.create_task(_tick())
    await fr.file_read(str(target))
    finished = time.monotonic()
    await task
    assert ticks and ticks[0] < finished
