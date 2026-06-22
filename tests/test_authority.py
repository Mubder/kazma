"""Tests for ContextAuthority — the 80% compaction enforcement loop."""

from __future__ import annotations

import pytest
from kazma_core.authority import ContextAuthority, create_authority
from kazma_core.compaction import CompactionEngine
from kazma_core.state import initial_state
from kazma_core.token_counter import TokenCounter


class TestContextAuthority:
    """Tests for ContextAuthority class."""

    def test_init_wires_counter_and_compactor(self) -> None:
        """ContextAuthority should store counter and compactor."""
        counter = TokenCounter(model="gpt-4", window=1000)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)
        assert authority.counter is counter
        assert authority.compactor is compactor

    def test_threshold_property(self) -> None:
        """threshold property should expose the 80% value."""
        counter = TokenCounter(model="gpt-4", window=1000)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)
        assert authority.threshold == 800

    @pytest.mark.asyncio
    async def test_check_and_enforce_no_compaction_needed(self) -> None:
        """State should pass through unchanged when below threshold."""
        counter = TokenCounter(model="gpt-4", window=10000)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)

        state = initial_state()
        state["messages"] = [{"role": "user", "content": "hi"}]

        result = await authority.check_and_enforce(state)
        assert result is state  # same object, no compaction

    @pytest.mark.asyncio
    async def test_check_and_enforce_triggers_compaction(self) -> None:
        """State should be compacted when threshold exceeded."""
        counter = TokenCounter(model="gpt-4", window=100)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)

        # Create state that exceeds 80% of 100 tokens
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 400}]

        result = await authority.check_and_enforce(state)

        # Should return a different state (compacted)
        assert result is not state
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_check_and_enforce_exact_boundary(self) -> None:
        """Compaction should trigger at exactly the 80% boundary."""
        counter = TokenCounter(model="gpt-4", window=100)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)

        # Exactly at 80%: 80 tokens needed
        # With heuristic: 4 overhead + (len+3)//4 = 80 -> len = 301
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 301}]

        # Verify we're exactly at threshold
        assert counter.should_compact(state["messages"]) is True

        result = await authority.check_and_enforce(state)
        assert result is not state  # should have compacted

    @pytest.mark.asyncio
    async def test_check_and_enforce_with_llm_client(self) -> None:
        """Compaction with LLM should use LLM for summarization."""
        class MockLLM:
            async def chat(self, messages: list[dict]) -> str:
                return "[CONTEXT SUMMARY] LLM summary [/CONTEXT SUMMARY]"

        counter = TokenCounter(model="gpt-4", window=100)
        compactor = CompactionEngine(llm_client=MockLLM())
        authority = ContextAuthority(counter=counter, compactor=compactor)

        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 400}]

        result = await authority.check_and_enforce(state)

        assert "LLM summary" in result["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_check_and_enforce_preserves_thread_id(self) -> None:
        """Compaction should preserve the thread_id from provenance."""
        counter = TokenCounter(model="gpt-4", window=100)
        compactor = CompactionEngine()
        authority = ContextAuthority(counter=counter, compactor=compactor)

        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 400}]
        original_thread = state["provenance"]["thread_id"]

        result = await authority.check_and_enforce(state)

        assert result["provenance"]["thread_id"] == original_thread


class TestCreateAuthority:
    """Tests for the create_authority factory function."""

    def test_create_authority_returns_context_authority(self) -> None:
        """Factory should return a ContextAuthority instance."""
        authority = create_authority()
        assert isinstance(authority, ContextAuthority)

    def test_create_authority_default_params(self) -> None:
        """Factory should use default model and window."""
        authority = create_authority()
        assert authority.counter.model == "gpt-4"
        assert authority.counter.window == 128000
        assert authority.threshold == 102400

    def test_create_authority_custom_params(self) -> None:
        """Factory should accept custom model and window."""
        authority = create_authority(model="claude-3", window=200000)
        assert authority.counter.model == "claude-3"
        assert authority.counter.window == 200000
        assert authority.threshold == 160000

    def test_create_authority_with_llm_client(self) -> None:
        """Factory should wire up LLM client."""
        class MockLLM:
            pass

        llm = MockLLM()
        authority = create_authority(llm_client=llm)
        assert authority.compactor.llm_client is llm

    def test_create_authority_with_checkpoint_manager(self) -> None:
        """Factory should wire up checkpoint manager."""
        class MockCP:
            pass

        cp = MockCP()
        authority = create_authority(checkpoint_manager=cp)
        assert authority.compactor.checkpoint_manager is cp

    def test_create_authority_with_memory_store(self) -> None:
        """Factory should wire up memory store."""
        class MockMem:
            pass

        mem = MockMem()
        authority = create_authority(memory_store=mem)
        assert authority.compactor.memory_store is mem


class TestAuthorityIntegration:
    """Integration tests: full authority loop with all components."""

    @pytest.mark.asyncio
    async def test_full_loop_no_compaction(self) -> None:
        """Full loop: below threshold, no compaction, state unchanged."""
        authority = create_authority(model="gpt-4", window=10000)
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "hello"}]

        result = await authority.check_and_enforce(state)
        assert result is state

    @pytest.mark.asyncio
    async def test_full_loop_with_compaction(self) -> None:
        """Full loop: above threshold, compaction occurs, fresh state returned."""
        authority = create_authority(model="gpt-4", window=100)
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "x" * 400}]

        result = await authority.check_and_enforce(state)
        assert result is not state
        assert "CONTEXT SUMMARY" in result["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_repeated_checks_stay_compacted(self) -> None:
        """After compaction, the new state should be below threshold."""
        # Use a large window so the summary fits comfortably
        authority = create_authority(model="gpt-4", window=128000)
        state = initial_state()
        state["messages"] = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]

        # Force compaction by pre-setting context_tokens high
        state["context_tokens"] = 102400  # above 80% of 128k
        # Manually trigger compaction via the compactor
        result = await authority.compactor.compact(state)

        # After compaction, the summary should be well below threshold
        assert authority.counter.should_compact(result["messages"]) is False
