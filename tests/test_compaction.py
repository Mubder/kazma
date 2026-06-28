"""Tests for TokenCounter and CompactionEngine."""

from __future__ import annotations

import pytest
from kazma_core.compaction import CompactionEngine
from kazma_core.state import AgentState, initial_state
from kazma_core.token_counter import TokenCounter


class TestTokenCounter:
    """Tests for TokenCounter class."""

    def test_init_sets_threshold_at_80_percent(self) -> None:
        """Threshold should be exactly 80% of window."""
        tc = TokenCounter(model="gpt-4", window=1000)
        assert tc.threshold == 800

    def test_init_default_window(self) -> None:
        """Default window should be 128000."""
        tc = TokenCounter(model="gpt-4")
        assert tc.window == 128000
        assert tc.threshold == 102400

    def test_count_empty_messages(self) -> None:
        """Empty message list should return 0 tokens."""
        tc = TokenCounter(model="gpt-4", window=1000)
        assert tc.count([]) == 0

    def test_count_single_message(self) -> None:
        """Single message should count tokens with overhead."""
        tc = TokenCounter(model="gpt-4", window=10000)
        messages = [{"role": "user", "content": "hello world"}]
        count = tc.count(messages)
        # 4 overhead + heuristic: "hello world" = 11 chars -> (11+3)//4 = 3
        # but if tiktoken is available, count may differ
        assert count >= 4  # at least the overhead

    def test_count_multiple_messages(self) -> None:
        """Multiple messages should sum up correctly."""
        tc = TokenCounter(model="gpt-4", window=10000)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        count = tc.count(messages)
        # 2 messages * 4 overhead = 8 minimum
        assert count >= 8

    def test_count_with_long_content(self) -> None:
        """Long content should produce proportional token counts."""
        tc = TokenCounter(model="gpt-4", window=10000)
        # 1000 chars should produce roughly 250 tokens (heuristic) or similar (tiktoken)
        content = "a" * 1000
        messages = [{"role": "user", "content": content}]
        count = tc.count(messages)
        assert count > 100  # significantly more than just overhead

    def test_should_compact_below_threshold(self) -> None:
        """should_compact should return False below threshold."""
        tc = TokenCounter(model="gpt-4", window=1000)
        # 1 message with short content won't hit 800 token threshold
        messages = [{"role": "user", "content": "hi"}]
        assert tc.should_compact(messages) is False

    def test_should_compact_at_threshold(self) -> None:
        """should_compact should return True at or above threshold."""
        tc = TokenCounter(model="gpt-4", window=100)
        # 80 tokens threshold = 80% of 100
        # Create enough content to exceed it
        # Need ~76 chars of content + 4 overhead = ~80 tokens (heuristic)
        content = "x" * 400  # 400/4 = 100 tokens + 4 overhead = 104
        messages = [{"role": "user", "content": content}]
        assert tc.should_compact(messages) is True

    def test_should_compact_exactly_at_boundary(self) -> None:
        """Test behavior right at the 80% boundary."""
        tc = TokenCounter(model="gpt-4", window=100)
        # Exactly 80 tokens needed. With 4 overhead, need 76 chars -> (76+3)//4 = 19
        # Actually with heuristic: chars = (tokens_needed - 4) * 4
        # To get exactly 80 tokens: need content that produces 76 tokens
        # Heuristic: (len + 3) // 4 = 76 -> len in [300, 303]
        content = "x" * 301
        messages = [{"role": "user", "content": content}]
        count = tc.count(messages)
        # Heuristic: 4 + (301+3)//4 = 4 + 76 = 80
        assert count == 80
        assert tc.should_compact(messages) is True

    def test_count_multimodal_content(self) -> None:
        """Count should handle multimodal content arrays."""
        tc = TokenCounter(model="gpt-4", window=10000)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello world"},
                    {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                ],
            }
        ]
        count = tc.count(messages)
        # 4 overhead + text part tokens
        assert count >= 4

    def test_threshold_is_hardcoded(self) -> None:
        """Threshold should always be 80% regardless of model."""
        for window in [1000, 4096, 8192, 32768, 128000]:
            tc = TokenCounter(model="any-model", window=window)
            expected = int(window * 0.8)
            assert tc.threshold == expected


