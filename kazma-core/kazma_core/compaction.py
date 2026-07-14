"""Kazma Context Compaction Engine — Summarizes conversation history to fit within token limits.

Compacts the agent's conversation context by:
1. Saving the current state to a checkpoint
2. Summarizing the conversation using an LLM (or heuristic fallback)
3. Retrieving relevant memories from the memory store
4. Building a fresh state with the summary and memories
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_core.state import AgentState

logger = logging.getLogger(__name__)

# Summary prompt that instructs the LLM to preserve critical information
_SUMMARY_SYSTEM = """You are a conversation summarizer for an AI agent. Summarize the following conversation history into a concise summary under 2000 tokens.

Your summary MUST preserve:
1. **Task Goal**: What the user is trying to accomplish
2. **Key Decisions**: Important choices made during the conversation
3. **Tool Results**: Critical output from tool calls that may be referenced later
4. **User Constraints**: Any limitations, preferences, or requirements the user specified

Format your summary as a structured text block. Be precise and factual — do not invent information not present in the conversation.

Keep the summary under 2000 tokens. Start with "[CONTEXT SUMMARY]" and end with "[/CONTEXT SUMMARY]"."""


class CompactionEngine:
    """Compacts conversation context by summarizing when approaching token limits.

    The engine:
    - Saves the current state to a checkpoint before compacting
    - Uses an LLM to create a structured summary preserving key facts
    - Retrieves relevant memories to enrich the compacted context
    - Returns a fresh AgentState with the summary and memories as system context
    """

    def __init__(
        self,
        llm_client: Any = None,
        checkpoint_manager: Any = None,
        memory_store: Any = None,
    ) -> None:
        """Initialize the CompactionEngine.

        Args:
            llm_client: Object with async chat(messages: list[dict]) -> str method.
                If None, a simple heuristic summary is used.
            checkpoint_manager: CheckpointManager instance for saving state snapshots.
                If None, checkpointing is skipped.
            memory_store: Object with async search(query: str, limit: int = 5) -> list[dict]
                for retrieving relevant memories. If None, memory retrieval is skipped.
        """
        self.llm_client = llm_client
        self.checkpoint_manager = checkpoint_manager
        self.memory_store = memory_store

    async def compact(self, state: AgentState) -> AgentState:
        """Compact the conversation context to free up token space.

        Steps:
        1. Save current state to checkpoint (if checkpoint_manager available)
        2. Summarize the conversation
        3. Retrieve top-5 relevant memories
        4. Build a fresh state with summary + memories as system context
        5. Return the new state

        Args:
            state: The current agent state to compact.

        Returns:
            A new AgentState with compacted context.
        """
        messages = state.get("messages", [])
        logger.info(
            "Compacting context with %d messages (%d tokens)",
            len(messages),
            state.get("context_tokens", 0),
        )

        # Step 1: Save checkpoint before compacting
        if self.checkpoint_manager is not None:
            try:
                cp_id = await self.checkpoint_manager.save(state)
                logger.info("Saved checkpoint %s before compaction", cp_id)
            except Exception:
                logger.exception("Failed to save checkpoint before compaction, continuing anyway")  # non-fatal

        # Step 2: Summarize the conversation
        summary = await self.summarize(messages)

        # Step 2.5: Auto-store the summary in memory for long-term retention
        # This ensures conversation facts survive context window compaction
        if self.memory_store is not None:
            try:
                import time
                await self.memory_store.store(
                    summary,
                    metadata={"type": "compaction_summary", "ts": time.time(), "source": "compaction"}
                )
                logger.debug("Auto-stored compaction summary to memory")
            except Exception:
                logger.debug("Auto-store failed (non-fatal)", exc_info=True)

        # Step 3: Retrieve relevant memories based on the summary
        memories = await self.retrieve_memories(summary, limit=5)

        # Step 4: Build fresh messages list with summary and memories as system context
        system_content = self._build_compacted_system(summary, memories)

        compacted_messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

        # Step 5: Build and return new state
        new_state: AgentState = {
            "messages": compacted_messages,
            "tool_results": {},
            "context_tokens": 0,
            "last_cp_id": state.get("last_cp_id", ""),
            "created_at": state.get("created_at", ""),
            "provenance": state.get("provenance", {}),
        }

        logger.info(
            "Compaction complete: %d messages -> 1 system message (%d chars)",
            len(messages),
            len(system_content),
        )
        return new_state

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        """Create a summary of the conversation that preserves key facts.

        Uses the LLM client if available, otherwise falls back to a simple
        heuristic summary.

        The summary must be under 2000 tokens and preserve:
        - Task goal
        - Key decisions
        - Important tool results
        - User constraints

        Args:
            messages: List of message dicts (role, content, etc.)

        Returns:
            A summary string under 2000 tokens.
        """
        if not messages:
            return "[CONTEXT SUMMARY] No prior conversation history. [/CONTEXT SUMMARY]"

        if self.llm_client is not None:
            return await self._summarize_with_llm(messages)
        return self._summarize_heuristic(messages)

    async def _summarize_with_llm(self, messages: list[dict[str, Any]]) -> str:
        """Use the LLM to create a structured summary."""
        # Format messages for the summarizer
        conversation_text = self._format_messages_for_summary(messages)

        prompt = [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": f"Summarize this conversation:\n\n{conversation_text}"},
        ]

        try:
            summary = await self.llm_client.chat(prompt)
            # Enforce token limit: truncate if LLM ignores constraint
            if len(summary) > 8000:  # ~2000 tokens rough chars estimate
                summary = summary[:8000]
                logger.warning("LLM summary truncated to 8000 chars to enforce token limit")
            return summary
        except Exception:
            logger.exception("LLM summarization failed, falling back to heuristic")
            return self._summarize_heuristic(messages)

    def _summarize_heuristic(self, messages: list[dict[str, Any]]) -> str:
        """Create a simple heuristic summary without an LLM."""
        message_count = len(messages)

        # Extract the last user message as context
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Handle structured content (e.g., multimodal)
                    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    content = " ".join(parts)
                last_user_msg = content[:500]  # Cap individual message length
                break

        # Collect any tool results mentioned
        tool_results_summary = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str):
                    tool_results_summary.append(content[:200])

        parts = [
            "[CONTEXT SUMMARY]",
            f"Conversation history with {message_count} messages.",
        ]

        if last_user_msg:
            parts.append(f"Last user request: {last_user_msg}")

        if tool_results_summary:
            recent_tools = tool_results_summary[-5:]  # Last 5 tool results
            parts.append(f"Recent tool outputs: {'; '.join(recent_tools)}")

        parts.append("[/CONTEXT SUMMARY]")

        summary = "\n".join(parts)
        logger.info("Using heuristic summary (%d chars) for %d messages", len(summary), message_count)
        return summary

    async def retrieve_memories(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve relevant memories from the memory store.

        Handles both async (``await search(query, limit=)``) and sync
        (``search(query, n_results=)``) backends gracefully.  This makes the
        compaction engine compatible with ``AsyncMemoryAdapter`` (the canonical
        wrapper), ``UnifiedMemoryAdapter.search_dict()``, raw ``VectorMemory`` (sync),
        and any future async backend.

        Args:
            query: Search query (typically the conversation summary).
            limit: Maximum number of memories to retrieve.

        Returns:
            List of memory dicts, or empty list if no memory store is configured
            or retrieval fails.
        """
        if self.memory_store is None:
            # Lazy resolution: if no store was passed at construction time
            # (e.g. because VectorMemory was set AFTER the agent was built),
            # try to resolve it now from the singleton. This fixes the
            # initialization-ordering issue where app.py constructs KazmaAgent
            # before calling set_vector_memory().
            store = self._resolve_memory_store()
            if store is None:
                return []
        else:
            store = self.memory_store

        try:
            result = store.search(query, limit=limit)
            # If the backend is async (e.g. AsyncMemoryAdapter or UnifiedMemoryAdapter), await it.
            if asyncio.iscoroutine(result):
                memories = await result
            else:
                memories = result
            logger.info("Retrieved %d memories for compaction", len(memories))
            return memories
        except TypeError:
            # Fallback: the backend may use n_results= instead of limit=.
            try:
                result = store.search(query, n_results=limit)
                if asyncio.iscoroutine(result):
                    memories = await result
                else:
                    memories = result
                logger.info("Retrieved %d memories for compaction (n_results fallback)", len(memories))
                return memories
            except Exception:
                logger.exception("Memory retrieval failed during compaction (n_results fallback)")
                return []
        except Exception:
            logger.exception("Memory retrieval failed during compaction")
            return []

    def _resolve_memory_store(self) -> Any:
        """Lazily resolve the memory store from the VectorMemory singleton.

        Called when ``self.memory_store`` is None. Wraps the singleton in an
        ``AsyncMemoryAdapter`` so the compaction engine can ``await`` it.
        """
        try:
            from kazma_core.agent.tool_registry import get_vector_memory
            from kazma_core.memory.async_adapter import wrap_vector_memory

            vm = get_vector_memory()
            if vm is not None:
                logger.debug("CompactionEngine: lazily resolved VectorMemory singleton")
                store = wrap_vector_memory(vm)
                # Cache it so we don't re-resolve on every compaction.
                self.memory_store = store
                return store
        except Exception:
            logger.debug("CompactionEngine: could not lazily resolve memory store", exc_info=True)
        return None

    def _build_compacted_system(self, summary: str, memories: list[dict[str, Any]]) -> str:
        """Build the system message content for the compacted context.

        Args:
            summary: The conversation summary.
            memories: List of relevant memory dicts.

        Returns:
            A system message string containing the summary and memories.
        """
        parts = [
            "You are an AI agent in a compacted context. The conversation history has been",
            "summarized to stay within token limits. Use this summary to maintain continuity.",
            "",
            "## Conversation Summary",
            summary,
        ]

        if memories:
            parts.append("")
            parts.append("## Relevant Memories")
            for i, memory in enumerate(memories, 1):
                content = memory.get("content", memory.get("text", str(memory)))
                parts.append(f"{i}. {content}")

        parts.append("")
        parts.append("Continue assisting the user based on this context.")

        return "\n".join(parts)

    def _format_messages_for_summary(self, messages: list[dict[str, Any]]) -> str:
        """Format messages into a readable string for the summarizer prompt.

        Args:
            messages: List of message dicts.

        Returns:
            Formatted string representation of the messages.
        """
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle structured content (multimodal)
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(parts)

            # Cap each message to avoid exceeding LLM context
            if isinstance(content, str) and len(content) > 2000:
                content = content[:2000] + "..."

            lines.append(f"[{role}]: {content}")

        return "\n".join(lines)
