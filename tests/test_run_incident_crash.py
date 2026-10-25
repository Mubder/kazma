"""A process killed at any stage of an incident leaves one honest outcome.

Phase 5 of ``docs/plans/RUN_SUPERVISION_PLAN.md``. A real child process runs
the real supervisor, incident store and delivery code against a fake kind and
is killed (``os._exit``) at a stage; a second child is the restart. Ground
truth is a log the fake platform and the fake kind append to.

* killed after the incident committed, before sending: the restart sends it,
  once;
* killed after it was marked sending (before or after the platform took it):
  never sent again, marked uncertain;
* killed while an owner's action ran: closed as interrupted, never replayed.

Negative control: sending BEFORE committing the incident, killed in between,
sends more than once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CHILD = textwrap.dedent(r'''
    import asyncio, json, os, sys
    from kazma_core.runs import registry, supervisor, notify
    from kazma_core.runs.contract import ActionResult, KindSpec, Run, RunState
    from kazma_core.runs.incidents import IncidentStore, get_incident_store, reset_incident_store
    from kazma_core.runs.kinds import KINDS

    LOG, DB, STAGE, MODE = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    T0 = 1_800_000_000.0

    def log(event, **kw):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, **kw}) + "\n")

    class Fake:
        STATE_MAP = {"failed": RunState.FAILED}
        async def list_runs(self, now, *, window_s):
            return [Run(run_id="fake:r1", kind="fake", native_id="r1", title="job", state=RunState.FAILED,
                        native_state="failed", ended_at=T0, actions=("retry",), version="1",
                        origin={"platform": "telegram", "target": "telegram:42", "thread_id": "g"})]
        async def act(self, native_id, action, *, actor):
            log("act", action=action)
            if STAGE == "during_act":
                os._exit(9)
            return ActionResult(True, "retried", "ok")

    for kind in list(KINDS):
        del KINDS[kind]
    KINDS["fake"] = KindSpec(kind="fake", adapter="x:y", label="Fake job", label_ar="x", source="t")
    registry._ADAPTERS["fake"] = Fake()
    store = IncidentStore(DB)
    reset_incident_store(store)
    supervisor._watch_started = T0 - 60

    async def send(target, text, *, backend="telegram", **kw):
        if STAGE == "after_claim_before_platform":
            os._exit(9)
        log("sent", text=text.splitlines()[0])
        if STAGE == "after_platform_before_finish":
            os._exit(9)
        return "sent:" + target

    import kazma_core.tools.send_message as sm
    sm.send_message = send

    if STAGE == "after_commit_before_send":
        async def stop(row):
            os._exit(9)
        notify.deliver = stop

    if MODE == "send_first":
        # The negative control: send before the incident is committed.
        original_open = store.open_incident
        def open_after_sending(run, condition, **kw):
            log("sent", text="(sent before commit)")
            if STAGE == "between_send_and_commit":
                os._exit(9)
            return original_open(run, condition, **kw)
        store.open_incident = open_after_sending
        async def no_send(row):
            store.claim_notification(row["id"])
            store.finish_notification(row["id"], outcome="delivered")
            return "delivered"
        notify.deliver = no_send

    async def main():
        if sys.argv[5] == "restart":
            store.recover()
        await supervisor.tick(now=T0 + 5)
        if sys.argv[5] == "act":
            from kazma_core.runs.decide import decide
            row = store.live()[0]
            await decide(row["id"], "retry", actor="owner", platform="test")
        await supervisor.tick(now=T0 + 35)
        log("state", rows=[{k: r[k] for k in ("state", "notify", "outcome_code")} for r in store.recent()])

    asyncio.run(main())
''')


def _run(tmp_path: Path, stage: str, phase: str, mode: str = "normal") -> int:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    env = {**os.environ, "KAZMA_DATA_DIR": str(tmp_path / "data"), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, str(script), str(tmp_path / "log.jsonl"), str(tmp_path / "inc.db"),
                           stage, mode, phase], env=env, capture_output=True, text=True, timeout=120)
    if proc.returncode not in (0, 9):
        raise AssertionError(proc.stderr[-3000:])
    return proc.returncode


def _events(tmp_path: Path) -> list[dict]:
    path = tmp_path / "log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _final_state(tmp_path: Path) -> dict:
    return [e for e in _events(tmp_path) if e["event"] == "state"][-1]["rows"][0]


def test_killed_after_commit_before_sending_is_sent_once_at_restart(tmp_path):
    assert _run(tmp_path, "after_commit_before_send", "first") == 9
    assert [e for e in _events(tmp_path) if e["event"] == "sent"] == []
    assert _run(tmp_path, "none", "restart") == 0
    assert len([e for e in _events(tmp_path) if e["event"] == "sent"]) == 1
    assert _final_state(tmp_path)["notify"] == "delivered"


@pytest.mark.parametrize("stage", ["after_claim_before_platform", "after_platform_before_finish"])
def test_killed_while_sending_is_never_sent_again(tmp_path, stage):
    assert _run(tmp_path, stage, "first") == 9
    sent_before = len([e for e in _events(tmp_path) if e["event"] == "sent"])
    assert _run(tmp_path, "none", "restart") == 0
    assert len([e for e in _events(tmp_path) if e["event"] == "sent"]) == sent_before, "never resent"
    assert _final_state(tmp_path)["notify"] == "uncertain"


def test_killed_while_an_action_ran_is_closed_and_never_replayed(tmp_path):
    assert _run(tmp_path, "during_act", "act") == 9
    assert len([e for e in _events(tmp_path) if e["event"] == "act"]) == 1
    assert _run(tmp_path, "none", "restart") == 0
    assert len([e for e in _events(tmp_path) if e["event"] == "act"]) == 1, "never replayed"
    final = _final_state(tmp_path)
    assert final["state"] == "closed" and final["outcome_code"] == "interrupted"


def test_sending_before_committing_sends_more_than_once(tmp_path):
    """Negative control: the order the store enforces is what prevents this."""
    assert _run(tmp_path, "between_send_and_commit", "first", mode="send_first") == 9
    assert _run(tmp_path, "none", "restart", mode="send_first") == 0
    # More than once: the killed send is repeated, and so is every later pass's.
    assert len([e for e in _events(tmp_path) if e["event"] == "sent"]) >= 2
