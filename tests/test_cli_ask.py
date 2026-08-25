"""kazma ask + ACP JSON-RPC (no live LLM)."""

from __future__ import annotations

import json

import pytest

from kazma_core.cli.ask import (
    ACP_PROTOCOL_VERSION,
    AcpSessionState,
    AskOptions,
    extract_hitl_interrupt,
    handle_acp_request,
    map_permission_outcome,
    parse_ask_argv,
    prompt_from_acp_blocks,
    run_ask,
    tool_kind_for,
)


class TestParse:
    def test_prompt_and_flags(self) -> None:
        o = parse_ask_argv(["--plan", "--json", "fix", "the", "tests"])
        assert o.plan is True
        assert o.json_out is True
        assert o.prompt == "fix the tests"
        assert o.acp is False
        assert o.stream is True

    def test_workspace_and_yolo(self) -> None:
        o = parse_ask_argv(["--workspace", "/tmp/ws", "--yolo", "hello"])
        assert o.workspace == "/tmp/ws"
        assert o.yolo is True
        assert o.prompt == "hello"

    def test_no_stream(self) -> None:
        o = parse_ask_argv(["--no-stream", "hi"])
        assert o.stream is False
        assert o.prompt == "hi"

    def test_unknown_flag(self) -> None:
        with pytest.raises(ValueError, match="Unknown flag"):
            parse_ask_argv(["--nope", "x"])

    def test_help(self) -> None:
        assert parse_ask_argv(["--help"]).help is True


class TestToolKind:
    def test_kinds(self) -> None:
        assert tool_kind_for("file_write") == "edit"
        assert tool_kind_for("file_apply_patch") == "edit"
        assert tool_kind_for("shell_exec") == "execute"
        assert tool_kind_for("file_read") == "read"
        assert tool_kind_for("codebase_search") == "search"
        assert tool_kind_for("file_delete") == "delete"
        assert tool_kind_for("memory_store") == "other"


class TestHitlHelpers:
    def test_extract_from_snapshot(self) -> None:
        class Intr:
            value = {"type": "hitl_approval", "tool": "shell_exec", "args": {"c": "1"}}

        class Task:
            interrupts = [Intr()]

        class Snap:
            tasks = [Task()]

        payload = extract_hitl_interrupt(Snap())
        assert payload is not None
        assert payload["tool"] == "shell_exec"

    def test_extract_from_interrupt_list(self) -> None:
        snap = {"__interrupt__": [{"type": "hitl_approval", "tool": "file_write"}]}
        assert extract_hitl_interrupt(snap)["tool"] == "file_write"

    def test_extract_none(self) -> None:
        class Snap:
            tasks = []

        assert extract_hitl_interrupt(Snap()) is None
        assert extract_hitl_interrupt(None) is None
        assert extract_hitl_interrupt({}) is None

    def test_map_allow_once(self) -> None:
        r = map_permission_outcome(
            {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
        )
        assert r["approved"] is True
        assert not r.get("always")

    def test_map_allow_always(self) -> None:
        r = map_permission_outcome({"outcome": {"optionId": "allow-always"}})
        assert r["approved"] is True
        assert r["always"] is True

    def test_map_reject(self) -> None:
        r = map_permission_outcome({"outcome": {"optionId": "reject-once"}})
        assert r["approved"] is False

    def test_map_cancelled(self) -> None:
        r = map_permission_outcome({"outcome": {"outcome": "cancelled"}})
        assert r["approved"] is False
        assert r.get("cancelled") is True


class TestAcp:
    def test_prompt_blocks(self) -> None:
        text = prompt_from_acp_blocks(
            [
                {"type": "text", "text": "Look at this"},
                {
                    "type": "resource",
                    "resource": {"uri": "file:///x.py", "text": "print(1)"},
                },
            ]
        )
        assert "Look at this" in text
        assert "print(1)" in text

    def test_initialize_and_session_new(self) -> None:
        st = AcpSessionState()
        init = handle_acp_request(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": 1},
            },
            st,
        )
        assert init is not None
        assert init["result"]["protocolVersion"] == ACP_PROTOCOL_VERSION
        assert init["result"]["agentInfo"]["name"] == "kazma"
        assert st.initialized is True

        new = handle_acp_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {"cwd": "/tmp/proj"},
            },
            st,
        )
        assert new is not None
        sid = new["result"]["sessionId"]
        assert sid in st.sessions
        assert st.workspace == "/tmp/proj"

    def test_prompt_is_async_marker(self) -> None:
        st = AcpSessionState()
        handle_acp_request(
            {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
            st,
        )
        handle_acp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}},
            st,
        )
        resp = handle_acp_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": next(iter(st.sessions)),
                    "prompt": [{"type": "text", "text": "hello"}],
                },
            },
            st,
        )
        assert resp is not None
        assert resp["_async"] == "prompt"
        assert resp["prompt"] == "hello"

    def test_unknown_method(self) -> None:
        st = AcpSessionState()
        err = handle_acp_request(
            {"jsonrpc": "2.0", "id": 9, "method": "nope/x", "params": {}},
            st,
        )
        assert err is not None
        assert err["error"]["code"] == -32601

    def test_cancel_is_notification(self) -> None:
        st = AcpSessionState()
        assert handle_acp_request(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {}},
            st,
        ) is None


