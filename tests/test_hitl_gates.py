"""HITL Gate Registry (P0) — state machine, CAS races, idempotency, TTL.

Every guard test carries a negative control (§28): we assert the illegal
transition FAILS, not just that the legal one succeeds.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.safety.hitl_gates import (
    GateRow,
    TransitionConflict,
    claim_gate,
    expire_due_gates,
    fail_gate,
    gate_for,
    gate_registry_enabled,
    live_gates,
    make_gate_id,
    mark_resuming,
    pending_gates,
    register_gate,
    settle_gate,
    supersede_gate,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    # Drop any subscribers a prior test left behind.
    hg.gate_events._subs = []
    yield
    hg.set_db_path_for_tests(None)


def _gate(gid="g1", thread="t1", tool="file_write", **kw) -> GateRow:
    return GateRow(gate_id=gid, thread_id=thread, tool=tool, **kw)


# ── register ────────────────────────────────────────────────────────────────


class TestRegister:
    def test_register_creates_pending(self):
        row = register_gate(_gate())
        assert row.state == "pending"
        assert gate_for("g1").state == "pending"

    def test_register_is_idempotent_on_gate_id(self):
        register_gate(_gate())
        again = register_gate(_gate())
        assert again.gate_id == "g1"
        assert len(pending_gates()) == 1  # negative control: not two rows

    def test_register_is_idempotent_on_alias_id(self):
        # Pre-pause registration under a hash id...
        provisional = make_gate_id("t1", "file_write")
        register_gate(_gate(gid=provisional))
        # ...post-pause registration under the real LangGraph id + alias.
        row = register_gate(_gate(gid="intr-real-1", alias_id=provisional))
        assert len(pending_gates()) == 1  # ONE row — the two-id window closed
        # Row upgraded to the real id; both ids resolve to it.
        assert row.gate_id == "intr-real-1"
        assert gate_for("intr-real-1") is not None
        assert gate_for(provisional).gate_id == "intr-real-1"

    def test_register_reverse_alias_lookup(self):
        # Row registered with the real id + alias first; a later provisional
        # register by the alias id must land on the same row.
        register_gate(_gate(gid="intr-real-2", alias_id="hash-x"))
        again = register_gate(_gate(gid="hash-x"))
        assert again.gate_id == "intr-real-2"
        assert len(pending_gates()) == 1

    def test_concurrent_native_and_hash_is_one_row(self):
        """SSE native-id + ensure_paused_gate hash-id raced into two cards."""
        import json

        args = json.dumps({"timing": "2m", "prompt": "ping"}, sort_keys=True)
        parsed = json.loads(args)
        alias = make_gate_id("t1", "schedule_task", parsed)
        barrier = threading.Barrier(2)

        def native():
            barrier.wait()
            return register_gate(_gate(
                gid="2afa500c471bf3f246a043ff176cf458",
                tool="schedule_task",
                alias_id=alias,
                args_json=args,
            ))

        def hashed():
            barrier.wait()
            return register_gate(_gate(
                gid=alias, tool="schedule_task", args_json=args,
            ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_native = pool.submit(native)
            f_hashed = pool.submit(hashed)
            f_native.result(timeout=5)
            f_hashed.result(timeout=5)
        assert len(pending_gates()) == 1  # negative control: not two dashboard cards

    def test_same_tool_different_args_stay_two_pending(self):
        register_gate(_gate(gid="a", tool="schedule_task", args_json='{"timing":"2m"}'))
        register_gate(_gate(gid="b", tool="schedule_task", args_json='{"timing":"5m"}'))
        assert len(pending_gates()) == 2

    def test_claim_supersedes_already_split_twin(self):
        args = '{"timing": "2m"}'
        register_gate(_gate(gid="native", tool="schedule_task", args_json=args))
        conn = hg._connect()
        try:
            conn.execute(
                "INSERT INTO hitl_gates (gate_id, alias_id, thread_id, tenant_id, "
                "session_id, turn_id, mechanism, kind, tool, args_json, message, "
                "payload_json, state, decision, actor, supersedes, created_at) "
                "VALUES ('gate-ghost','','t1','','','','graph','security',"
                "'schedule_task',?,'','','pending','','','',?)",
                (args, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        assert len(pending_gates()) == 2
        claim_gate("native", "approve", "web:me")
        assert gate_for("native").state == "claimed"
        ghost = gate_for("gate-ghost")
        assert ghost is not None and ghost.state == "superseded"
        assert len(pending_gates()) == 0

    def test_new_row_emits_gate_pending_exactly_once(self):
        events: list[tuple[str, str]] = []
        hg.gate_events.subscribe(lambda ev, r: events.append((ev, r.gate_id)))
        register_gate(_gate())
        register_gate(_gate())  # idempotent repeat — must NOT re-emit
        assert events == [("gate_pending", "g1")]

    def test_ttl_default_applied(self):
        row = register_gate(_gate())
        assert row.expires_at is not None and row.expires_at > time.time()

    def test_ttl_zero_means_no_expiry(self):
        row = register_gate(_gate(), ttl_seconds=0)
        assert row.expires_at is None


# ── state machine legality ──────────────────────────────────────────────────


class TestStateMachine:
    def test_happy_path(self):
        register_gate(_gate())
        assert claim_gate("g1", "approve", "web:me").state == "claimed"
        assert mark_resuming("g1").state == "resuming"
        assert settle_gate("g1").state == "settled"

    def test_claim_requires_pending(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "a")
        settle_gate("g1")
        with pytest.raises(TransitionConflict) as ei:
            claim_gate("g1", "deny", "b")
        assert ei.value.actual == "settled"

    def test_resuming_requires_claimed(self):
        register_gate(_gate())
        with pytest.raises(TransitionConflict) as ei:
            mark_resuming("g1")  # never claimed
        assert ei.value.actual == "pending"

    def test_settle_from_pending_claimed_resuming_all_legal(self):
        for i, prep in enumerate([
            lambda g: None,
            lambda g: claim_gate(g, "approve", "a"),
            lambda g: (claim_gate(g, "approve", "a"), mark_resuming(g)),
        ]):
            gid = f"sm-{i}"
            register_gate(_gate(gid=gid))
            prep(gid)
            assert settle_gate(gid).state == "settled"

    def test_settle_is_idempotent(self):
        register_gate(_gate())
        settle_gate("g1")
        assert settle_gate("g1").state == "settled"  # no raise

    def test_transition_on_missing_row_reports_none(self):
        with pytest.raises(TransitionConflict) as ei:
            claim_gate("ghost", "approve", "a")
        assert ei.value.actual is None

    def test_fail_gate_from_resuming(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "a")
        mark_resuming("g1")
        assert fail_gate("g1", "boom").state == "error"

    def test_fail_gate_illegal_from_pending(self):
        register_gate(_gate())
        with pytest.raises(TransitionConflict):
            fail_gate("g1")

    def test_supersede_only_from_pending(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "a")
        assert supersede_gate("g1", "g2") is None  # negative control
        register_gate(_gate(gid="g3"))
        sup = supersede_gate("g3", "g4")
        assert sup is not None and sup.state == "superseded" and sup.supersedes == "g4"


# ── claim CAS semantics ─────────────────────────────────────────────────────


class TestClaim:
    def test_same_decision_reclaim_is_idempotent(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "web:me")
        row = claim_gate("g1", "approve", "web:other")  # same decision → 200
        assert row.state == "claimed" and row.decision == "approve"

    def test_different_decision_is_conflict_with_winner_info(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "web:me")
        with pytest.raises(TransitionConflict) as ei:
            claim_gate("g1", "deny", "tg:someone")
        assert ei.value.actual == "claimed"
        assert ei.value.decision == "approve"
        assert ei.value.actor == "web:me"

    def test_race_exactly_one_winner(self):
        register_gate(_gate())
        results: list[str] = []
        lock = threading.Lock()

        def contender(i: int):
            try:
                claim_gate("g1", f"decision-{i}", f"actor-{i}")
                with lock:
                    results.append("won")
            except TransitionConflict:
                with lock:
                    results.append("conflict")

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(contender, range(50)))
        assert results.count("won") == 1
        assert results.count("conflict") == 49

    def test_race_loop_many_gates(self):
        # 20 independent gates, 8 racers each — every gate has exactly 1 winner.
        for n in range(20):
            register_gate(_gate(gid=f"race-{n}"))

        def racer(args):
            n, i = args
            try:
                claim_gate(f"race-{n}", f"d{i}", f"a{i}")
                return (n, True)
            except TransitionConflict:
                return (n, False)

        jobs = [(n, i) for n in range(20) for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            outcomes = list(ex.map(racer, jobs))
        for n in range(20):
            wins = [ok for (g, ok) in outcomes if g == n and ok]
            assert len(wins) == 1, f"gate race-{n} had {len(wins)} winners"


# ── queries + silence rule ──────────────────────────────────────────────────


class TestQueries:
    def test_live_gates_reflects_second_pending_next_to_claimed(self):
        # THE incident: gate #1 claimed, gate #2 pending — both live, both visible.
        register_gate(_gate(gid="w", tool="file_write"))
        claim_gate("w", "approve", "web:me")
        register_gate(_gate(gid="d", tool="file_delete"))
        rows = live_gates("t1")
        assert [r.gate_id for r in rows] == ["w", "d"]
        assert rows[0].state == "claimed" and rows[1].state == "pending"

    def test_live_gates_empty_after_all_settled(self):
        register_gate(_gate())
        claim_gate("g1", "approve", "a")
        settle_gate("g1")
        assert live_gates("t1") == []

    def test_pending_gates_only_pending(self):
        register_gate(_gate(gid="p1"))
        register_gate(_gate(gid="p2"))
        claim_gate("p1", "approve", "a")
        assert [r.gate_id for r in pending_gates()] == ["p2"]

    def test_pending_gates_tenant_filter(self):
        register_gate(_gate(gid="a1", tenant_id="tenA"))
        register_gate(_gate(gid="b1", tenant_id="tenB"))
        assert [r.gate_id for r in pending_gates("tenA")] == ["a1"]

    def test_gate_for_by_alias(self):
        register_gate(_gate(gid="real", alias_id="prov"))
        assert gate_for("prov").gate_id == "real"
        assert gate_for("missing") is None


# ── TTL sweep ───────────────────────────────────────────────────────────────


class TestTtl:
    def test_expired_pending_goes_timeout_and_emits(self):
        events: list[str] = []
        hg.gate_events.subscribe(lambda ev, r: events.append(ev))
        register_gate(_gate(), ttl_seconds=0.001)
        time.sleep(0.01)
        expired = expire_due_gates()
        assert [r.gate_id for r in expired] == ["g1"]
        assert gate_for("g1").state == "timeout"
        assert events == ["gate_pending", "gate_settled"]

    def test_unexpired_untouched(self):
        register_gate(_gate(), ttl_seconds=3600)
        assert expire_due_gates() == []  # negative control
        assert gate_for("g1").state == "pending"

    def test_settled_rows_never_expire(self):
        register_gate(_gate(), ttl_seconds=0.001)
        settle_gate("g1")
        time.sleep(0.01)
        assert expire_due_gates() == []
        assert gate_for("g1").state == "settled"


# ── persistence + misc ──────────────────────────────────────────────────────


class TestPersistence:
    def test_rows_survive_schema_reinit(self, tmp_path):
        register_gate(_gate())
        claim_gate("g1", "approve", "a")
        # Simulate process restart: force schema re-init on same file.
        hg._schema_ready = False
        row = gate_for("g1")
        assert row.state == "claimed" and row.decision == "approve"

    def test_subscriber_exception_never_breaks_registry(self):
        def bad(ev, row):
            raise RuntimeError("boom")

        hg.gate_events.subscribe(bad)
        row = register_gate(_gate())  # must not raise
        assert row.state == "pending"

    def test_kill_switch_env(self, monkeypatch):
        monkeypatch.delenv("KAZMA_GATE_REGISTRY", raising=False)
        assert gate_registry_enabled() is True
        monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
        assert gate_registry_enabled() is False

    def test_make_gate_id_stable(self):
        a = make_gate_id("t", "tool", {"x": 1})
        b = make_gate_id("t", "tool", {"x": 1})
        c = make_gate_id("t", "tool", {"x": 2})
        assert a == b and a != c

    def test_args_and_payload_parse_defensively(self):
        row = register_gate(_gate(args_json="not-json", payload_json="[]"))
        assert row.args() == {} and row.payload() == {}


# ── re-ask semantics (terminal collision) ───────────────────────────────────


class TestReAsk:
    def test_same_hash_id_after_terminal_is_a_new_gate(self):
        # User asked again after a deny: same tool+args → same hash id.
        # The settled row must NOT eat the new question.
        gid = make_gate_id("t1", "file_write", {"p": 1})
        register_gate(_gate(gid=gid))
        claim_gate(gid, "deny", "a")
        settle_gate(gid)
        again = register_gate(_gate(gid=gid))
        assert again.gate_id != gid          # fresh row
        assert again.state == "pending"
        assert len(pending_gates()) == 1

    def test_native_id_repeat_after_terminal_is_idempotent(self):
        # A LangGraph native id can never recur as a new pause — late
        # re-register of the same settled pause returns the terminal row.
        register_gate(_gate(gid="intr-native-9"))
        settle_gate("intr-native-9")
        again = register_gate(_gate(gid="intr-native-9"))
        assert again.gate_id == "intr-native-9" and again.state == "settled"
        assert pending_gates() == []

    def test_third_ask_lands_on_live_second_row_not_another_ghost(self):
        gid = make_gate_id("t1", "file_write", {"p": 1})
        register_gate(_gate(gid=gid))
        settle_gate(gid)
        second = register_gate(_gate(gid=gid))   # re-ask → fresh live row
        third = register_gate(_gate(gid=gid))    # same ask again (poll repeat)
        assert third.gate_id == second.gate_id   # idempotent on the LIVE row
        assert len(pending_gates()) == 1


# ── CAS column whitelist ─────────────────────────────────────────────────────


class TestCasWhitelist:
    """The CAS helper interpolates column NAMES into SQL — only a fixed
    whitelist may pass. Negative control (§28): an unknown key raises."""

    def test_unknown_column_rejected(self):
        register_gate(_gate(gid="cas-1"))
        conn = hg._connect()
        try:
            with pytest.raises(ValueError, match="illegal column"):
                hg._cas(conn, "cas-1", "pending", "claimed",
                        {"decision; DROP TABLE hitl_gates--": "x"})
        finally:
            conn.close()
        assert gate_for("cas-1").state == "pending"  # untouched

    def test_whitelisted_columns_still_work(self):
        register_gate(_gate(gid="cas-2"))
        row = claim_gate("cas-2", "approve", "web:test")
        assert row.state == "claimed" and row.decision == "approve"


class TestAbortSettlesPending:
    """/abort must settle pending gates; settle_thread_gates must not.

    Negative control: the turn-terminal helper leaves pending rows live so a
    second question stays open. Abort is terminal — pending dies too.
    """

    @pytest.mark.asyncio
    async def test_abort_settles_pending_gate(self):
        from kazma_ui.hitl_gate_bridge import abort_thread_hitl, settle_thread_gates

        register_gate(_gate(gid="g-abort", thread="t-abort"))
        assert live_gates("t-abort")[0].state == "pending"
        await settle_thread_gates("t-abort")
        assert live_gates("t-abort")[0].state == "pending"
        await abort_thread_hitl("t-abort")
        assert live_gates("t-abort") == []
        row = gate_for("g-abort")
        assert row is not None and row.state == "settled"
        assert row.decision == "aborted"
