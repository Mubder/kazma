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
from kazma_core.summarizer import _normalize_msg


__all__ = ["CompactionEngine"]

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
        messages = [_normalize_msg(m) for m in state.get("messages", [])]
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
        # This ensures conversation facts survive context window compaction.
        # Audit AC4: the summary is LLM-generated over untrusted conversation
        # content, so it MUST be run through filter_injection before it lands
        # in memory — otherwise attacker text bypasses the consolidator's
        # sanitization and is retrieved + re-injected on future turns.
        try:
            import time
            safe_summary = summary
            try:
                from kazma_core.safety.prompt_fence import is_override_delta

                if is_override_delta(summary):
                    logger.warning(
                        "[Compaction] summary matched an injection marker — "
                        "NOT auto-storing to memory (would poison future prompts)"
                    )
                    safe_summary = None
            except Exception:
                pass
            if safe_summary is not None:
                # V2-native: store the summary as a V2 episode. The
                # is_override_delta guard above MUST run first (audit
                # AC4 — unsanitized summaries poison future prompts).
                from kazma_core.memory.swarm_bridge import store_compaction_summary

                store_compaction_summary(
                    safe_summary,
                    metadata={"type": "compaction_summary", "ts": time.time(), "source": "compaction"},
                )
                logger.debug("Auto-stored compaction summary to V2 memory")
        except Exception:
            logger.debug("Auto-store failed (non-fatal)", exc_info=True)

        # Step 3: Retrieve relevant memories based on the summary
        memories = await self.retrieve_memories(summary, limit=5)

        # Step 4: Build fresh messages list with summary and memories as system context
        system_content = self._build_compacted_system(summary, memories)

        compacted_messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

        # Preserve the in-flight user question. Compaction fires mid-turn
        # (80% threshold or /compact), so dropping the latest user message
        # would leave the agent with nothing to answer and lose the request.
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                compacted_messages.append({"role": "user", "content": msg.get("content", "")})
                break

        # Step 5: Build and return new state.
        # Audit AC4: preserve the most recent tool_results instead of wiping to
        # {}. Dropping them mid-ReAct caused the agent to lose tool context,
        # re-issue the same calls, climb tokens, and re-compact in a loop. We
        # keep the last _MAX_PRESERVED_TOOL_RESULTS entries (bounded so the
        # fresh context stays small).
        _MAX_PRESERVED_TOOL_RESULTS = 8
        prev_tool_results = state.get("tool_results", {}) or {}
        if isinstance(prev_tool_results, dict) and len(prev_tool_results) > _MAX_PRESERVED_TOOL_RESULTS:
            # dict is insertion-ordered; keep the most recent entries.
            preserved_tool_results = dict(
                list(prev_tool_results.items())[-_MAX_PRESERVED_TOOL_RESULTS:]
            )
        else:
            preserved_tool_results = dict(prev_tool_results)

        new_state: AgentState = {
            "messages": compacted_messages,
            "tool_results": preserved_tool_results,
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
        messages = [_normalize_msg(m) for m in messages]
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
        """Retrieve relevant memories via V2 recall (the single read path).

        Args:
            query: Search query (typically the conversation summary).
            limit: Maximum number of memories to retrieve.

        Returns:
            List of memory dicts (id/content/text/score/source_layer/metadata),
            or empty list if V2 recall fails or finds nothing.
        """
        try:
            from kazma_core.memory.recall import search as v2_search
            from kazma_core.memory.config import resolve_tenant_id

            _tenant = resolve_tenant_id(prefer_context=True)
            hits = v2_search(query, limit=limit, tenant_id=_tenant)
            logger.info("Retrieved %d V2 memories for compaction", len(hits))
            return hits
        except Exception:
            logger.debug("[Compaction] V2 recall failed", exc_info=True)
            return []

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
            # Memory content is untrusted (it originates from past conversation /
            # tool output). It is being injected into the SYSTEM prompt — the
            # highest-trust slot — so it must be fenced and override-laden
            # entries dropped, mirroring graph_builder._format_retrieved_memories.
            try:
                from kazma_core.safety.prompt_fence import (
                    format_untrusted_block,
                    is_override_delta,
                )
            except Exception:  # safety module unavailable → degrade to no filter
                format_untrusted_block = None  # type: ignore[assignment]
                is_override_delta = None  # type: ignore[assignment]

            lines: list[str] = []
            for memory in memories:
                content = str(memory.get("content", memory.get("text", ""))).strip()
                if not content:
                    continue
                if is_override_delta is not None and is_override_delta(content):
                    logger.warning(
                        "[Compaction] dropped override-laden memory from system prompt"
                    )
                    continue
                if len(content) > 300:  # compaction runs under token pressure
                    content = content[:300] + "…"
                lines.append(f"- {content}")
            if lines:
                body = "## Relevant Memories\n" + "\n".join(lines)
                parts.append("")
                parts.append(
                    format_untrusted_block(body, source="memory_compaction")
                    if format_untrusted_block
                    else body
                )

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
