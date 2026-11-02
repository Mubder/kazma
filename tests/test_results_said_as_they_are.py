"""A result is said as it is (2026-11 functional audit, Stage 3).

- FA-108: ``persist_reply`` logged "Reply persisted" whatever the transcript
  store answered.
- FA-109: orphan recovery counted a task "terminally failed" when the store
  refused to write it.
- FA-110: an agent that could not take a new provider key said so at DEBUG,
  and kept answering with the old key's client.
- FA-202: a document worker logged an ERROR on every poll while its store was
  away (one a second per worker during a pool outage).
- FA-236: a swarm dispatch that dispatched nothing answered 200 "warning", and
  the CLI turned any error answer into a traceback.

Each case runs the real code; each has the old behaviour as its control.
"""

from __future__ import annotations

import asyncio
import logging

import pytest


# ── FA-108 ────────────────────────────────────────────────────────────────


def _persisted_lines(records) -> list[str]:
    return [r.getMessage() for r in records if "Reply persisted" in r.getMessage()]


@pytest.mark.parametrize("answer", [True, False])
def test_persist_reply_says_what_the_store_answered(monkeypatch, caplog, answer):
    from kazma_ui import reply_sink, turn_runtime

    monkeypatch.setattr(reply_sink, "upsert_reply", lambda *a, **k: answer)
    monkeypatch.setattr(reply_sink, "close_reply_turn", lambda *a, **k: None)
    with caplog.at_level(logging.DEBUG, logger="kazma_ui.turn_runtime"):
        ok = turn_runtime.persist_reply(
            thread_id="thread-1", session_id="session-1", reply_turn_id="turn-1",
            content="the answer", interrupted=False,
        )
    assert ok is answer
    persisted = _persisted_lines(caplog.records)
    refused = [r for r in caplog.records if r.levelno == logging.WARNING and "NOT persisted" in r.getMessage()]
    if answer:
        assert persisted and not refused
    else:
        assert not persisted, persisted
        assert refused, [r.getMessage() for r in caplog.records]


def test_nothing_to_persist_is_not_a_warning(monkeypatch, caplog):
    from kazma_ui import reply_sink, turn_runtime

    monkeypatch.setattr(reply_sink, "upsert_reply", lambda *a, **k: False)
    monkeypatch.setattr(reply_sink, "close_reply_turn", lambda *a, **k: None)
    with caplog.at_level(logging.DEBUG, logger="kazma_ui.turn_runtime"):
        turn_runtime.persist_reply(
            thread_id="thread-1", session_id="session-1", reply_turn_id="turn-1",
            content="", interrupted=False,
        )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not _persisted_lines(caplog.records)


def test_negative_control_the_old_line_said_persisted_over_a_refusal(caplog):
    """The old code logged the INFO line after any answer."""
    log = logging.getLogger("kazma_ui.turn_runtime")
    with caplog.at_level(logging.INFO, logger="kazma_ui.turn_runtime"):
        ok = False
        log.info("[turn] Reply persisted thread=%s turn=%s chars=%d interrupted=%s",
                 "thread-1", "turn-1", 10, False)
    assert not ok and _persisted_lines(caplog.records)


# ── FA-109 ────────────────────────────────────────────────────────────────


def _running_task(store):
    from kazma_core.swarm.task import SwarmTask, TaskStatus

    task = SwarmTask(id="orphan-1", prompt="left running", workers=["auto"])
    task.status = TaskStatus.RUNNING
    assert store.admit(task)
    return task


def test_a_refused_recovery_write_is_not_counted_recovered(tmp_path, monkeypatch):
    from kazma_core import metrics
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "tasks.db"))
    _running_task(store)
    sites: list[str] = []
    monkeypatch.setattr(metrics, "record_swarm_failure", sites.append)
    monkeypatch.setattr(store, "persist_task", lambda task: False)
    outcome = store.requeue_orphaned_running()
    assert outcome == {"requeued": [], "failed": []}
    assert sites == ["task_write"]


def test_a_written_recovery_is_counted(tmp_path):
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "tasks.db"))
    _running_task(store)
    assert store.requeue_orphaned_running()["failed"] == ["orphan-1"]
    assert store.get_task("orphan-1").status.value == "failed"


# ── FA-110 ────────────────────────────────────────────────────────────────


@pytest.fixture
def fallback_record(monkeypatch):
    from kazma_core.observability import model_fallback, ops_alerts
    from kazma_core.observability.alerts import AlertDispatcher

    model_fallback.reset_fallback_state()
    seen: dict[str, list] = {"alerts": [], "banners": [], "resolved": []}
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: seen["alerts"].append(key) or True)
    monkeypatch.setattr(AlertDispatcher, "post_banner",
                        classmethod(lambda cls, **k: seen["banners"].append(k.get("subsystem"))))
    monkeypatch.setattr(AlertDispatcher, "resolve_alerts_for_subsystem",
                        classmethod(lambda cls, sub: seen["resolved"].append(sub)))
    yield seen
    model_fallback.reset_fallback_state()