class TestRunAsk:
    @pytest.mark.asyncio
    async def test_empty_prompt(self) -> None:
        r = await run_ask("")
        assert r.ok is False
        assert "Empty" in r.error

    @pytest.mark.asyncio
    async def test_invoke_monkeypatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake(prompt: str, *, thread_id: str, **kwargs: object) -> str:
            assert "hello" in prompt
            return "world"

        monkeypatch.setattr("kazma_core.cli.ask._invoke_supervisor", fake)
        monkeypatch.setattr("kazma_core.cli.ask._boot_env", lambda **k: None)
        r = await run_ask("hello", AskOptions(prompt="hello"))
        assert r.ok is True
        assert r.text == "world"
        assert r.thread_id.startswith("cli-")

    @pytest.mark.asyncio
    async def test_on_event_tokens_and_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict] = []

        async def fake(
            prompt: str,
            *,
            thread_id: str,
            on_event=None,
            **kwargs: object,
        ) -> str:
            if on_event:
                on_event({"event": "token", "text": "hi"})
            return "hi there"

        monkeypatch.setattr("kazma_core.cli.ask._invoke_supervisor", fake)
        monkeypatch.setattr("kazma_core.cli.ask._boot_env", lambda **k: None)
        r = await run_ask(
            "x",
            AskOptions(prompt="x", stream=True),
            on_event=events.append,
        )
        assert r.ok is True
        assert r.streamed is True
        kinds = [e["event"] for e in events]
        assert "token" in kinds
        assert "done" in kinds

    @pytest.mark.asyncio
    async def test_no_stream_skips_streamed_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake(prompt: str, *, thread_id: str, **kwargs: object) -> str:
            return "full"

        monkeypatch.setattr("kazma_core.cli.ask._invoke_supervisor", fake)
        monkeypatch.setattr("kazma_core.cli.ask._boot_env", lambda **k: None)
        r = await run_ask("x", AskOptions(stream=False), on_event=lambda e: None)
        assert r.ok is True
        assert r.streamed is False


class TestCliWrapper:
    def test_json_ndjson_tokens(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        from kazma_core.cli.ask import AskResult

        async def fake_run_ask(prompt, opts, *, on_event=None, hitl_decide=None):
            assert prompt == "hello"
            if on_event:
                on_event({"event": "token", "text": "hi"})
                on_event({"event": "tool_start", "tool": "file_read"})
                on_event({"event": "done", "ok": True, "text": "hi", "thread_id": "t"})
            return AskResult(ok=True, text="hi", thread_id="t", streamed=True)

        monkeypatch.setattr("kazma_cli.ask.run_ask", fake_run_ask)
        from kazma_cli.ask import run

        code = run(["--json", "hello"])
        assert code == 0
        lines = [
            json.loads(line)
            for line in capsys.readouterr().out.strip().splitlines()
            if line.strip()
        ]
        assert any(row.get("event") == "token" for row in lines)
        assert any(row.get("event") == "done" for row in lines)

    def test_no_stream_json_blob(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        from kazma_core.cli.ask import AskResult

        async def fake_run_ask(prompt, opts, *, on_event=None, hitl_decide=None):
            assert on_event is None
            return AskResult(ok=True, text="full", thread_id="t", streamed=False)

        monkeypatch.setattr("kazma_cli.ask.run_ask", fake_run_ask)
        from kazma_cli.ask import run

        code = run(["--json", "--no-stream", "hello"])
        assert code == 0
        blob = json.loads(capsys.readouterr().out)
        assert blob["ok"] is True
        assert blob["text"] == "full"

    def test_tty_hitl_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_cli.ask import _hitl_decide_tty

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdin.readline", lambda: "y\n")
        r = _hitl_decide_tty(
            {"tool": "shell_exec", "message": "run echo"},
            stdin_consumed=False,
        )
        assert r == {"approved": True}

    def test_hitl_deny_when_stdin_consumed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kazma_cli.ask import _hitl_decide_tty

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        r = _hitl_decide_tty({"tool": "shell_exec"}, stdin_consumed=True)
        assert r["approved"] is False
