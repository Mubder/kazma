"""Research storage is sized with research admission (FA-266).

The 2026-11 functional audit, five research runs started at once: four were
admitted (the pipeline capacity) and one answered 503 with no row -- correct.
But of the four, one ended at once in ``error``: "Retained research storage
allowance exhausted (2GiB); remove settled reports before starting more work".
Four reservations of 512 MiB are exactly 2 GiB and the check is strict, so
with one byte of settled reports the fourth admitted run was always refused --
after its session row existed, with an instruction that would free 14.9 MB of
the 512 MiB it needed.

Now the allowance holds every admitted run's reservation plus a declared
share for settled reports, and storage is part of the admission that answers
503: a run refused for storage has no session row. The old 2 GiB is the
negative control.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from kazma_core import config_store
from kazma_core.tools import research_session as sessions
from kazma_core.web_acquire import artifact_storage, resources
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    monkeypatch.setattr(sessions, "_db_path", lambda: tmp_path / "sessions.db")
    yield settings
    settings.close()


def _settled_report(root, name="old", body="Saved owner report") -> None:
    folder = root / "research" / "reports" / name
    folder.mkdir(parents=True)
    (folder / "report.md").write_text(body, encoding="utf-8")


async def _four_running_reservations(root, monkeypatch) -> list[str]:
    """Four runs reserve while one settled report is kept; each stays running
    (its reservation is live). Returns how each ended."""
    monkeypatch.setattr(artifact_storage, "_live", lambda row, jobs: bool(row.get("active")))
    outcomes = []
    for n in range(4):
        job = SimpleNamespace(token=f"claim-{n}", job_id=f"job-{n}")
        try:
            await artifact_storage.reserve(job, root, root / "research" / "reports" / f"run-{n}")
            outcomes.append("reserved")
        except AcquisitionError as exc:
            outcomes.append(str(exc))
    return outcomes


def test_the_allowance_holds_every_admitted_run_beside_the_settled_share():
    """The source rule: the capacity research admits, times the reservation,
    fits beside a declared settled share."""
    assert resources.research_capacity().capacity == artifact_storage._ACTIVE_RUNS
    assert resources.research_session_capacity().capacity <= artifact_storage._ACTIVE_RUNS
    assert artifact_storage._SETTLED_SHARE > 0
    assert (artifact_storage._ACTIVE_RUNS * artifact_storage._RESERVATION
            + artifact_storage._SETTLED_SHARE) <= artifact_storage._LIMIT


async def test_four_admitted_runs_all_reserve_beside_a_settled_report(store, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    _settled_report(root)
    assert await _four_running_reservations(root, monkeypatch) == ["reserved"] * 4


async def test_the_old_allowance_refused_the_fourth_run(store, tmp_path, monkeypatch):
    """Negative control: the 2 GiB allowance before FA-266."""
    monkeypatch.setattr(artifact_storage, "_LIMIT", 2 * 1024**3)
    root = tmp_path / "workspace"
    _settled_report(root)
    outcomes = await _four_running_reservations(root, monkeypatch)
    assert outcomes[:3] == ["reserved"] * 3 and "allowance exhausted" in outcomes[3]


async def test_settled_reports_past_their_share_refuse_at_admission_with_no_row(store, tmp_path, monkeypatch):
    """Through the real route: settled reports over their share answer 503
    before a session row exists, and the refusal says the settled reports
    are the cause."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.research_panel.routes import create_research_router

    root = tmp_path / "workspace"
    _settled_report(root, body="x" * 64)
    monkeypatch.setattr(artifact_storage, "_LIMIT",
                        artifact_storage._ACTIVE_RUNS * artifact_storage._RESERVATION + 16)
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: root)
    app = FastAPI()
    app.include_router(create_research_router())
    with TestClient(app) as client:
        answer = client.post("/api/research/sessions", json={"topic": "storage admission", "depth": "brief"})
        assert answer.status_code == 503, answer.text
        body = answer.json()
        assert body["error_code"] == str(FetchErrorCode.LIMIT)
        assert "settled reports hold" in body["error"] and "share" in body["error"]
    assert sessions.list_sessions(archived=None) == [], "no session row for a refused run"
    assert resources.research_session_capacity().snapshot()["active"] == 0


async def test_an_abandoned_reservation_is_measured_again_at_admission(store, tmp_path, monkeypatch):
    """A run that ended without settling left its 512 MiB reservation; the
    next admission measures its folder instead of refusing on it."""
    root = tmp_path / "workspace"
    _settled_report(root, name="abandoned", body="partial report")
    from kazma_core.web_acquire.durable_state import mutate
    from kazma_core.web_acquire.crawl_jobs import call

    folder = root / "research" / "reports" / "abandoned"

    def plant(table):
        table[artifact_storage._identity(folder)] = dict(
            path=str(folder), root=str(root), bytes=artifact_storage._RESERVATION, active=True,
            claim="gone", job_id="gone", owner=[1, 1.0])

    await call(mutate, "artifacts", plant, entries=256)
    monkeypatch.setattr(artifact_storage, "_LIMIT",
                        artifact_storage._ACTIVE_RUNS * artifact_storage._RESERVATION + 1024)
    await artifact_storage.admit(root)  # the reservation is now its folder's size
    snap = await asyncio.to_thread(artifact_storage.snapshot)
    assert snap["running_jobs"] == 0 and snap["running_reserved_bytes"] == 0
    assert 0 < snap["settled_bytes"] < 1024
