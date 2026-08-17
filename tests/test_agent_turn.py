"""Single-brain turn helper + SSE mouth parser."""

from __future__ import annotations

from kazma_core.agent.turn import extract_assistant_text
from kazma_core.agent.turn_client import iter_sse_frames
from kazma_core.runtime.local_api import candidate_api_bases


def test_extract_assistant_text_last_nonempty():
    state = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "first"},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "final answer"},
        ]
    }
    assert extract_assistant_text(state) == "final answer"
    assert extract_assistant_text({}) == ""
    assert extract_assistant_text({"messages": [{"role": "assistant", "content": ""}]}) == ""


def test_iter_sse_frames_tokens_tools_done():
    raw = [
        "event: token",
        "data: {\"content\": \"Hel\"}",
        "",
        "event: token",
        "data: {\"content\": \"lo\"}",
        "",
        "event: tool_call",
        "data: {\"tool_name\": \"file_read\"}",
        "",
        "event: done",
        "data: {\"tokens\": 2}",
        "",
    ]
    evs = iter_sse_frames(raw)
    kinds = [e.kind for e in evs]
    assert kinds == ["token", "token", "tool_call", "done"]
    assert evs[0].text + evs[1].text == "Hello"
    assert evs[2].tool == "file_read"


def test_iter_sse_frames_done_and_capacity_carry_text():
    evs = iter_sse_frames([
        "event: done",
        "data: {\"content\": \"full reply\", \"tokens\": 3}",
        "",
        "event: capacity",
        "data: {\"action\": \"yolo\", \"reply\": \"YOLO ON\"}",
        "",
    ])
    assert evs[0].kind == "done"
    assert evs[0].text == "full reply"
    assert evs[1].kind == "capacity"
    assert evs[1].text == "YOLO ON"


def test_candidate_api_bases_include_9090_and_8000(monkeypatch):
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    monkeypatch.delenv("KAZMA_BASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_PORT", raising=False)
    bases = candidate_api_bases()
    assert "http://127.0.0.1:9090" in bases
    assert "http://127.0.0.1:8000" in bases
    assert bases[0] == "http://127.0.0.1:9090"


def test_candidate_api_bases_honor_env(monkeypatch):
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "http://127.0.0.1:9090")
    monkeypatch.setenv("KAZMA_PORT", "9090")
    bases = candidate_api_bases()
    assert bases.count("http://127.0.0.1:9090") == 1


def test_candidate_api_bases_ignore_public_url(monkeypatch):
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.kazma.ai")
    monkeypatch.delenv("KAZMA_BASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_PORT", raising=False)
    bases = candidate_api_bases()
    assert bases[0] == "http://127.0.0.1:9090"
    assert "https://my.kazma.ai" not in bases


def test_candidate_api_bases_honor_explicit_base_url(monkeypatch):
    monkeypatch.setenv("KAZMA_BASE_URL", "http://127.0.0.1:9191")
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    monkeypatch.delenv("KAZMA_PORT", raising=False)
    bases = candidate_api_bases()
    assert bases[0] == "http://127.0.0.1:9090"
    assert "http://127.0.0.1:9191" in bases