def test_a_key_the_agent_cannot_take_is_said(monkeypatch, caplog, fallback_record):
    from kazma_core.runtime import model_switch

    def _broken(agent):
        raise RuntimeError("client build failed")

    monkeypatch.setattr(model_switch, "_sync_agent", _broken)
    with caplog.at_level(logging.WARNING, logger="kazma_core.runtime.model_switch"):
        model_switch.notify_credentials_changed("deepseek")
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("deepseek" in m for m in warned), warned
    assert "llm.fallback.rebind:deepseek" in fallback_record["alerts"]
    assert "llm-rebind:deepseek" in fallback_record["banners"]

    # The next save that takes ends it.
    monkeypatch.setattr(model_switch, "_sync_agent", lambda agent: None)
    model_switch.notify_credentials_changed("deepseek")
    assert "llm-rebind:deepseek" in fallback_record["resolved"]


def test_a_key_taken_raises_nothing(monkeypatch, fallback_record):
    from kazma_core.runtime import model_switch

    monkeypatch.setattr(model_switch, "_sync_agent", lambda agent: None)
    model_switch.notify_credentials_changed("deepseek")
    assert fallback_record["alerts"] == [] and fallback_record["banners"] == []


# ── FA-202 ────────────────────────────────────────────────────────────────


class _StoreAway:
    """A job repository whose claims fail *down* times, then find nothing."""

    def __init__(self, worker_holder: list, down: int, polls: int) -> None:
        self.calls = 0
        self.down, self.polls, self.holder = down, polls, worker_holder

    def claim_next(self, **_kw):
        self.calls += 1
        if self.calls >= self.polls:
            self.holder[0]._stop_event.set()
        if self.calls <= self.down:
            raise ConnectionError("pool away")
        return None


def _claim_lines(records) -> tuple[list, list, list]:
    errors = [r for r in records if r.levelno >= logging.ERROR]
    warnings = [r for r in records if r.levelno == logging.WARNING and "claims failing" in r.getMessage()]
    back = [r for r in records if "claims work again" in r.getMessage()]
    return errors, warnings, back


def test_an_outage_is_one_warning_and_one_recovery(caplog):
    from kazma_core.documents.worker import DocumentWorker

    holder: list = [None]
    repo = _StoreAway(holder, down=20, polls=25)
    worker = DocumentWorker(repo, {}, concurrency=1, poll_interval=0.001)
    holder[0] = worker
    with caplog.at_level(logging.DEBUG, logger="kazma_core.documents.worker"):
        asyncio.run(asyncio.wait_for(worker._worker_loop(0), timeout=30))
    errors, warnings, back = _claim_lines(caplog.records)
    assert repo.calls >= 21
    assert errors == [], [r.getMessage() for r in errors]
    assert len(warnings) == 1 and len(back) == 1


def test_negative_control_an_error_per_poll_is_caught(caplog):
    """The old loop: one ERROR for every failed claim."""
    log = logging.getLogger("kazma_core.documents.worker")
    with caplog.at_level(logging.DEBUG, logger="kazma_core.documents.worker"):
        for _ in range(20):
            log.error("Document claim failed worker=%s error_type=%s", "w:0", "ConnectionError")
    errors, _warnings, _back = _claim_lines(caplog.records)
    assert len(errors) == 20


# ── FA-236 ────────────────────────────────────────────────────────────────


def test_a_dispatch_to_no_registered_worker_is_refused():
    from tests.test_swarm_api import _build_client

    client = _build_client()
    response = client.post("/api/swarm/dispatch", json={"workers": ["ghost"], "task": "anything"})
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["code"] == "workers_not_registered" and body["missing"] == ["ghost"]
    assert body["dispatched"] == []
    tasks = client.get("/api/swarm/tasks").json()
    listed = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
    assert not [t for t in listed if isinstance(t, dict) and t.get("task") == "anything"]


def test_the_cli_says_the_servers_refusal(monkeypatch, capsys):
    from kazma_cli import gateway as cli_gateway
    from kazma_cli import swarm as cli_swarm

    async def _refused(client, method, path, **kw):
        raise cli_gateway._GatewayRequestError(404, "None of the specified workers are registered: ghost.")

    monkeypatch.setattr(cli_swarm, "_request", _refused)
    monkeypatch.setattr(cli_swarm, "resolve_base_url", lambda *a, **k: "http://127.0.0.1:9")
    with pytest.raises(SystemExit) as exit_info:
        cli_swarm.run(["dispatch", "ghost", "anything"])
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "404" in out and "ghost" in out
