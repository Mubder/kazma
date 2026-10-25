"""Soak the run supervisor: many runs, a fake clock, restarts, failures, presses.

Phase 5 of ``docs/plans/RUN_SUPERVISION_PLAN.md``. The REAL supervisor, incident
store (a private SQLite file), delivery code and decision path run against a
simulated world of fake kinds whose runs progress, stall, lose their owner,
recover, fail and finish on a fake clock. Each cycle is one 30-second tick;
between ticks the harness presses buttons the way an owner would (retry,
cancel, dismiss), makes the chat platform refuse sends, and restarts (a new
store object, ``recover()``, the supervisor's module state reset).

Ground truth is the world's own record of when each run went bad. After the
run every incident and every send is checked:

* ``missed``         a bad episode lasting two ticks with no incident;
* ``false_page``     an incident opened while its run was fine;
* ``double_send``    one incident's card sent more than once;
* ``double_action``  one incident's action executed more than once;
* ``reopened``       an incident opened again after the owner decided about
                     the same run, condition and version.

Usage::

    python scripts/run_supervision_soak.py --cycles 2000 --runs 60 --seed 7 --out report.json
    python scripts/run_supervision_soak.py --plant double_send   # a planted bug: must be caught

Private stores only: an install's ``.env`` would point the writes at its data.
Exit status 1 when any violation is found.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

T0 = 1_800_000_000.0
TICK = 30.0
STALL = 300.0
DEAD = 120.0
PLANTS = ("double_send", "missed", "double_action")


@dataclass
class SimRun:
    ident: str
    kind: str
    state: str = "running"  # running | failed | done
    progress_at: float = T0
    owner: bool = True
    version: int = 1
    effects: str = "none"
    # (condition, start, end or None)
    episodes: list[list[Any]] = field(default_factory=list)


class World:
    """The simulated runs, and the fake adapter every fake kind reads."""

    def __init__(self, rng: random.Random, n_runs: int) -> None:
        self.rng = rng
        self.now = T0
        self.runs: dict[str, SimRun] = {}
        self.acted: Counter[str] = Counter()
        self.next_id = 0
        for _ in range(n_runs):
            self._spawn()

    def _spawn(self) -> None:
        self.next_id += 1
        kind = self.rng.choice(("soak_a", "soak_b"))
        run = SimRun(ident=f"r{self.next_id}", kind=kind, progress_at=self.now,
                     effects="unknown" if self.rng.random() < 0.1 else "none")
        self.runs[run.ident] = run

    def condition(self, run: SimRun) -> str | None:
        if run.state == "failed":
            return "failed"
        if run.state != "running":
            return None
        idle = self.now - run.progress_at
        if not run.owner and idle >= DEAD:
            return "dead"
        if run.owner and idle >= STALL:
            return "stalled"
        return None

    def step(self) -> None:
        """Advance every run by one tick of random behaviour."""
        self.now += TICK
        for run in list(self.runs.values()):
            if run.state != "running":
                continue
            r = self.rng.random()
            if r < 0.55:
                run.progress_at = self.now
            elif r < 0.57:
                run.state = "failed"
                run.version += 1
            elif r < 0.60:
                run.state = "done"
            elif r < 0.61:
                run.owner = False
        if self.rng.random() < 0.3:
            self._spawn()
        for run in self.runs.values():
            current = self.condition(run)
            open_ep = run.episodes[-1] if run.episodes and run.episodes[-1][2] is None else None
            if open_ep and open_ep[0] != current:
                open_ep[2] = self.now
                open_ep = None
            if current and not open_ep:
                run.episodes.append([current, self.now, None])

    def to_run(self, run: SimRun) -> Any:
        from kazma_core.runs.contract import Run, RunState

        state = {"running": RunState.RUNNING, "failed": RunState.FAILED, "done": RunState.DONE}[run.state]
        actions: tuple[str, ...] = ()
        if run.state == "running":
            actions = ("cancel",)
        elif run.state == "failed":
            actions = ("retry_checked",) if run.effects == "unknown" else ("retry",)
        return Run(run_id=f"{run.kind}:{run.ident}", kind=run.kind, native_id=run.ident, title=run.ident,
                   state=state, native_state=run.state, started_at=T0, progress_at=run.progress_at,
                   owner_alive=run.owner if run.state == "running" else None, version=str(run.version),
                   ended_at=self.now if run.state != "running" else None, actions=actions, effects=run.effects,
                   origin={"platform": "telegram", "target": "telegram:1", "thread_id": "g"})


class SimAdapter:
    STATE_MAP = {"running": "running", "failed": "failed", "done": "done"}

    def __init__(self, world: World, kind: str, plant: str) -> None:
        self.world, self.kind, self.plant = world, kind, plant

    async def list_runs(self, now: float, *, window_s: float) -> list[Any]:
        runs = [self.world.to_run(r) for r in self.world.runs.values() if r.kind == self.kind]
        if self.plant == "missed":
            runs = [r for r in runs if not r.native_id.endswith("7")]
        return runs

    async def act(self, native_id: str, action: str, *, actor: str) -> Any:
        from kazma_core.runs.contract import ActionResult

        run = self.world.runs[native_id]
        self.world.acted[f"{self.kind}:{native_id}:{action}:{run.version}"] += 1
        if action == "cancel":
            run.state = "done"
        elif action in ("retry", "retry_checked"):
            run.state, run.progress_at, run.owner = "running", self.world.now, True
            run.version += 1
        return ActionResult(True, "done", action)


def soak(*, cycles: int, runs: int, seed: int, plant: str = "", workdir: Path) -> dict[str, Any]:
    import kazma_core.tools.send_message as send_module
    from kazma_core.runs import registry, supervisor
    from kazma_core.runs.contract import KindSpec
    from kazma_core.runs.decide import decide
    from kazma_core.runs.incidents import IncidentStore, reset_incident_store
    from kazma_core.runs.kinds import KINDS

    rng = random.Random(seed)
    world = World(rng, runs)
    saved_kinds = dict(KINDS)
    KINDS.clear()
    for kind in ("soak_a", "soak_b"):
        KINDS[kind] = KindSpec(kind=kind, adapter="x:y", label=kind, label_ar=kind, source="soak",
                               stall_after_s=STALL, dead_after_s=DEAD)
        registry._ADAPTERS[kind] = SimAdapter(world, kind, plant)
    db = workdir / "incidents.db"
    reset_incident_store(IncidentStore(db))
    sends: Counter[str] = Counter()
    opened_while_fine: list[str] = []
    stats: Counter[str] = Counter()
    original_send = send_module.send_message

    async def fake_send(target: str, text: str, *, backend: str = "telegram", **kw: Any) -> str:
        card = kw.get("run_card")
        if card is None:
            return "sent:" + target  # a recovery or diagnosis note
        if rng.random() < 0.05:
            stats["platform_refused"] += 1
            return "Error: refused"
        sends[card["incident_id"]] += 2 if plant == "double_send" else 1
        return "sent:" + target

    def fake_alert(key: str, title: str, detail: str = "", **kw: Any) -> bool:
        if key.startswith("runs.") and ".inc_" in key:
            sends[key.rsplit(".", 1)[1]] += 1
        if kw.get("on_delivery"):
            kw["on_delivery"](True)
        return True

    import kazma_core.observability.ops_alerts as ops

    original_alert = ops.alert
    send_module.send_message = fake_send
    ops.alert = fake_alert
    supervisor._watch_started = T0
    started = time.monotonic()
    try:
        for cycle in range(cycles):
            world.step()
            if rng.random() < 0.02:  # a restart: a new store object; recover what was cut short
                stats["restarts"] += 1
                store = IncidentStore(db)
                reset_incident_store(store)
                store.recover(now=world.now)
            asyncio.run(supervisor.tick(now=world.now))
            from kazma_core.runs.incidents import get_incident_store

            incidents = get_incident_store()
            for row in incidents.live():
                sim = world.runs.get(row["run_id"].split(":", 1)[1])
                if sim is not None and row["opened_at"] == world.now and world.condition(sim) != row["condition"]:
                    opened_while_fine.append(row["id"])
                if row["state"] == "open" and rng.random() < 0.25:
                    choice = rng.choice([a for a in row["actions"] if a != "diagnose"])
                    if plant == "double_action" and "cancel" in row["actions"]:
                        choice = "cancel"  # the planted bug shows on a cancel
                    outcome = asyncio.run(decide(row["id"], choice, actor="soak", platform="soak"))
                    stats["decisions_" + ("ok" if outcome.ok else outcome.code)] += 1
                    if plant == "double_action" and outcome.ok and choice == "cancel":
                        # The planted bug: a surface acting without the
                        # incident's claim (a second press executed again).
                        kind, native = row["run_id"].split(":", 1)
                        asyncio.run(registry.adapter_for(kind).act(native, "cancel", actor="soak"))
    finally:
        send_module.send_message = original_send
        ops.alert = original_alert
        KINDS.clear()
        KINDS.update(saved_kinds)
        for kind in ("soak_a", "soak_b"):
            registry._ADAPTERS.pop(kind, None)
        reset_incident_store(None)

    rows = IncidentStore(db)._read("SELECT * FROM run_incidents")
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_run[row["run_id"]].append(row)
    violations: dict[str, list[str]] = defaultdict(list)
    for sim in world.runs.values():
        run_id = f"{sim.kind}:{sim.ident}"
        for condition, start, end in sim.episodes:
            lasting = (end or world.now) - start
            if lasting < 2 * TICK:
                continue
            seen = [r for r in by_run[run_id] if r["condition"] == condition
                    and r["opened_at"] <= (end or world.now)]
            if not seen:
                violations["missed"].append(f"{run_id} {condition}@{start}")
    for incident, count in sends.items():
        if count > 1:
            violations["double_send"].append(incident)
    for key, count in world.acted.items():
        if count > 1:
            violations["double_action"].append(key)
    for row in rows:
        if row["state"] not in ("closed", "dismissed", "expired"):
            continue
        # One incident per run and condition is live at a time, so a row of the
        # same key opened after this one was opened after its decision.
        later = [r for r in by_run[row["run_id"]] if r["id"] != row["id"] and r["condition"] == row["condition"]
                 and r["run_version"] == row["run_version"] and r["opened_at"] > row["opened_at"]]
        if later:
            violations["reopened"].append(f"{row['run_id']}:{row['condition']}:{row['run_version']}")
    violations["false_page"].extend(opened_while_fine)
    return {
        "cycles": cycles, "seed": seed, "plant": plant or None, "runs": len(world.runs),
        "simulated_hours": round((world.now - T0) / 3600, 1), "wall_seconds": round(time.monotonic() - started, 1),
        "incidents": len(rows), "cards_sent": sum(sends.values()),
        "states": dict(Counter(r["state"] for r in rows)), "notify": dict(Counter(r["notify"] for r in rows)),
        "stats": dict(stats),
        "violations": {k: v[:20] for k, v in violations.items() if v},
        "violation_counts": {k: len(v) for k, v in violations.items() if v},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cycles", type=int, default=2000)
    parser.add_argument("--runs", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--plant", choices=PLANTS, default="")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="kazma-run-soak-") as tmp:
        report = soak(cycles=args.cycles, runs=args.runs, seed=args.seed, plant=args.plant, workdir=Path(tmp))
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
