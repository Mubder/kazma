"""Tests for the CRITICAL chat SSE fix (Issue 3).

Root cause: LLMProvider uses custom httpx calls (not LangChain BaseChatModel),
so graph.astream_events() never emits on_chat_model_stream events.  The
response exists in the graph terminal state messages[-1] but is never
extracted.

Fix: In sse_chat.py _stream_langgraph_events(), in the on_chain_end __end__
handler, extract the final assistant message from output['messages'] and
emit it as a token SSE frame before the done frame.  Also extract error
messages from the supervisor's error path.  Guard with 'if not content_acc'
to prevent duplicates if streaming is ever added.

Fulfils:
  - VAL-CHAT-001: Chat displays assistant response
  - VAL-CHAT-002: Chat displays error message on LLM failure
  - VAL-CHAT-003: Chat response persists in session history
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_ui.sse_chat import _stream_langgraph_events, create_sse_chat_router


@pytest.fixture(autouse=True)
def _reset_shared_session_store():
    """Reset the shared SessionManager singleton before each test."""
    from kazma_ui.session_manager import reset_session_manager

    reset_session_manager()


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _mock_astream_events(events: list[dict[str, Any]]):
    """Create a mock astream_events that yields the given events."""

    async def _gen(*args, **kwargs):
        for event in events:
            yield event

    return _gen()


def _collect_frames(graph, input_state, config):
    """Run _stream_langgraph_events and collect all frames synchronously."""
    frames: list[str] = []

    async def _collect():
        async for frame in _stream_langgraph_events(graph, input_state, config):
            frames.append(frame)

    asyncio.run(_collect())
    return frames


def _parse_frame(frame: str) -> tuple[str, dict[str, Any]]:
    """Parse an SSE frame into (event, data_dict)."""
    event_line = frame.split("event: ", 1)[1].split("\n", 1)[0]
    data_line = frame.split("data: ", 1)[1].split("\n\n", 1)[0]
    return event_line, json.loads(data_line)


# ═══════════════════════════════════════════════════════════════════
# VAL-CHAT-001: Final assistant message extracted from terminal state
# ═══════════════════════════════════════════════════════════════════


class TestFinalMessageExtraction:
    """When the graph completes with no token events, the final assistant
    message must be extracted from the terminal state and emitted as a
    token frame."""

    def test_final_message_extracted_from_state(self):
        """VAL-CHAT-001: assistant response in messages[-1] is emitted
        as a token frame when no on_chat_model_stream events fired."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 50,
                        "last_cost_usd": 0.002,
                        "messages": [
                            {"role": "user", "content": "hello"},
                            {
                                "role": "assistant",
                                "content": "Hi there! How can I help you?",
                            },
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        done_frames = [f for f in frames if f.startswith("event: done")]

        # Must have exactly one token frame with the assistant content
        assert len(token_frames) == 1, f"Expected 1 token frame, got {len(token_frames)}"
        evt, data = _parse_frame(token_frames[0])
        assert data["content"] == "Hi there! How can I help you?"

        # Done frame must follow
        assert len(done_frames) == 1

    def test_no_duplicate_token_when_streaming_active(self):
        """Guard: if content_acc already has content from
        on_chat_model_stream events, do NOT emit the final message again."""
        class FakeChunk:
            def __init__(self, text):
                self.content = text

        events = [
            {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk("streamed")}, "name": "llm"},
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 10,
                        "last_cost_usd": 0.001,
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {
                                "role": "assistant",
                                "content": "streamed response",
                            },
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        # Should have only the 1 streaming token, NOT a duplicate from state
        assert len(token_frames) == 1
        _, data = _parse_frame(token_frames[0])
        assert data["content"] == "streamed"

    def test_no_messages_key_does_not_crash(self):
        """If the terminal state has no 'messages' key, we must not crash."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 5,
                        "last_cost_usd": 0.0001,
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        # No token frame, but done frame should still be there
        token_frames = [f for f in frames if f.startswith("event: token")]
        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(token_frames) == 0
        assert len(done_frames) == 1

    def test_empty_messages_list_does_not_crash(self):
        """If messages list is empty, we must not crash."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(token_frames) == 0
        assert len(done_frames) == 1

    def test_last_message_is_user_role_no_token_emitted(self):
        """If the last message is from the user (no assistant response),
        do not emit a token frame."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hello"},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        assert len(token_frames) == 0

    def test_token_frame_comes_before_done_frame(self):
        """The token frame must be emitted BEFORE the done frame so the
        frontend receives content before the 'turn complete' signal."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "q"},
                            {"role": "assistant", "content": "answer"},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_idx = next(i for i, f in enumerate(frames) if f.startswith("event: token"))
        done_idx = next(i for i, f in enumerate(frames) if f.startswith("event: done"))
        assert token_idx < done_idx


# ═══════════════════════════════════════════════════════════════════
# VAL-CHAT-002: Error messages from LLM failures appear in chat
# ═══════════════════════════════════════════════════════════════════


class TestErrorMessageExtraction:
    """The supervisor's error path adds an assistant message with error
    content.  This must be extracted and emitted as a token frame so the
    user sees the error in the chat UI."""

    def test_error_message_from_supervisor_error_path(self):
        """VAL-CHAT-002: error content in messages[-1] (role=assistant)
        is emitted as a token frame."""
        error_text = "⚠️ LLM Error: Connection refused (API key invalid?)"
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hello"},
                            {"role": "assistant", "content": error_text},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        assert len(token_frames) == 1
        _, data = _parse_frame(token_frames[0])
        assert data["content"] == error_text

    def test_error_message_with_arabic_content(self):
        """Error messages may contain Arabic text (friendly_llm_error
        produces bilingual messages)."""
        error_text = "⚠️ تعذّر الاتصال بنموذج اللغة"
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": error_text},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        assert len(token_frames) == 1
        _, data = _parse_frame(token_frames[0])
        assert data["content"] == error_text


# ═══════════════════════════════════════════════════════════════════
# VAL-CHAT-003: Session history includes assistant response
# ═══════════════════════════════════════════════════════════════════


class TestSessionHistoryPersistence:
    """After receiving a response, the conversation must be stored so
    that loading the session shows both the user message and the
    assistant response."""

    def test_assistant_response_stored_in_session(self):
        """VAL-CHAT-003: the full assistant response is stored in
        session.messages after the stream completes."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 30,
                        "last_cost_usd": 0.001,
                        "messages": [
                            {"role": "user", "content": "hello"},
                            {
                                "role": "assistant",
                                "content": "Hello! I am Kazma, your AI assistant.",
                            },
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        router = create_sse_chat_router(graph=graph, checkpointer=None)
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Send a message
        resp = client.post("/api/chat/stream", json={"message": "hello"})
        assert resp.status_code == 200

        # The SSE response should contain a token frame with the assistant content
        assert "event: token" in resp.text
        assert "Hello! I am Kazma" in resp.text

        # Fetch session messages
        sessions = client.get("/api/chat/sessions").json()
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]

        msgs = client.get(f"/api/chat/sessions/{session_id}/messages").json()
        # Must have user + assistant messages
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles

        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Hello! I am Kazma, your AI assistant."

    def test_content_acc_populated_from_terminal_state(self):
        """VAL-CHAT-003 (unit): content_acc must be populated from the
        terminal state so that the done handler stores it in session
        history."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "Greetings!"},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        # The token frame proves content was emitted
        token_frames = [f for f in frames if f.startswith("event: token")]
        assert len(token_frames) == 1
        _, data = _parse_frame(token_frames[0])
        assert data["content"] == "Greetings!"


# ═══════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for the terminal state message extraction."""

    def test_assistant_message_with_empty_content(self):
        """If the assistant message has empty content, do not emit a
        token frame (it would create an empty bubble)."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": ""},
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        assert len(token_frames) == 0

    def test_output_not_dict_does_not_crash(self):
        """If output is not a dict (e.g. None or a string), we must not
        crash."""
        events = [
            {
                "event": "on_chain_end",
                "data": {"output": None},
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(done_frames) == 1

    def test_message_with_content_key_missing(self):
        """If the last message dict has no 'content' key, do not crash."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant"},  # no content key
                        ],
                    }
                },
                "name": "__end__",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(done_frames) == 1


