"""A2A, agent to agent: what Kazma says and accepts (REMAINING 4.5, 2026-11-14).

The Agent2Agent protocol (A2A 0.3, JSON-RPC 2.0 over HTTP) lets another AI
agent find Kazma and ask it something. This module is the protocol's half that
needs nothing of the server: the agent card, reading a ``message/send``
request, and the task and error objects Kazma answers with. The endpoint that
runs the turn is ``kazma_ui/a2a_api.py``.

What another agent may do through it is deliberately small, and the operator's
to switch on (``a2a.enabled``, off by default; Settings -> System):

- it authenticates with one of the operator's API tokens (Settings -> Account
  -> API tokens): an agent holding one acts with the operator's authority;
- each request is one agent turn offered **read tools only** (the scheduled
  task's ``read`` set, ``cron/capabilities.py``): it can search the web, the
  Knowledge Library and memory and read what they hold, never send, write,
  post, delete or run anything, so no turn ever waits for an approval;
- the request reaches the model labelled as another agent's request, not as
  the operator speaking, and nothing of it is written to memory;
- text in and text out (``text/plain``); a conversation (``contextId``) keeps
  its last turns for the next request, in this process only.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

__all__ = [
    "ENABLED_KEY",
    "JSONRPC_ERRORS",
    "PROTOCOL_VERSION",
    "A2AError",
    "agent_card",
    "error_response",
    "read_send_params",
    "task_object",
]

#: The operator's switch (Settings -> System): off, the card and the
#: endpoint answer nothing.
ENABLED_KEY = "a2a.enabled"
PROTOCOL_VERSION = "0.3.0"
#: The longest request text a turn takes.
MAX_TEXT = 20_000

#: JSON-RPC 2.0 and A2A error codes Kazma answers with.
JSONRPC_ERRORS = {
    "parse_error": -32700,
    "invalid_request": -32600,
    "method_not_found": -32601,
    "invalid_params": -32602,
    "internal_error": -32603,
    "task_not_found": -32001,
    "task_not_cancelable": -32002,
    "content_type_not_supported": -32005,
    "busy": -32010,
}


class A2AError(Exception):
    """A request Kazma refuses: ``code`` is a key of :data:`JSONRPC_ERRORS`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def agent_card(*, url: str, version: str) -> dict[str, Any]:
    """Kazma's agent card (``/.well-known/agent-card.json``)."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "Kazma",
        "description": ("A personal AI agent. Ask it a question: it can search the web, its owner's Knowledge "
                        "Library and memory, and answer from what it finds. It only reads: it never sends, "
                        "changes or deletes anything for another agent."),
        "url": url,
        "preferredTransport": "JSONRPC",
        "version": version,
        "capabilities": {"streaming": False, "pushNotifications": False, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "ask",
            "name": "Ask Kazma",
            "description": "Answer a question with web search, the Knowledge Library and memory (read only).",
            "tags": ["research", "search", "knowledge", "memory", "arabic"],
            "examples": ["What did we decide about the kitchen tiles?",
                         "Summarize the latest news on electric cars, with sources."],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        }],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer",
                                       "description": "An API token of the Kazma install's operator."}},
        "security": [{"bearer": []}],
    }


def read_send_params(params: Any) -> tuple[str, str, str | None]:
    """``(text, context_id, task_id)`` from ``message/send``'s params; raises
    :class:`A2AError` for anything that is not a text message from a user."""
    if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
        raise A2AError("invalid_params", "params.message is required")
    message = params["message"]
    if message.get("role") not in (None, "user"):
        raise A2AError("invalid_params", "the message's role must be user")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise A2AError("invalid_params", "the message has no parts")
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise A2AError("invalid_params", "a part is not an object")
        kind = part.get("kind") or part.get("type")
        if kind != "text":
            raise A2AError("content_type_not_supported", "Kazma takes text parts only")
        texts.append(str(part.get("text") or ""))
    text = "\n".join(t for t in texts if t).replace("\x00", "").strip()
    if not text:
        raise A2AError("invalid_params", "the message holds no text")
    if len(text) > MAX_TEXT:
        raise A2AError("invalid_params", f"the message is longer than {MAX_TEXT} characters")
    context = str(message.get("contextId") or params.get("contextId") or "") or str(uuid.uuid4())
    if len(context) > 200:
        raise A2AError("invalid_params", "the contextId is too long")
    task_id = message.get("taskId")
    return text, context, (str(task_id) if task_id else None)


def _message(role: str, text: str, *, context_id: str, task_id: str) -> dict[str, Any]:
    return {"kind": "message", "role": role, "messageId": str(uuid.uuid4()), "contextId": context_id,
            "taskId": task_id, "parts": [{"kind": "text", "text": text}]}


def task_object(*, task_id: str, context_id: str, state: str, request: str, answer: str = "",
                reason: str = "", at: float | None = None) -> dict[str, Any]:
    """An A2A Task: its state (``submitted``, ``working``, ``completed``,
    ``failed``, ``canceled``), the answer as an artifact, the exchange as history."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at if at is not None else time.time()))
    status: dict[str, Any] = {"state": state, "timestamp": stamp}
    if reason:
        status["message"] = _message("agent", reason, context_id=context_id, task_id=task_id)
    history = [_message("user", request, context_id=context_id, task_id=task_id)]
    task: dict[str, Any] = {"kind": "task", "id": task_id, "contextId": context_id, "status": status,
                            "history": history}
    if answer:
        task["artifacts"] = [{"artifactId": f"{task_id}-answer", "name": "answer",
                              "parts": [{"kind": "text", "text": answer}]}]
        history.append(_message("agent", answer, context_id=context_id, task_id=task_id))
    return task


def error_response(request_id: Any, code: str, message: str) -> dict[str, Any]:
    """A JSON-RPC error answer."""
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": JSONRPC_ERRORS.get(code, -32603), "message": message}}
