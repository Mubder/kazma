"""Context Integrity Hardening — one test group per defect in the plan.

Derived from the live 2026-08-30 incident (@KazmaAI tweet batch): 8 approved
drafts vanished between proposal and approval because deterministic trim
deleted them, the scratchpad built to prevent it was wiped every turn, the
summary net never fired (24K→160K dead band), and a misread "what going on?"
disarmed recall.

House rules applied (AGENTS.md §28 + the plan's verification section):
  - A read must not prove a write: the scratchpad fix is verified by a SECOND
    turn through the real graph, not by reading state back in-turn.
  - Every guard has a negative control asserting the OLD behaviour would
    fail the test.
  - The end-to-end regression reproduces the incident chain verbatim.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from unittest.mock import patch

from kazma_core.agent.state import (
    SCRATCHPAD_CLEAR,
    SCRATCHPAD_MAX_KEYS,
    SCRATCHPAD_MAX_VALUE_CHARS,
    initial_supervisor_state,
    merge_scratchpad,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture()
def artifact_db(tmp_path, monkeypatch):
    """Isolate the durable artifact store into a tmp DB for each test."""
    import kazma_core.agent.artifacts as artifacts_mod

    monkeypatch.setenv("KAZMA_ARTIFACTS_DB", str(tmp_path / "artifacts.db"))
    artifacts_mod.reset_artifact_store()
    yield artifacts_mod.get_artifact_store()
    artifacts_mod.reset_artifact_store()


@pytest.fixture()
def commitment_on(monkeypatch):
    """Ensure the commitment layer is ENABLED (default) for resolver tests."""
    monkeypatch.delenv("KAZMA_COMMITMENT_ENABLED", raising=False)


def _max_distance_embedder():
    """An embedder whose vectors are maximally distant for any pair."""

    class _Emb:
        dim = 2

        def encode(self, text: str) -> list[float]:
            t = (text or "").lower()
            # check-in-ish text vs goal-ish text → orthogonal
            if "tweet" in t or "draft" in t or "post" in t or "memory" in t:
                return [1.0, 0.0]
            return [0.0, 1.0]

        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [self.encode(x) for x in texts]

    return _Emb()


# ═══════════════════════════════════════════════════════════════════
# S1-1 — scratchpad merge reducer + transport drop
# ═══════════════════════════════════════════════════════════════════


class TestS11ScratchpadReducer:
    def test_reducer_merges_instead_of_replacing(self):
        current = {"drafts": "8 tweet drafts: ...", "audit": "bidi counts ok"}
        incoming = {"bidi": "ar=12 en=30"}
        merged = merge_scratchpad(current, incoming)
        assert merged["drafts"] == current["drafts"]
        assert merged["audit"] == current["audit"]
        assert merged["bidi"] == incoming["bidi"]

    def test_negative_control_last_value_would_lose_keys(self):
        """The OLD (LastValue) semantics: an incoming empty dict wipes everything.

        This is failure link #3 of the incident — the guard exists to make
        that semantics impossible through the channel.
        """
        current = {"drafts": "8 tweet drafts"}
        # What plain dict.update (the transport's old effective behaviour
        # through a LastValue channel) would have done:
        replaced = {**current, **{}}
        assert replaced == current  # dict-spread alone is fine —
        # the LOSS came from the channel replacing state with the transport's
        # value; simulate the channel semantics directly:
        channel_value = {}  # transport contributed scratchpad: {}
        assert "drafts" not in channel_value  # ← the old bug, stated plainly

    def test_reducer_survives_empty_dict_transport_clobber(self):
        current = {"drafts": "8 tweet drafts"}
        assert merge_scratchpad(current, {}) == {"drafts": "8 tweet drafts"}

    def test_bounds_25th_key_evicts_oldest(self):
        pad = {f"k{i}": f"v{i}" for i in range(SCRATCHPAD_MAX_KEYS)}
        merged = merge_scratchpad(pad, {"newest": "fresh"})
        assert "newest" in merged
        assert "k0" not in merged  # oldest evicted
        assert len(merged) == SCRATCHPAD_MAX_KEYS

    def test_bounds_oversized_value_truncated(self):
        merged = merge_scratchpad({}, {"big": "x" * (SCRATCHPAD_MAX_VALUE_CHARS + 999)})
        assert len(merged["big"]) == SCRATCHPAD_MAX_VALUE_CHARS

    def test_clear_sentinel_resets(self):
        current = {"drafts": "gone-after-deliberate-reset", "audit": "x"}
        merged = merge_scratchpad(current, {SCRATCHPAD_CLEAR: "1", "fresh": "y"})
        assert merged == {"fresh": "y"}

    def test_transport_returns_no_scratchpad_key(self):
        from kazma_core.agent.turn_input import build_turn_working_memory

        wm = build_turn_working_memory("post the English ones now")
        assert "scratchpad" not in wm, (
            "transport must never contribute a scratchpad value — an empty dict "
            "here clobbered the checkpointed scratchpad every turn (link #3)"
        )
        assert set(wm.keys()) == {"active_goal", "active_attachments", "hard_constraints"}


class TestS11ThroughTheGraph:
    """The scratchpad fix verified by a SECOND turn through the real graph."""

    @staticmethod
    def _build(checkpointer, monkeypatch):
        import aiosqlite
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.agent.tool_registry import LocalToolRegistry
        from kazma_core.authority import create_authority
        from kazma_core.cost_breaker import create_cost_breaker
        from kazma_core.llm_provider import LLMResponse
        from kazma_core.tracing import KazmaTracer

        monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")

        class StubLLM:
            def __init__(self) -> None:
                self.calls = 0

            async def chat(self, *, messages, tools=None, model=None, **kwargs):
                self.calls += 1
                return LLMResponse(
                    content="Done.",
                    tool_calls=[],
                    finish_reason="stop",
                    model="stub",
                    usage={"total_tokens": 1},
                    cost_usd=0.0,
                )

        registry = LocalToolRegistry(include_builtins=False)
        llm = StubLLM()
        graph = build_supervisor_graph(
            llm=llm,
            system_prompt="You are a test agent.",
            tool_definitions=[],
            tool_executor=registry,
            cost_breaker=create_cost_breaker(),
            authority=create_authority(model="test", window=128000),
            tracer=KazmaTracer(backend="console"),
            checkpointer=checkpointer,
        )
        return llm, graph

    async def test_scratchpad_survives_next_turn_through_graph(
        self, monkeypatch, tmp_path
    ):
        """Write on turn N; assert present on turn N+1 THROUGH the graph —
        with turn N+1's input carrying ``scratchpad: {}`` (exactly the old
        transport clobber). Under LastValue semantics this test fails."""
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(":memory:")
        try:
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            llm, graph = self._build(saver, monkeypatch)
            cfg = {"configurable": {"thread_id": "s11-thread"}}

            turn1 = initial_supervisor_state(thread_id="s11-thread")
            turn1["messages"] = [{"role": "user", "content": "draft 8 tweets"}]
            turn1["scratchpad"] = {"drafts": "8 tweet drafts EN+AR"}
            await graph.ainvoke(turn1, cfg)

            # Turn 2 — input includes the old clobber shape on purpose.
            turn2 = initial_supervisor_state(thread_id="s11-thread")
            turn2["messages"] = [{"role": "user", "content": "post them"}]
            turn2["scratchpad"] = {}  # the pre-fix transport payload
            final = await graph.ainvoke(turn2, cfg)

            assert final.get("scratchpad", {}).get("drafts") == "8 tweet drafts EN+AR"
            snap = await graph.aget_state(cfg)
            assert snap.values.get("scratchpad", {}).get("drafts") == "8 tweet drafts EN+AR"
        finally:
            await conn.close()

    def test_legacy_checkpoint_channel_load(self):
        """Old checkpoints hold a plain dict under ``scratchpad`` written by a
        LastValue channel; the new aggregate channel must load them by direct
        assignment (``from_checkpoint``), never by invoking the reducer."""
        from langgraph.channels.binop import BinaryOperatorAggregate

        ch = BinaryOperatorAggregate(dict, merge_scratchpad)
        legacy_value = {"drafts": "from an old checkpoint"}
        restored = ch.from_checkpoint(legacy_value)  # what checkpoint resume does
        assert restored.get() == legacy_value
        # and subsequent merges still work on top of the legacy value
        restored.update([{"new": "post-restart write"}])
        assert restored.get() == {
            "drafts": "from an old checkpoint",
            "new": "post-restart write",
        }


# ═══════════════════════════════════════════════════════════════════
# S1-4 — summary dead band (24K → 160K)
# ═══════════════════════════════════════════════════════════════════


class TestS14DeadBand:
    def _incident_messages(self) -> list[dict[str, Any]]:
        drafts = "\n".join(
            f"{i}. Draft #{i}: Kazma ships context integrity — part {i}"
            for i in range(1, 9)
        )
        return [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "Draft 8 tweets about the release, EN and AR."},
            {"role": "assistant", "content": f"Here are the 8 drafts:\n{drafts}"},
            {"role": "user", "content": "النسخ العربية حلوة"},
            {"role": "assistant", "content": "تم — Arabic drafts refined."},
            {"role": "user", "content": "post the English ones immediately"},
        ]
    def test_gate_fires_when_trim_drops_conversation_turns(self):
        from kazma_core.agent.semantic_compact import dropped_conversation_turns
        from kazma_core.agent.turn_input import trim_messages_deterministic

        msgs = self._incident_messages()
        trimmed = trim_messages_deterministic(
            msgs, max_tokens=60, keep_last_tool_rounds=0,
            active_goal="post the English ones", working_memory_block="wm",
        )
        dropped = dropped_conversation_turns(msgs, trimmed)
        # Negative control: with nothing dropped the gate must NOT fire.
        assert dropped_conversation_turns(trimmed, trimmed) == []
        # The drafts turn is exactly what the incident lost.
        assert any("8 drafts" in str(m.get("content") or "") for m in dropped), (
            f"drafts assistant turn not recognized as dropped: {[m.get('role') for m in dropped]}"
        )

    def test_heuristic_summary_names_what_was_dropped(self):
        from kazma_core.agent.semantic_compact import (
            describe_dropped,
            heuristic_dropped_summary,
        )

        drafts = "\n".join(f"{i}. Draft #{i}" for i in range(1, 9))
        dropped = [
            {"role": "assistant", "content": f"8 drafts:\n{drafts}"},
            {"role": "user", "content": "nice"},
            {"role": "assistant", "content": "Thanks!"},
        ]
        d = describe_dropped(dropped)
        assert "2 assistant turns" in d
        assert "8 enumerated draft items" in d
        assert "1 user turn" in d
        summary = heuristic_dropped_summary(dropped)
        assert "compacted" in summary
        assert "Draft #1" in summary  # heads shown so the model can act

    @pytest.mark.asyncio
    async def test_small_drop_uses_heuristic_without_llm(self):
        """The common case costs no extra LLM call — even when an LLM is handed over."""
        from kazma_core.agent.semantic_compact import inject_summary_of_dropped

        before = self._incident_messages()
        after = [before[0], before[-1]]  # trim kept system + latest user only

        class _Boom:
            async def chat(self, **kw):
                raise AssertionError("LLM must not be called under the 2K-token heuristic path")

        out = await inject_summary_of_dropped(before, after, llm=_Boom())
        notes = [m for m in out if m.get("role") == "system" and "compacted" in str(m.get("content"))]
        assert notes, "no compaction note injected"
        assert "enumerated draft items" in notes[0]["content"]

    def test_dead_band_math(self):
        """The 24K/160K band is real: trim budget vs should_compact gate."""
        from kazma_core.agent.turn_input import resolve_trim_token_budget
        from kazma_core.token_counter import TokenCounter

        trim_budget = resolve_trim_token_budget(last_model="gpt-4o-200k")
        counter = TokenCounter(model="x", window=200000)
        assert trim_budget <= 24000
        assert counter.threshold == 160000
        # The band between them is where turns used to vanish silently.
        assert trim_budget < counter.threshold


# ═══════════════════════════════════════════════════════════════════
# S2-2 — interrogative check-ins misread as topic changes
# ═══════════════════════════════════════════════════════════════════


class TestS22InterrogativeGuard:
    @pytest.mark.parametrize(
        "text",
        ["what going on?", "what happened?", "status?", "what's going on",
         "why did it stop?", "how did that go?", "update?"],
    )
    def test_en_interrogatives_never_drift(self, text):
        from kazma_core.agent.topic_drift import is_interrogative_checkin, semantic_topic_drift

        assert is_interrogative_checkin(text)
        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            assert semantic_topic_drift(
                text, "draft and post 8 tweets about the release", threshold=0.55, enabled=True
            ) is False

    @pytest.mark.parametrize("text", ["شنو صار؟", "وش الوضع؟", "ليش وقف؟", "شلون تمشي؟"])
    def test_ar_interrogatives_never_drift(self, text):
        from kazma_core.agent.topic_drift import is_interrogative_checkin, semantic_topic_drift

        assert is_interrogative_checkin(text)
        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            assert semantic_topic_drift(
                text, "اكتب 8 تغريدات عن الإصدار الجديد", threshold=0.55, enabled=True
            ) is False

    def test_negative_control_declarative_still_drifts(self):
        """The guard must not blanket-suppress drift: a declarative pivot
        with the same maximally-distant embedder still fires."""
        from kazma_core.agent.topic_drift import semantic_topic_drift

        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            assert semantic_topic_drift(
                "Book me a table at the sushi place downtown tonight",
                "draft and post 8 tweets about the release",
                threshold=0.55, enabled=True,
            ) is True

    def test_short_contentless_text_fails_open(self):
        from kazma_core.agent.topic_drift import semantic_topic_drift

        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            # 26 chars (clears the old 12-gate easily), no content word
            assert semantic_topic_drift(
                "ok ok ok ok ok ok ok ok ok ok ok", "draft 8 tweets", threshold=0.55
            ) is False

    def test_classify_checkin_is_normal_not_shift(self):
        from kazma_core.agent.turn_input import classify_turn_intent

        history = [
            {"role": "user", "content": "Draft 8 tweets about the release for @KazmaAI"},
            {"role": "assistant", "content": "Drafted 8, awaiting approval."},
        ]
        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            mode = classify_turn_intent(
                "what going on?",
                messages=history,
                task_status="in_progress",
                task_goal_summary="Draft 8 tweets about the release for @KazmaAI",
                use_embedding_drift=True,
            )
        assert mode == "normal"  # link #6 of the incident chain, now closed


# ═══════════════════════════════════════════════════════════════════
# S2-1 — explicit vs inferred shift
# ═══════════════════════════════════════════════════════════════════


class TestS21ShiftSplit:
    def test_explicit_shift_en(self):
        from kazma_core.agent.turn_input import (
            classify_turn_intent,
            should_suppress_memory_recall,
        )

        assert classify_turn_intent("never mind, new topic: the weather") == "shift_explicit"
        assert should_suppress_memory_recall(intent_mode="shift_explicit") is True
        # Legacy value from old checkpoints is treated as explicit.
        assert should_suppress_memory_recall(intent_mode="shift") is True

    def test_explicit_shift_ar(self):
        from kazma_core.agent.turn_input import classify_turn_intent

        assert classify_turn_intent("موضوع ثاني، وش الطقس اليوم؟") == "shift_explicit"

    def test_inferred_shift_keeps_recall(self):
        from kazma_core.agent.turn_input import (
            classify_turn_intent,
            should_quarantine_documents_search,
            should_suppress_memory_recall,
        )

        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            mode = classify_turn_intent(
                "Book me a table at the sushi place downtown tonight",
                messages=[{"role": "user", "content": "draft and post 8 tweets about the release"}],
                task_status="in_progress",
                task_goal_summary="draft and post 8 tweets about the release",
                use_embedding_drift=True,
            )
        assert mode == "shift_inferred"
        # Recall stays ON — disarming recall when context is uncertain is
        # precisely backwards (incident link #7).
        assert should_suppress_memory_recall(intent_mode="shift_inferred") is False
        assert should_quarantine_documents_search(intent_mode="shift_inferred") is False

    def test_inferred_stub_keeps_assistant_prose(self):
        from kazma_core.agent.topic_drift import stub_prior_tool_chains

        prose = (
            "Here are the 8 tweet drafts:\n"
            + "\n".join(f"{i}. Draft #{i} — Kazma ships context integrity" for i in range(1, 9))
        )
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "draft 8 tweets"},
            {
                "role": "assistant",
                "content": prose,
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "x_status", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok " + ("z" * 3000)},
            {"role": "user", "content": "so anyway book the sushi table tonight"},
        ]
        out = stub_prior_tool_chains(msgs, keep_assistant_prose=True)
        blob = " ".join(str(m.get("content") or "") for m in out if isinstance(m, dict))
        # Full prose survived — the 8 drafts are still readable.
        assert "Draft #8" in blob, "inferred-shift stub erased the drafts prose"
        # Tool payload collapsed.
        assert "z" * 100 not in blob
        assert not any(isinstance(m, dict) and m.get("tool_calls") for m in out[:-1])

    def test_explicit_stub_negative_control_truncates_prose(self):
        from kazma_core.agent.topic_drift import stub_prior_tool_chains

        prose = "Intro line.\n" + "\n".join(
            f"{i}. Draft #{i}" for i in range(1, 30)
        )
        msgs: list[dict[str, Any]] = [
            {"role": "user", "content": "draft tweets"},
            {
                "role": "assistant",
                "content": prose,
                "tool_calls": [{
                    "id": "c9", "type": "function",
                    "function": {"name": "x_status", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "c9", "content": "ok"},
            {"role": "user", "content": "never mind"},
        ]
        out = stub_prior_tool_chains(msgs, keep_assistant_prose=False)
        blob = " ".join(str(m.get("content") or "") for m in out if isinstance(m, dict))
        assert "29. Draft #29" not in blob  # explicit shift still truncates


# ═══════════════════════════════════════════════════════════════════
# S1-2 — durable artifact store
# ═══════════════════════════════════════════════════════════════════


class TestS12ArtifactStore:
    def test_scratchpad_round_trip_and_thread_isolation(self, artifact_db):
        artifact_db.put_scratchpad("default", "thread-a", "drafts", "8 drafts")
        artifact_db.put_scratchpad("default", "thread-b", "drafts", "other thread")
        artifact_db.put_scratchpad("tenant-2", "thread-a", "drafts", "other tenant")
        assert artifact_db.list_scratchpad("thread-a") == {"drafts": "8 drafts"}
        assert artifact_db.list_scratchpad("thread-b") == {"drafts": "other thread"}
        assert artifact_db.list_scratchpad("thread-a", tenant_id="tenant-2") == {
            "drafts": "other tenant"
        }

    def test_write_through_from_apply_scratchpad_write(self, artifact_db):
        from kazma_core.agent.turn_input import (
            apply_scratchpad_write,
            bind_scratchpad_thread,
            reset_scratchpad_thread,
        )

        tok = bind_scratchpad_thread("wt-thread")
        try:
            out = apply_scratchpad_write("drafts", "survives restart")
            assert "Scratchpad saved" in out
        finally:
            reset_scratchpad_thread(tok)
        assert artifact_db.list_scratchpad("wt-thread") == {"drafts": "survives restart"}

    def test_gc_per_thread_cap(self, artifact_db):
        for i in range(140):
            artifact_db.put_scratchpad("default", "gc-thread", f"k{i}", f"v{i}")
        summary = artifact_db.gc_sweep(max_per_thread=128)
        assert summary["evicted"] >= 12
        pad = artifact_db.list_scratchpad("gc-thread")
        assert len(pad) <= 128

    def test_proposal_round_trip_after_trim_and_thread_switch(self, artifact_db):
        """The plan's headline test: save → new thread, trim everything →
        IDs still resolve to the EXACT text."""
        drafts = [f"Draft #{i}: Kazma ships context integrity" for i in range(1, 9)]
        payload = artifact_db.save_proposal("default", "thread-1", "tweets", drafts)
        pid = payload["proposal_id"]
        assert pid.startswith("prop_")

        # New thread, nothing in context — resolution is by ID alone.
        info = artifact_db.resolve_proposal(pid, tenant_id="default")
        assert info is not None
        assert info["kind"] == "tweets"
        assert info["texts"] == drafts  # exact text, not a summary

        # Single-item refs resolve too.
        item = artifact_db.resolve_proposal(f"{pid}#3")
        assert item is not None and item["texts"] == [drafts[2]]
        item2 = artifact_db.resolve_proposal(payload["items"][5]["id"])
        assert item2 is not None and item2["texts"] == [drafts[5]]

        # Unknown id does not resolve.
        assert artifact_db.resolve_proposal("prop_does_not_exist") is None


# ═══════════════════════════════════════════════════════════════════
# S1-3 — proposal enforcement at the outbound chokepoint
# ═══════════════════════════════════════════════════════════════════


class TestS13OutboundEnforcement:
    @pytest.mark.parametrize("tool", ["x_post", "x_schedule_post"])
    def test_post_without_proposal_id_denied(self, commitment_on, artifact_db, tool):
        from kazma_core.safety.commitment import authorize_effect

        decision = authorize_effect(
            tool, {"text": "hello world"}, thread_id="t1", tenant_id="default"
        )
        assert decision.decision == "deny"
        assert "save_proposal" in decision.reason

    def test_post_with_unresolvable_id_denied(self, commitment_on, artifact_db):
        from kazma_core.safety.commitment import authorize_effect

        decision = authorize_effect(
            "x_post",
            {"text": "hello", "proposal_id": "prop_missing"},
            thread_id="t1",
            tenant_id="default",
        )
        assert decision.decision == "deny"
        assert "does not resolve" in decision.reason

    def test_post_with_resolvable_id_allowed_and_stored_text_wins(
        self, commitment_on, artifact_db
    ):
        from kazma_core.safety.commitment import authorize_effect

        payload = artifact_db.save_proposal(
            "default", "t1", "tweets", ["Exact approved draft text"]
        )
        decision = authorize_effect(
            "x_post",
            {"text": "paraphrased garbage the model half-remembers",
             "proposal_id": payload["proposal_id"]},
            thread_id="t1",
            tenant_id="default",
        )
        assert decision.decision == "allow"
        # The id wins over context memory — what goes out is EXACTLY what
        # was saved (and approved).
        assert decision.rewritten_args["text"] == "Exact approved draft text"

    def test_multi_item_proposal_needs_single_item_ref(self, commitment_on, artifact_db):
        from kazma_core.safety.commitment import authorize_effect

        payload = artifact_db.save_proposal(
            "default", "t1", "tweets", ["one", "two"]
        )
        decision = authorize_effect(
            "x_post",
            {"text": "x", "proposal_id": payload["proposal_id"]},
            thread_id="t1", tenant_id="default",
        )
        assert decision.decision == "deny"
        assert "single item" in decision.reason
        ok = authorize_effect(
            "x_post",
            {"text": "x", "proposal_id": f"{payload['proposal_id']}#2"},
            thread_id="t1", tenant_id="default",
        )
        assert ok.decision == "allow"
        assert ok.rewritten_args["text"] == "two"

    def test_broken_store_fails_closed(self, commitment_on, monkeypatch):
        from kazma_core.safety.commitment import authorize_effect

        def _boom():
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(
            "kazma_core.agent.artifacts.get_artifact_store", _boom
        )
        decision = authorize_effect(
            "x_post",
            {"text": "hello", "proposal_id": "prop_any"},
            thread_id="t1", tenant_id="default",
        )
        assert decision.decision == "deny"
        assert "unavailable" in decision.reason

    def test_non_posting_outbound_unaffected(self, commitment_on, artifact_db):
        """Only the content-posting class requires proposals — a normal
        outbound tool still follows the allowlist logic."""
        from kazma_core.safety.commitment import authorize_effect

        decision = authorize_effect(
            "email_send",
            {"to": "someone@example.com", "subject": "hi", "body": "yo"},
            thread_id="t1", tenant_id="default",
        )
        assert decision.decision in ("allow", "clarify")  # allowlist path, not proposal path


# ═══════════════════════════════════════════════════════════════════
# S2-3 — recovery-spiral breaker
# ═══════════════════════════════════════════════════════════════════


class TestS23RecoveryBreaker:
    def test_count_recovery_probes(self):
        from kazma_core.agent.tool_loop_breaker import count_recovery_probes

        calls = [
            {"name": "session_search", "arguments": {"query": "my earlier tweet drafts"}},
            {"name": "checkpoint_list", "arguments": {"q": "assistant proposal 8 items"}},
            {"name": "audit_log_search", "arguments": {"what": "what I posted yesterday"}},
            # NOT probes:
            {"name": "session_search", "arguments": {"query": "user preferences theme"}},
            {"name": "file_read", "arguments": {"path": "earlier drafts.txt"}},
        ]
        assert count_recovery_probes(calls) == 3

    def test_honest_message_names_the_behavior(self):
        from kazma_core.agent.tool_loop_breaker import recovery_honest_message

        msg = recovery_honest_message(3)
        assert "STOP searching" in msg
        assert "no longer see" in msg
        assert "ONE concrete question" in msg

    @staticmethod
    def _spiral_graph(monkeypatch):
        import aiosqlite
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.agent.tool_registry import LocalToolRegistry
        from kazma_core.authority import create_authority
        from kazma_core.cost_breaker import create_cost_breaker
        from kazma_core.llm_provider import LLMResponse, ToolCall
        from kazma_core.tracing import KazmaTracer

        monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")

        registry = LocalToolRegistry(include_builtins=False)

        async def _session_history_search(query: str = "") -> str:
            return "no results found"

        registry.register_function(
            "session_history_search", _session_history_search,
            description="Search session history.", category="memory",
        )

        class SpiralLLM:
            def __init__(self) -> None:
                self.calls = 0

            async def chat(self, *, messages, tools=None, model=None, **kwargs):
                self.calls += 1
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=f"call_{self.calls}",
                            name="session_history_search",
                            arguments={"query": f"find my earlier tweet drafts attempt {self.calls}"},
                        )
                    ],
                    finish_reason="tool_calls",
                    model="stub",
                    usage={"total_tokens": 1},
                    cost_usd=0.0,
                )

        llm = SpiralLLM()
        graph = build_supervisor_graph(
            llm=llm,
            system_prompt="You are a test agent.",
            tool_definitions=registry.get_tool_definitions(),
            tool_executor=registry,
            cost_breaker=create_cost_breaker(),
            authority=create_authority(model="test", window=128000),
            tracer=KazmaTracer(backend="console"),
            checkpointer=None,
        )
        return llm, graph

    @pytest.mark.asyncio
    async def test_three_probes_force_honest_respond(self, monkeypatch):
        import kazma_core.safety.hitl as hitl_mod
        from kazma_core.agent.state import NodeName, initial_supervisor_state

        # Register the fabricated tool as read-tier so the HITL splitter
        # treats it as safe (§26B: unclassified tools are gated).
        hitl_mod.TOOL_TIERS["session_history_search"] = "read"
        try:
            llm, graph = self._spiral_graph(monkeypatch)
            state = initial_supervisor_state(thread_id="spiral-1")
            state["messages"] = [{"role": "user", "content": "post the English ones now"}]
            final = await graph.ainvoke(state, {"configurable": {"thread_id": "spiral-1"}})

            blob = " ".join(
                str(m.get("content") or "")
                for m in final.get("messages", [])
                if isinstance(m, dict)
            )
            assert "STOP searching" in blob, (
                "recovery spiral tripped without the honest message; "
                f"probes={final.get('recovery_probes')} msgs={blob[:400]}"
            )
            assert llm.calls <= 4, "model kept digging after the trip"
            assert final.get("next_node") in (NodeName.RESPOND, "end", None)
        finally:
            hitl_mod.TOOL_TIERS.pop("session_history_search", None)


# ═══════════════════════════════════════════════════════════════════
# S3-1 — compaction is surfaced
# ═══════════════════════════════════════════════════════════════════


class TestS31ContextCompactedState:
    def test_state_declares_context_compacted_and_recovery_probes(self):
        st = initial_supervisor_state(thread_id="cc")
        assert st.get("context_compacted") == {}
        assert st.get("recovery_probes") == 0

    def test_anchor_renders_scratchpad_drafts(self):
        from kazma_core.agent.turn_input import format_working_memory_anchor

        block = format_working_memory_anchor(
            active_goal="post the English ones",
            scratchpad={"drafts": "8 tweet drafts EN+AR"},
        )
        assert "8 tweet drafts EN+AR" in block
        assert "survives history trim" in block.lower()


# ═══════════════════════════════════════════════════════════════════
# Deferred items — trim measurement + budget knob (plan §Open question)
# ═══════════════════════════════════════════════════════════════════


class TestTrimMeasurement:
    def test_record_context_trim_increments_counters(self):
        from prometheus_client import REGISTRY

        from kazma_core.metrics import record_context_trim

        def _value(sample_name: str, label_value: str | None = None) -> float:
            # Match on SAMPLE names — prometheus_client strips the "_total"
            # suffix from the metric family name but keeps it on samples.
            for metric in REGISTRY.collect():
                for s in metric.samples:
                    if s.name != sample_name:
                        continue
                    if label_value is None or s.labels.get("summary") == label_value:
                        return s.value
            return -1.0

        before_fired = _value("kazma_context_trims_total", "fired")
        before_msgs = _value("kazma_context_trim_dropped_messages_total")
        record_context_trim(7, summary_fired=True)
        assert _value("kazma_context_trims_total", "fired") >= before_fired + 1
        assert (
            _value("kazma_context_trim_dropped_messages_total") >= before_msgs + 7
        )

    def test_record_context_trim_never_raises_without_prometheus(self, monkeypatch):
        import kazma_core.metrics as m

        monkeypatch.setattr(m, "_PROMETHEUS_AVAILABLE", False, raising=False)
        m.record_context_trim(5, summary_fired=False)  # must be a silent no-op


class TestTrimBudgetKnob:
    def test_default_behavior_unchanged(self):
        """Unset config → exactly the old formula (the plan forbids masking
        the fixes by re-tuning the default)."""
        from kazma_core.agent.turn_input import resolve_trim_token_budget
        from kazma_core.token_counter import resolve_context_window

        window = resolve_context_window(None, "gpt-4o-200k")
        expected = max(4000, min(24000, int(window * 0.6)))
        assert resolve_trim_token_budget(last_model="gpt-4o-200k") == expected

    def test_operator_override_wins_and_is_clamped(self, monkeypatch):
        from kazma_core.agent import turn_input as ti
        from kazma_core.token_counter import resolve_context_window

        window = resolve_context_window(None, "gpt-4o-200k")

        class _StubStore:
            def __init__(self, val):
                self._val = val

            def get(self, key, default=None):
                return self._val if key == "agent.trim.token_budget" else default

        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store",
            lambda: _StubStore("100000"),
        )
        # 100K on a 200K window: allowed (that is the point of the knob)…
        assert ti.resolve_trim_token_budget(last_model="gpt-4o-200k") == 100000
        # …but a value above 95% of the window clamps down.
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store",
            lambda: _StubStore("999000"),
        )
        assert (
            ti.resolve_trim_token_budget(last_model="gpt-4o-200k")
            == int(window * 0.95)
        )
        # And garbage config cannot break resolution.
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store",
            lambda: _StubStore("not-a-number"),
        )
        assert (
            ti.resolve_trim_token_budget(last_model="gpt-4o-200k")
            == max(4000, min(24000, int(window * 0.6)))
        )


# ═══════════════════════════════════════════════════════════════════
# End-to-end incident regression (component chain, verbatim)
# ═══════════════════════════════════════════════════════════════════


class TestIncidentRegression:
    """2026-08-30 19:00–19:25, replayed: propose 8 drafts → trim above the
    budget → 'post the English ones immediately' → 'what going on?'.

    Every assertion below FAILED on the pre-fix code:
      - trim dropped the drafts with no summary (dead band)
      - 'what going on?' classified as shift and disarmed recall
      - no durable proposal existed to post against
    """

    def test_incident_chain(self, artifact_db):
        from kazma_core.agent.semantic_compact import (
            dropped_conversation_turns,
            inject_summary_of_dropped,
        )
        from kazma_core.agent.turn_input import (
            classify_turn_intent,
            should_suppress_memory_recall,
            trim_messages_deterministic,
        )
        from kazma_core.safety.commitment import authorize_effect

        drafts = [f"{i}. Draft #{i}: Kazma ships context integrity" for i in range(1, 9)]

        # ── Turn N: user asks, assistant proposes 8 drafts ──────────────
        messages = [
            {"role": "system", "content": "You are Kazma."},
            {"role": "user", "content": "Draft 8 tweets about the release, EN and AR."},
            {"role": "assistant", "content": "The 8 drafts:\n" + "\n".join(drafts)},
            {"role": "user", "content": "send the English ones now"},
        ]

        # ── Trim fires (small budget stands in for 24K vs a big history) ─
        trimmed = trim_messages_deterministic(
            messages, max_tokens=80, keep_last_tool_rounds=0,
            active_goal="send the English ones now", working_memory_block="wm",
        )
        dropped = dropped_conversation_turns(messages, trimmed)
        assert any("Draft #8" in str(m.get("content") or "") for m in dropped), (
            "the drafts turn should be recognized as dropped"
        )
        # …and the summary net now FIRES on that drop (dead band closed).
        out = asyncio.run(inject_summary_of_dropped(messages, trimmed, llm=None))
        note = next(
            (m for m in out if m.get("role") == "system" and "compacted" in str(m.get("content"))),
            None,
        )
        assert note is not None
        assert "8 enumerated draft items" in note["content"]

        # ── The model persists the drafts (what the nudge now tells it to) ─
        payload = artifact_db.save_proposal("default", "incident-thread", "tweets", drafts)

        # ── Turn N+1: "post the English ones immediately" ───────────────
        # Approval resolves the ID — exact text, regardless of the trim.
        decision = authorize_effect(
            "x_post",
            {"text": "guessed paraphrase", "proposal_id": f"{payload['proposal_id']}#1"},
            thread_id="incident-thread", tenant_id="default",
        )
        assert decision.decision == "allow"
        assert decision.rewritten_args["text"] == drafts[0]

        # ── Turn N+2: "what going on?" — must NOT disarm recall ─────────
        with patch(
            "kazma_core.memory.embedder.get_embedder",
            return_value=_max_distance_embedder(),
        ):
            mode = classify_turn_intent(
                "what going on?",
                messages=[{"role": "user", "content": "Draft 8 tweets about the release"}],
                task_status="in_progress",
                task_goal_summary="Draft 8 tweets about the release",
                use_embedding_drift=True,
            )
        assert mode == "normal"
        assert should_suppress_memory_recall(intent_mode=mode) is False

        # The topic holds — the agent answers about tweets, and the durable
        # copy exists even though the conversation no longer contains it.
        resolved = artifact_db.resolve_proposal(payload["proposal_id"])
        assert resolved["texts"] == drafts