class TestCompactionEngine:
    """Tests for CompactionEngine class."""

    @pytest.mark.asyncio
    async def test_compact_returns_fresh_state(self) -> None:
        """compact() should return a new AgentState with system message."""
        engine = CompactionEngine()
        state = initial_state()
        state["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        new_state = await engine.compact(state)

        assert "messages" in new_state
        assert len(new_state["messages"]) == 1
        assert new_state["messages"][0]["role"] == "system"
        assert "CONTEXT SUMMARY" in new_state["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_compact_preserves_provenance(self) -> None:
        """compact() should preserve provenance from original state."""
        engine = CompactionEngine()
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]

        new_state = await engine.compact(state)

        assert new_state.get("provenance") == state.get("provenance")
        assert new_state.get("last_cp_id") == state.get("last_cp_id")

    @pytest.mark.asyncio
    async def test_compact_clears_tool_results(self) -> None:
        """compact() should clear tool_results since they're in the summary."""
        engine = CompactionEngine()
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]
        state["tool_results"] = {"tool1": "result1"}

        new_state = await engine.compact(state)

        assert new_state["tool_results"] == {}

    @pytest.mark.asyncio
    async def test_compact_saves_checkpoint(self) -> None:
        """compact() should save checkpoint before compacting if manager available."""
        saved_states = []

        class MockCheckpointManager:
            async def save(self, state: AgentState) -> str:
                saved_states.append(state)
                return "test-cp-id"

        engine = CompactionEngine(checkpoint_manager=MockCheckpointManager())
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]

        await engine.compact(state)

        assert len(saved_states) == 1
        assert saved_states[0] is state

    @pytest.mark.asyncio
    async def test_compact_with_llm_client(self) -> None:
        """compact() should use LLM client for summarization when available."""

        class MockLLM:
            async def chat(self, messages: list[dict]) -> str:
                return "[CONTEXT SUMMARY] LLM summary of the conversation. [/CONTEXT SUMMARY]"

        engine = CompactionEngine(llm_client=MockLLM())
        state = initial_state()
        state["messages"] = [
            {"role": "user", "content": "help me with python"},
            {"role": "assistant", "content": "sure, what do you need?"},
        ]

        new_state = await engine.compact(state)

        assert "LLM summary" in new_state["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_compact_with_memory_store(self) -> None:
        """compact() should retrieve memories when store is available."""

        class MockMemoryStore:
            async def search(self, query: str, limit: int = 5) -> list[dict]:
                return [
                    {"content": "User prefers dark mode"},
                    {"content": "Project uses Python 3.11"},
                ]

        engine = CompactionEngine(memory_store=MockMemoryStore())
        state = initial_state()
        state["messages"] = [{"role": "user", "content": "test"}]

        new_state = await engine.compact(state)

        system_content = new_state["messages"][0]["content"]
        assert "Relevant Memories" in system_content
        assert "dark mode" in system_content
        assert "Python 3.11" in system_content

    @pytest.mark.asyncio
    async def test_summarize_empty_messages(self) -> None:
        """summarize() should handle empty message list."""
        engine = CompactionEngine()
        summary = await engine.summarize([])
        assert "CONTEXT SUMMARY" in summary

    @pytest.mark.asyncio
    async def test_summarize_heuristic_includes_message_count(self) -> None:
        """Heuristic summary should include message count."""
        engine = CompactionEngine()
        messages = [{"role": "user", "content": f"message {i}"} for i in range(10)]
        summary = await engine.summarize(messages)
        assert "10 messages" in summary

    @pytest.mark.asyncio
    async def test_retrieve_memories_empty_when_no_store(self) -> None:
        """retrieve_memories() should return empty list without store."""
        engine = CompactionEngine()
        memories = await engine.retrieve_memories("test query")
        assert memories == []

    @pytest.mark.asyncio
    async def test_retrieve_memories_with_store(self) -> None:
        """retrieve_memories() should delegate to memory store."""

        class MockMemoryStore:
            async def search(self, query: str, limit: int = 5) -> list[dict]:
                return [{"content": "memory 1"}, {"content": "memory 2"}]

        engine = CompactionEngine(memory_store=MockMemoryStore())
        memories = await engine.retrieve_memories("test query", limit=2)
        assert len(memories) == 2
