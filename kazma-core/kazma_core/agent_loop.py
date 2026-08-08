"""Agent Loop with Stream Truncation & Auto-Continuation Catch (Task 5).

Monitors LLM completion finish_reason. If truncated due to max_tokens (finish_reason == "length"),
automatically appends a continuation prompt and stitches the output streams together.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["execute_agent_turn_with_autocontinue"]


async def execute_agent_turn_with_autocontinue(
    messages: list[dict[str, Any]],
    llm_client: Any = None,
    max_retries: int = 3,
) -> str:
    """Monitors completion finish_reason.

    If truncated due to max_tokens (finish_reason == "length"), automatically
    appends a continuation prompt and stitches the output streams together.
    """
    full_response = ""
    current_messages = list(messages)

    if llm_client is None:
        try:
            from kazma_core.model_registry import get_client

            llm_client = get_client()
        except Exception as exc:
            logger.debug("[agent_loop] get_client fallback: %s", exc)

    for _ in range(max_retries):
        if llm_client is None:
            break

        try:
            if hasattr(llm_client, "chat"):
                response = await llm_client.chat(messages=current_messages)
                content = getattr(response, "content", "") or ""
                finish_reason = getattr(response, "finish_reason", "stop") or "stop"
            elif hasattr(llm_client, "generate"):
                response = await llm_client.generate(messages=current_messages)
                choice = response.choices[0]
                content = choice.message.content or ""
                finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
            else:
                break
        except Exception as exc:
            logger.warning("[agent_loop] auto-continue step error: %s", exc)
            break

        full_response += content

        if finish_reason == "length":
            logger.info(
                "[agent_loop] Output truncated by max_tokens (finish_reason=length). Auto-continuing..."
            )
            current_messages.append({"role": "assistant", "content": content})
            current_messages.append(
                {
                    "role": "user",
                    "content": "Continue exactly from where you stopped. Do not repeat previous text.",
                }
            )
        else:
            break

    return full_response