# ═══════════════════════════════════════════════════════════════════
# LangGraph 1.x compatibility: event name is "LangGraph" not "__end__"
# ═══════════════════════════════════════════════════════════════════


class TestLangGraphV1EventName:
    """LangGraph 1.x emits the terminal on_chain_end event with name
    'LangGraph' (not '__end__').  The handler must fire for both names."""

    def test_final_message_extracted_with_langgraph_name(self):
        """VAL-CHAT-001: with real LangGraph 1.x, the terminal event name
        is 'LangGraph'.  The handler must extract the assistant message."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 50,
                        "last_cost_usd": 0.002,
                        "messages": [
                            {"role": "user", "content": "hello"},
                            {
                                "role": "assistant",
                                "content": "Hi from LangGraph!",
                            },
                        ],
                    }
                },
                "name": "LangGraph",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        token_frames = [f for f in frames if f.startswith("event: token")]
        done_frames = [f for f in frames if f.startswith("event: done")]

        assert len(token_frames) == 1
        _, data = _parse_frame(token_frames[0])
        assert data["content"] == "Hi from LangGraph!"
        assert len(done_frames) == 1

    def test_cost_extracted_with_langgraph_name(self):
        """Cost/tokens must also be extracted when the event name is
        'LangGraph'."""
        events = [
            {
                "event": "on_chain_end",
                "data": {
                    "output": {
                        "last_tokens": 42,
                        "last_cost_usd": 0.003,
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "ok"},
                        ],
                    }
                },
                "name": "LangGraph",
            },
        ]

        graph = MagicMock()
        graph.astream_events.return_value = _mock_astream_events(events)

        frames = _collect_frames(
            graph,
            {"messages": []},
            {"configurable": {"thread_id": "t1"}},
        )

        done_frames = [f for f in frames if f.startswith("event: done")]
        assert len(done_frames) == 1
        _, data = _parse_frame(done_frames[0])
        assert data["tokens"] == 42
        assert data["cost"] == 0.003
