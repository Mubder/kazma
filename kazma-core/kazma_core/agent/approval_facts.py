"""Approval facts from the tool worker's decisions, never from tool prose."""
from __future__ import annotations

import re
from typing import Any


def approval_scope_note(
    tools: list[dict[str, Any]], results: list[dict[str, Any]], *,
    approved: bool, approved_ids: set[str] | None, mode: str,
) -> dict[str, str]:
    """State authorization and result status separately, including denial.

    No arguments or result content enter this trusted note. Untrusted tool
    names are reduced to identifier characters; they cannot inject a prompt.
    """
    outcomes = {str(result.get("tool_call_id") or ""): result for result in results}
    selected = [tool for tool in tools if approved and (
        approved_ids is None or str(tool.get("id") or "") in approved_ids
    )]
    if mode == "human":
        decision = "SINGLE human approval received" if approved else "Human approval denied"
        intro = f"One approval card covered {len(tools)} danger tools. {decision}."
    elif mode == "yolo":
        intro = "Session YOLO authorized this batch; no new human approval card was required."
    else:
        intro = "Policy denied this batch; no human approval was received."
    lines = [
        "APPROVAL SCOPE (runtime decision facts): " + intro,
        f"Authorized {len(selected)} of {len(tools)} requested actions.",
    ]
    for tool in tools:
        identifier = str(tool.get("id") or "")
        name = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(tool.get("name") or "tool"))[:120]
        allowed = approved and (approved_ids is None or identifier in approved_ids)
        result = outcomes.get(identifier)
        status = ("denied; not executed" if not allowed else
                  "authorized; outcome unavailable" if result is None else
                  "authorized; tool returned an error" if result.get("is_error") else
                  "authorized; tool reported success")
        lines.append(f"- {name}: {status}.")
    lines.append("Report these decisions accurately. Approval does not prove success. "
                 "Do not claim they were approved individually or that no card appeared "
                 "when this record says one did.")
    return {"role": "system", "content": "\n".join(lines)}
