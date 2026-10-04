"""Test evaluation plumbing with scripted clients; no accuracy claims."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from kazma_core.agent_evaluation import evaluate_case, fingerprint, summarize, validate_dataset
from kazma_core.llm_provider import LLMError, LLMResponse, ToolCall

EXAMPLES = Path(__file__).parent / "fixtures" / "live_agent_eval_examples.json"


@pytest.fixture
def case(monkeypatch):
    from kazma_core.config_store import get_config_store

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setenv("KAZMA_LLM_STREAM", "0")
    store = get_config_store()
    store.set("memory.enabled", False)
    store.set("agent.nonstop.ledger.enabled", False)
    store.set("agent.nonstop.failover.enabled", False)
    return copy.deepcopy(validate_dataset(json.loads(EXAMPLES.read_text(encoding="utf-8")))[0])


def test_examples_are_not_human_holdout():
    cases = validate_dataset(json.loads(EXAMPLES.read_text(encoding="utf-8")))
    assert {case["language"] for case in cases} == {"ar", "en"}
    assert all(case["human_labeled"] is False and case["split"] == "development" for case in cases)


def test_duplicates_do_not_inflate_evidence(case):
    duplicate = dict(case, id="different-id")
    with pytest.raises(ValueError, match="unique"):
        validate_dataset({"schema_version": 1, "cases": [case, duplicate]})


def test_source_groups_cannot_leak_into_holdout(case):
    related = dict(case, id="related", prompt="A different request from the same chat.", split="holdout")
    with pytest.raises(ValueError, match="source group"):
        validate_dataset({"schema_version": 1, "cases": [case, related]})


@pytest.mark.asyncio
async def test_actual_graph_records_tool_choice_and_pins_model(case):
    class Client:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            assert kwargs["model"] == "explicit-local-model"
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(id="read", name="file_read", arguments={"path": "status.txt"})])
            return LLMResponse(content="The release is pending review.")

    result = await evaluate_case(case, Client(), model="explicit-local-model", system_prompt="You are Kazma.")
    assert all(result["checks"].values()), result
    assert result["attempted_tools"] == [{"name": "file_read", "arguments": {"path": "status.txt"}}]
    assert result["fixture_calls"][0]["name"] == "file_read"
    assert result["review"] is None
    assert summarize({"results": [result]})["languages"]["en"]["accuracy"] is None


@pytest.mark.asyncio
async def test_danger_decision_is_recorded_but_never_executed(case):
    case["forbidden_tools"] = ["file_delete"]
    class Client:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(id="delete", name="file_delete", arguments={"path": "status.txt"})])
            return LLMResponse(content="The operation was denied.")

    result = await evaluate_case(case, Client(), model="pinned", system_prompt="You are Kazma.")
    assert result["attempted_tools"][0]["name"] == "file_delete"
    assert not result["fixture_calls"]
    assert result["checks"]["danger_tools_not_executed"] is True
    assert result["checks"]["forbidden_tools_not_attempted"] is False


@pytest.mark.asyncio
async def test_dead_turn_does_not_count_as_a_pass(case):
    class Client:
        async def chat(self, **kwargs):
            raise LLMError("bad schema", transient=False)

    result = await evaluate_case(case, Client(), model="pinned", system_prompt="You are Kazma.")
    assert result["checks"]["turn_succeeded"] is False
    assert result["error"]


def test_only_reviewed_frozen_human_holdout_counts():
    row = {"language": "en", "split": "holdout", "human_labeled": True,
           "answer": "pending", "checks": {"turn_succeeded": True},
           "review": {"reviewer": "operator", "answer_sha256": fingerprint("pending"),
                      "completion": True, "grounding": True, "language": True, "injection_resistance": True}}
    assert summarize({"results": [row]})["reviewed_holdout"] == 1
    row["answer"] = "changed after review"
    assert summarize({"results": [row]})["reviewed_holdout"] == 0
    row["answer"] = "pending"
    row["human_labeled"] = False
    assert summarize({"results": [row]})["reviewed_holdout"] == 0


def test_child_strips_live_store_overrides(tmp_path):
    from scripts.live_eval import environment

    child = environment(tmp_path, source={"KAZMA_DATABASE_URL": "postgresql://live",
                                         "DATABASE_URL": "postgresql://live",
                                         "KAZMA_SETTINGS_DB": "live-settings.db",
                                         "KAZMA_SKILLS_HOME": "live-skills"})
    assert "DATABASE_URL" not in child and "KAZMA_DATABASE_URL" not in child
    assert "KAZMA_SETTINGS_DB" not in child
    assert child["KAZMA_SKILLS_HOME"] == str(tmp_path / "skills")
    assert child["KAZMA_DB_BACKEND"] == "sqlite"


def test_isolated_worker_runs_native_provider_http_path(case, monkeypatch, tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    from scripts.live_eval import run_cases

    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            assert request["model"] == "test-local-model"
            if len(calls) == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "read", "type": "function", "function": {"name": "file_read", "arguments": '{"path":"status.txt"}'}}]}
            else:
                message = {"role": "assistant", "content": "The release is pending review."}
            body = json.dumps({"model": "test-local-model", "choices": [{"message": message, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1"
    registry = SimpleNamespace(get_provider=lambda name: {"name": "local", "enabled": True, "base_url": endpoint, "api_key": ""},
                               resolve_provider_credentials=lambda name: (endpoint, ""))
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    try:
        output = tmp_path / "report.json"
        report = run_cases({"schema_version": 1, "cases": [case]}, provider="local", model="test-local-model", output=output, timeout=60)
        assert len(calls) >= 2
        assert all(report["results"][0]["checks"].values()), report["results"]
        assert report["summary"]["reviewed_holdout"] == 0
        assert report["summary"]["languages"]["en"]["accuracy"] is None
        assert json.loads(output.read_text(encoding="utf-8"))["dataset_sha256"] == fingerprint({"schema_version": 1, "cases": [case]})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
