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


class TurnResult:
    """Helper result wrapper for agent turns."""

    def __init__(
        self,
        text_output: str,
        is_complete: bool = False,
        has_generated_artifacts: bool = False,
    ):
        self.text_output = text_output
        self.is_complete = is_complete
        self.has_generated_artifacts = has_generated_artifacts


async def run_agent_workflow_to_completion(
    session_id: str,
    user_prompt: str,
    max_auto_continues: int = 3,
    turn_executor: Any = None,
) -> str:
    """Executes agent turns autonomously.

    If a partial tool yield occurs (such as "جزئي" or "لم يُكتب الملف"),
    it automatically resumes without interrupting the user or prompting for confirmation.
    """
    current_turn = 0
    accumulated_response = ""

    while current_turn < max_auto_continues:
        current_turn += 1
        if turn_executor is not None:
            turn_result = await turn_executor(session_id, user_prompt if current_turn == 1 else "CONTINUE_JOB")
        else:
            # Native fallback turn execution
            resp_text = await execute_agent_turn_with_autocontinue([{"role": "user", "content": user_prompt}])
            is_done = "الحالة: مكتمل" in resp_text or "SR-2026-" in resp_text or ".pdf" in resp_text
            turn_result = TurnResult(
                text_output=resp_text,
                is_complete=is_done,
                has_generated_artifacts=is_done,
            )

        accumulated_response += turn_result.text_output

        # Stop if execution is complete or target artifact generated
        if turn_result.is_complete or turn_result.has_generated_artifacts:
            break

        # Catch partial stalls and auto-resume execution
        if "جزئي" in turn_result.text_output or "لم يُكتب الملف" in turn_result.text_output:
            logger.info(f"Auto-resuming partial job for session {session_id} (Turn {current_turn})")
            user_prompt = "أكمل الدمج والتصدير وتوليد الملف النهائي فوراً بدون أي توقف."
            continue

    return accumulated_response

