"""Tests for the gateway, swarm, and status CLI commands.

Covers argument parsing, HTTP payload construction (mocked), command
routing in main.py, and completions SUBCMDS correctness.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_cli import completions
from kazma_cli.completions import SUBCMDS
from kazma_cli.gateway import (
    ServerNotRunningError,
    extract_port,
    resolve_base_url,
    resolve_port,
)
from kazma_cli.gateway import (
    cmd_refresh as gw_cmd_refresh,
)
from kazma_cli.gateway import (
    cmd_restart as gw_cmd_restart,
)
from kazma_cli.gateway import (
    cmd_start as gw_cmd_start,
)
from kazma_cli.gateway import (
    cmd_status as gw_cmd_status,
)
from kazma_cli.gateway import (
    cmd_stop as gw_cmd_stop,
)
from kazma_cli.swarm import (
    _split_workers,
    cmd_approve,
    cmd_broadcast,
    cmd_circuit_breaker,
    cmd_consult,
    cmd_dispatch,
    cmd_fanout,
    cmd_history,
    cmd_metrics,
    cmd_pipeline,
    cmd_reject,
    cmd_start,
    cmd_stop,
    cmd_task,
    cmd_worker_add,
    cmd_worker_remove,
    cmd_worker_spawn,
    cmd_workers,
    parse_flags,
)
from kazma_cli.swarm import (
    cmd_status as swarm_cmd_status,
)

# ---------------------------------------------------------------------------
# Gateway argument parsing
# ---------------------------------------------------------------------------

class TestGatewayArgParsing:
    """Gateway --port and URL resolution helpers."""

    def test_extract_port_with_value(self) -> None:
        port, remaining = extract_port(["--port", "9000", "status"])
        assert port == 9000
        assert remaining == ["status"]

    def test_extract_port_without_flag(self) -> None:
        port, remaining = extract_port(["status"])
        assert port is None
        assert remaining == ["status"]

    def test_extract_port_keeps_other_args(self) -> None:
        port, remaining = extract_port(["start", "--port", "1234"])
        assert port == 1234
        assert remaining == ["start"]

    def test_resolve_port_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert resolve_port() == 8000

    def test_resolve_port_from_env(self) -> None:
        with patch.dict("os.environ", {"KAZMA_PORT": "9999"}, clear=True):
            assert resolve_port() == 9999

    def test_resolve_port_explicit_overrides_env(self) -> None:
        with patch.dict("os.environ", {"KAZMA_PORT": "9999"}, clear=True):
            assert resolve_port(7777) == 7777

    def test_resolve_port_invalid_env_falls_back(self) -> None:
        with patch.dict("os.environ", {"KAZMA_PORT": "not-a-number"}, clear=True):
            assert resolve_port() == 8000

    def test_resolve_base_url(self) -> None:
        assert resolve_base_url(8000) == "http://localhost:8000"
        assert resolve_base_url(9000) == "http://localhost:9000"


# ---------------------------------------------------------------------------
# Swarm argument parsing
# ---------------------------------------------------------------------------

class TestSwarmArgParsing:
    """Swarm parse_flags and worker-list helpers."""

    def test_parse_flags_value_flags(self) -> None:
        positionals, values, bools = parse_flags(
            ["--model", "gpt-4o", "--provider", "openai", "name", "prompt"]
        )
        assert positionals == ["name", "prompt"]
        assert values == {"--model": "gpt-4o", "--provider": "openai"}
        assert bools == set()

    def test_parse_flags_bool_flag(self) -> None:
        positionals, _values, bools = parse_flags(["--reset", "worker1"])
        assert positionals == ["worker1"]
        assert "--reset" in bools

    def test_parse_flags_equals_form(self) -> None:
        positionals, values, _bools = parse_flags(["--model=gpt-4o", "name"])
        assert positionals == ["name"]
        assert values == {"--model": "gpt-4o"}

    def test_parse_flags_no_args(self) -> None:
        positionals, values, bools = parse_flags([])
        assert positionals == []
        assert values == {}
        assert bools == set()

    def test_split_workers_trims(self) -> None:
        assert _split_workers("a, b ,c") == ["a", "b", "c"]

    def test_split_workers_empty(self) -> None:
        assert _split_workers("") == []
        assert _split_workers(" , , ") == []


# ---------------------------------------------------------------------------
# Gateway command functions (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.fixture
def base_url() -> str:
    return "http://localhost:8000"


class TestGatewayCommands:
    """Gateway command handlers with mocked _request."""

    async def test_cmd_status_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            return_value={
                "adapters": [{"platform": "telegram", "status": "running", "uptime_seconds": 42}],
                "persistence": {"active_threads": 3, "session_store": "sqlite"},
                "threads": [],
            },
        ):
            rc = await gw_cmd_status(base_url)
        assert rc == 0

    async def test_cmd_status_server_not_running(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            side_effect=ServerNotRunningError("refused"),
        ):
            rc = await gw_cmd_status(base_url)
        assert rc == 1

    async def test_cmd_start_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            return_value={"status": "started"},
        ):
            rc = await gw_cmd_start(base_url)
        assert rc == 0

    async def test_cmd_stop_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            return_value={"status": "stopped"},
        ):
            rc = await gw_cmd_stop(base_url)
        assert rc == 0

    async def test_cmd_restart_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            side_effect=[{"status": "stopped"}, {"status": "started"}],
        ), patch("kazma_cli.gateway.asyncio.sleep", new_callable=AsyncMock):
            rc = await gw_cmd_restart(base_url)
        assert rc == 0

    async def test_cmd_restart_server_down(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            side_effect=ServerNotRunningError("refused"),
        ):
            rc = await gw_cmd_restart(base_url)
        assert rc == 1

    async def test_cmd_refresh_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.gateway._request",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ):
            rc = await gw_cmd_refresh(base_url)
        assert rc == 0


# ---------------------------------------------------------------------------
# Swarm command functions (mocked HTTP)
# ---------------------------------------------------------------------------

class TestSwarmCommands:
    """Swarm command handlers with mocked _request."""

    async def test_cmd_status_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={
                "workers": [{"name": "w1", "model": "gpt-4o", "status": "online"}],
                "count": 1,
                "started": True,
            },
        ):
            rc = await swarm_cmd_status(base_url)
        assert rc == 0

    async def test_cmd_status_server_not_running(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            side_effect=ServerNotRunningError("refused"),
        ):
            rc = await swarm_cmd_status(base_url)
        assert rc == 1

    async def test_cmd_workers_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"workers": [], "count": 0, "started": False},
        ):
            rc = await cmd_workers(base_url)
        assert rc == 0

    async def test_cmd_worker_add_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(return_value={"status": "ok", "worker": {"name": "alice"}})
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_worker_add(
                base_url, ["alice"], {"--model": "gpt-4o", "--provider": "openai"}
            )
        assert rc == 0
        call_kwargs = mock_req.call_args
        payload = call_kwargs.kwargs["json"]
        assert payload["name"] == "alice"
        assert payload["model"] == "gpt-4o"
        assert payload["provider"] == "openai"

    async def test_cmd_worker_add_missing_name(self, base_url: str) -> None:
        rc = await cmd_worker_add(base_url, [], {})
        assert rc == 1

    async def test_cmd_worker_spawn_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(return_value={"status": "ok", "worker": {"name": "bob"}})
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_worker_spawn(
                base_url, ["bob", "researcher"], {"--model": "claude"}
            )
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["name"] == "bob"
        assert payload["role"] == "researcher"
        assert payload["capabilities"] == {"role": "researcher"}
        assert payload["model"] == "claude"

    async def test_cmd_worker_spawn_missing_role(self, base_url: str) -> None:
        rc = await cmd_worker_spawn(base_url, ["bob"], {})
        assert rc == 1

    async def test_cmd_worker_remove_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"status": "ok", "message": "removed"},
        ):
            rc = await cmd_worker_remove(base_url, ["alice"])
        assert rc == 0

    async def test_cmd_worker_remove_missing_name(self, base_url: str) -> None:
        rc = await cmd_worker_remove(base_url, [])
        assert rc == 1

    async def test_cmd_dispatch_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "dispatched": ["w1"], "task_id": "t1", "results": []}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_dispatch(base_url, ["w1", "do thing"], {"--context": "ctx"})
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["workers"] == ["w1"]
        assert payload["task"] == "do thing"
        assert payload["context"] == "ctx"
        assert payload["type"] == "dispatch"

    async def test_cmd_dispatch_missing_args(self, base_url: str) -> None:
        rc = await cmd_dispatch(base_url, ["w1"], {})
        assert rc == 1

    async def test_cmd_broadcast_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "dispatched": ["all"], "results": []}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_broadcast(base_url, ["hello"], {})
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["workers"] == ["all"]
        assert payload["type"] == "broadcast"

    async def test_cmd_consult_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "dispatched": ["a", "b"], "results": []}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_consult(base_url, ["opinion?"], {"--workers": "a,b"})
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["workers"] == ["a", "b"]
        assert payload["type"] == "consult"

    async def test_cmd_consult_missing_workers(self, base_url: str) -> None:
        rc = await cmd_consult(base_url, ["opinion?"], {})
        assert rc == 1

    async def test_cmd_pipeline_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "dispatched": ["a", "b", "c"], "results": []}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_pipeline(base_url, ["step"], {"--workers": "a,b,c"})
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["workers"] == ["a", "b", "c"]
        assert payload["type"] == "pipeline"

    async def test_cmd_fanout_builds_payload(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "dispatched": ["a", "b"], "results": []}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_fanout(
                base_url, ["task"], {"--workers": "a,b", "--aggregation": "vote"}
            )
        assert rc == 0
        payload = mock_req.call_args.kwargs["json"]
        assert payload["workers"] == ["a", "b"]
        assert payload["type"] == "fan_out"
        assert payload["aggregation"] == "vote"

    async def test_cmd_history_with_filters(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"tasks": [{"task_id": "t1", "type": "dispatch", "status": "completed",
                                     "workers": ["w1"], "prompt": "hi"}],
                          "total": 1, "page": 1}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_history(
                base_url, {"--type": "dispatch", "--page": "2", "--page-size": "5"}
            )
        assert rc == 0
        params = mock_req.call_args.kwargs["params"]
        assert params["type"] == "dispatch"
        assert params["page"] == "2"
        assert params["pageSize"] == "5"

    async def test_cmd_history_empty(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"tasks": [], "count": 0},
        ):
            rc = await cmd_history(base_url, {})
        assert rc == 0

    async def test_cmd_task_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"task": {"task_id": "t1", "type": "dispatch", "status": "completed",
                                   "workers": ["w1"], "prompt": "hi"}},
        ):
            rc = await cmd_task(base_url, ["t1"])
        assert rc == 0

    async def test_cmd_task_missing_id(self, base_url: str) -> None:
        rc = await cmd_task(base_url, [])
        assert rc == 1

    async def test_cmd_metrics_all(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"metrics": [{"worker": "w1", "date": "2026-01-01", "total": 5,
                                       "success": 4, "failed": 1, "avg_duration": 1.2}]},
        ):
            rc = await cmd_metrics(base_url, {})
        assert rc == 0

    async def test_cmd_metrics_single_worker(self, base_url: str) -> None:
        mock_req = AsyncMock(return_value={"metrics": [], "worker": "w1"})
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_metrics(base_url, {"--worker": "w1"})
        assert rc == 0
        # Should hit the per-worker endpoint: _request(client, "GET", path)
        call_args = mock_req.call_args
        assert call_args.args[1] == "GET"
        assert "w1" in call_args.args[2]

    async def test_cmd_start_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"status": "ok", "message": "started"},
        ):
            rc = await cmd_start(base_url)
        assert rc == 0

    async def test_cmd_stop_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"status": "ok", "message": "stopped"},
        ):
            rc = await cmd_stop(base_url)
        assert rc == 0

    async def test_cmd_approve_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"status": "ok", "message": "approved"},
        ):
            rc = await cmd_approve(base_url, ["task-1"])
        assert rc == 0

    async def test_cmd_approve_missing_id(self, base_url: str) -> None:
        rc = await cmd_approve(base_url, [])
        assert rc == 1

    async def test_cmd_reject_ok(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"status": "rejected", "message": "aborted"},
        ):
            rc = await cmd_reject(base_url, ["task-1"])
        assert rc == 0

    async def test_cmd_reject_missing_id(self, base_url: str) -> None:
        rc = await cmd_reject(base_url, [])
        assert rc == 1

    async def test_cmd_circuit_breaker_all(self, base_url: str) -> None:
        with patch(
            "kazma_cli.swarm._request",
            new_callable=AsyncMock,
            return_value={"breakers": {"w1": {"state": "closed", "failure_count": 0}}, "count": 1},
        ):
            rc = await cmd_circuit_breaker(base_url, [], set())
        assert rc == 0

    async def test_cmd_circuit_breaker_single(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"worker": "w1", "circuit_breaker": {"state": "open", "failure_count": 3}}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_circuit_breaker(base_url, ["w1"], set())
        assert rc == 0
        # GET endpoint for a single worker: _request(client, "GET", path)
        call_args = mock_req.call_args
        assert call_args.args[1] == "GET"
        assert call_args.args[2].endswith("/api/swarm/workers/w1/circuit-breaker")

    async def test_cmd_circuit_breaker_reset(self, base_url: str) -> None:
        mock_req = AsyncMock(
            return_value={"status": "ok", "worker": "w1",
                          "circuit_breaker": {"state": "closed", "failure_count": 0}}
        )
        with patch("kazma_cli.swarm._request", new_callable=AsyncMock, side_effect=mock_req):
            rc = await cmd_circuit_breaker(base_url, ["w1"], {"--reset"})
        assert rc == 0
        call_args = mock_req.call_args
        assert call_args.args[1] == "POST"
        assert call_args.args[2].endswith("/api/swarm/workers/w1/circuit-breaker/reset")


# ---------------------------------------------------------------------------
# Status command (mocked httpx.Client)
# ---------------------------------------------------------------------------

class TestStatusCommand:
    """Tests for the real kazma status command."""

    def _make_client(self, get_responses: list) -> MagicMock:
        """Build a mock httpx.Client context manager."""
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = get_responses
        return client

    def test_status_server_running(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kazma_cli.main import _run_status

        gw_resp = MagicMock(status_code=200)
        gw_resp.json.return_value = {
            "adapters": [{"platform": "telegram", "status": "running"}],
            "threads": [],
        }
        swarm_resp = MagicMock(status_code=200)
        swarm_resp.json.return_value = {"count": 3, "workers": []}

        client = self._make_client([gw_resp, swarm_resp])
        with patch("httpx.Client", return_value=client), patch.dict(
            "os.environ", {}, clear=True
        ):
            _run_status()

        out = capsys.readouterr().out
        assert "running on http://localhost:8000" in out
        assert "1 adapter(s) active" in out
        assert "telegram" in out
        assert "3 worker(s) registered" in out

    def test_status_server_not_running(self, capsys: pytest.CaptureFixture[str]) -> None:
        import httpx
        from kazma_cli.main import _run_status

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = httpx.ConnectError("refused")

        with patch("httpx.Client", return_value=client), patch.dict(
            "os.environ", {}, clear=True
        ):
            _run_status()

        out = capsys.readouterr().out
        assert "not running" in out
        assert "kazma serve" in out

    def test_status_shows_python_and_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        import httpx
        from kazma_cli.main import _run_status

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = httpx.ConnectError("refused")

        with patch("httpx.Client", return_value=client), patch.dict(
            "os.environ", {}, clear=True
        ):
            _run_status()

        out = capsys.readouterr().out
        assert "Python:" in out
        assert "Kazma:" in out
        assert "Config:" in out


# ---------------------------------------------------------------------------
# Command routing in main.py
# ---------------------------------------------------------------------------

class TestCommandRouting:
    """Verify main() dispatches to gateway/swarm/project handlers."""

    def test_gateway_routing(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "gateway", "status"]
            with patch("kazma_cli.gateway.run") as mock_run:
                main_mod.main()
            mock_run.assert_called_once()
            # The handler should receive the subcommand args
            passed = mock_run.call_args.args[0]
            assert "status" in passed
        finally:
            main_mod.sys.argv = original_argv

    def test_swarm_routing(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "swarm", "workers"]
            with patch("kazma_cli.swarm.run") as mock_run:
                main_mod.main()
            mock_run.assert_called_once()
            passed = mock_run.call_args.args[0]
            assert "workers" in passed
        finally:
            main_mod.sys.argv = original_argv

    def test_status_routing(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "status"]
            with patch("kazma_cli.main._run_status") as mock_status:
                main_mod.main()
            mock_status.assert_called_once()
        finally:
            main_mod.sys.argv = original_argv

    def test_gateway_routing_port_flag(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "gateway", "start", "--port", "9000"]
            with patch("kazma_cli.gateway.run") as mock_run:
                main_mod.main()
            passed = mock_run.call_args.args[0]
            assert "--port" in passed
            assert "9000" in passed
        finally:
            main_mod.sys.argv = original_argv

    def test_unknown_command_exits(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "bogus-command"]
            with pytest.raises(SystemExit):
                main_mod.main()
        finally:
            main_mod.sys.argv = original_argv


# ---------------------------------------------------------------------------
# Completions SUBCMDS correctness
# ---------------------------------------------------------------------------

class TestCompletionsSubcmds:
    """Verify SUBCMDS reflects the implemented commands."""

    def test_chat_removed(self) -> None:
        assert "chat" not in SUBCMDS

    def test_project_added(self) -> None:
        assert "project" in SUBCMDS

    def test_gateway_added(self) -> None:
        assert "gateway" in SUBCMDS

    def test_swarm_added(self) -> None:
        assert "swarm" in SUBCMDS

    def test_core_commands_present(self) -> None:
        for cmd in ("serve", "status", "help", "completion", "wizard", "hub", "docs"):
            assert cmd in SUBCMDS, f"{cmd} missing from SUBCMDS"

    def test_bash_script_has_new_commands(self) -> None:
        from kazma_cli.completions import _bash_completion_script

        output = _bash_completion_script()
        assert "gateway" in output
        assert "swarm" in output
        assert "project" in output
        assert "chat" not in output

    def test_zsh_script_has_new_commands(self) -> None:
        from kazma_cli.completions import _zsh_completion_script

        output = _zsh_completion_script()
        assert "gateway[Gateway control]" in output
        assert "swarm[Swarm orchestration]" in output
        assert "project[Project-level config]" in output
        assert "chat[" not in output

    def test_subcmds_module_attribute_matches(self) -> None:
        assert completions.SUBCMDS is SUBCMDS


# ---------------------------------------------------------------------------
# Gateway/Swarm run() dispatch (sync entry points)
# ---------------------------------------------------------------------------

class TestRunDispatch:
    """Verify the sync run() entry points dispatch to async handlers."""

    def test_gateway_run_status(self) -> None:
        from kazma_cli.gateway import run

        with patch("kazma_cli.gateway.cmd_status", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = 0
            run(["status"])
        mock_cmd.assert_awaited_once()

    def test_swarm_run_status(self) -> None:
        from kazma_cli.swarm import run

        with patch("kazma_cli.swarm.cmd_status", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = 0
            run(["status"])
        mock_cmd.assert_awaited_once()

    def test_swarm_run_worker_add(self) -> None:
        from kazma_cli.swarm import run

        with patch("kazma_cli.swarm.cmd_worker_add", new_callable=AsyncMock) as mock_cmd:
            mock_cmd.return_value = 0
            run(["worker", "add", "alice", "--model", "gpt-4o"])
        mock_cmd.assert_awaited_once()
        positionals = mock_cmd.call_args.args[1]
        flags = mock_cmd.call_args.args[2]
        assert positionals == ["alice"]
        assert flags["--model"] == "gpt-4o"

    def test_gateway_run_help_no_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kazma_cli.gateway import run

        run(["--help"])
        out = capsys.readouterr().out
        assert "Gateway" in out

    def test_swarm_run_help_no_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kazma_cli.swarm import run

        run(["--help"])
        out = capsys.readouterr().out
        assert "Swarm" in out

    def test_gateway_run_unknown_exits(self) -> None:
        from kazma_cli.gateway import run

        with pytest.raises(SystemExit):
            run(["bogus"])

    def test_swarm_run_unknown_exits(self) -> None:
        from kazma_cli.swarm import run

        with pytest.raises(SystemExit):
            run(["bogus"])


# ---------------------------------------------------------------------------
# Update command — flag parsing
# ---------------------------------------------------------------------------

class TestUpdateFlagParsing:
    """Update command parse_update_flags helper."""

    def test_parse_check_long_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, positionals = parse_update_flags(["--check"])
        assert "--check" in flags
        assert positionals == []

    def test_parse_check_short_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, _positionals = parse_update_flags(["-c"])
        assert "-c" in flags

    def test_parse_force_long_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, _positionals = parse_update_flags(["--force"])
        assert "--force" in flags

    def test_parse_force_short_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, _positionals = parse_update_flags(["-f"])
        assert "-f" in flags

    def test_parse_yes_long_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, _positionals = parse_update_flags(["--yes"])
        assert "--yes" in flags

    def test_parse_yes_short_flag(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, _positionals = parse_update_flags(["-y"])
        assert "-y" in flags

    def test_parse_multiple_flags(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, positionals = parse_update_flags(["--check", "--force", "--yes"])
        assert flags == {"--check", "--force", "--yes"}
        assert positionals == []

    def test_parse_positionals(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, positionals = parse_update_flags(["--check", "extra"])
        assert "--check" in flags
        assert positionals == ["extra"]

    def test_parse_no_args(self) -> None:
        from kazma_cli.update import parse_update_flags

        flags, positionals = parse_update_flags([])
        assert flags == set()
        assert positionals == []


# ---------------------------------------------------------------------------
# Update command — version comparison
# ---------------------------------------------------------------------------

class TestVersionComparison:
    """parse_version and is_newer helpers."""

    def test_parse_version_simple(self) -> None:
        from kazma_cli.update import parse_version

        assert parse_version("0.1.0") == (0, 1, 0)

    def test_parse_version_multi_digit(self) -> None:
        from kazma_cli.update import parse_version

        assert parse_version("1.12.3") == (1, 12, 3)

    def test_parse_version_with_prerelease(self) -> None:
        from kazma_cli.update import parse_version

        # Pre-release suffixes are stripped
        assert parse_version("2.0.0a1") == (2, 0, 0)

    def test_parse_version_two_parts(self) -> None:
        from kazma_cli.update import parse_version

        assert parse_version("1.5") == (1, 5)

    def test_is_newer_true(self) -> None:
        from kazma_cli.update import is_newer

        assert is_newer("0.1.0", "0.2.0") is True

    def test_is_newer_false_equal(self) -> None:
        from kazma_cli.update import is_newer

        assert is_newer("0.1.0", "0.1.0") is False

    def test_is_newer_false_older(self) -> None:
        from kazma_cli.update import is_newer

        assert is_newer("1.0.0", "0.9.0") is False

    def test_is_newer_major_bump(self) -> None:
        from kazma_cli.update import is_newer

        assert is_newer("1.9.9", "2.0.0") is True

    def test_is_newer_patch_bump(self) -> None:
        from kazma_cli.update import is_newer

        assert is_newer("1.0.0", "1.0.1") is True


# ---------------------------------------------------------------------------
# Update command — install type detection (mocked subprocess)
# ---------------------------------------------------------------------------

def _make_completed_process(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> MagicMock:
    """Create a mock subprocess.CompletedProcess-like object."""
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class TestDetectInstallType:
    """detect_install_type with mocked pip show."""

    def test_detects_editable_install(self) -> None:
        from kazma_cli.update import detect_install_type

        pip_output = (
            "Name: kazma\n"
            "Version: 0.1.0\n"
            "Editable project location: /home/user/kazma\n"
        )
        with patch("kazma_cli.update._run_pip", return_value=_make_completed_process(stdout=pip_output)):
            assert detect_install_type() == "git"

    def test_detects_regular_pip_install(self) -> None:
        from kazma_cli.update import detect_install_type

        pip_output = "Name: kazma\nVersion: 0.1.0\nLocation: /usr/lib/python3.11/site-packages\n"
        with patch("kazma_cli.update._run_pip", return_value=_make_completed_process(stdout=pip_output)):
            assert detect_install_type() == "pip"

    def test_defaults_to_pip_on_failure(self) -> None:
        from kazma_cli.update import detect_install_type

        with patch("kazma_cli.update._run_pip", side_effect=Exception("pip not found")):
            assert detect_install_type() == "pip"

    def test_defaults_to_pip_on_nonzero_returncode(self) -> None:
        from kazma_cli.update import detect_install_type

        with patch(
            "kazma_cli.update._run_pip",
            return_value=_make_completed_process(returncode=1, stderr="not found"),
        ):
            assert detect_install_type() == "pip"


# ---------------------------------------------------------------------------
# Update command — PyPI version fetch (mocked httpx)
# ---------------------------------------------------------------------------

class TestPypiVersionFetch:
    """get_latest_pypi_version with mocked httpx."""

    def test_returns_version_on_success(self) -> None:
        from kazma_cli.update import get_latest_pypi_version

        mock_response = MagicMock()
        mock_response.json.return_value = {"info": {"version": "0.5.0"}}
        mock_response.raise_for_status = MagicMock()

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = mock_response

        with patch("httpx.Client", return_value=client):
            result = get_latest_pypi_version()
        assert result == "0.5.0"

    def test_returns_none_on_network_error(self) -> None:
        import httpx
        from kazma_cli.update import get_latest_pypi_version

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = httpx.ConnectError("no network")

        with patch("httpx.Client", return_value=client):
            result = get_latest_pypi_version()
        assert result is None

    def test_returns_none_on_missing_version_field(self) -> None:
        from kazma_cli.update import get_latest_pypi_version

        mock_response = MagicMock()
        mock_response.json.return_value = {"info": {}}
        mock_response.raise_for_status = MagicMock()

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.return_value = mock_response

        with patch("httpx.Client", return_value=client):
            result = get_latest_pypi_version()
        assert result is None


# ---------------------------------------------------------------------------
# Update command — current version detection
# ---------------------------------------------------------------------------

class TestGetCurrentVersion:
    """get_current_version with mocked importlib.metadata / pip show."""

    def test_uses_importlib_metadata(self) -> None:
        from kazma_cli.update import get_current_version

        with patch("importlib.metadata.version", return_value="0.3.0"):
            assert get_current_version() == "0.3.0"

    def test_falls_back_to_pip_show(self) -> None:
        from kazma_cli.update import get_current_version

        pip_output = "Name: kazma\nVersion: 0.4.0\n"
        with patch(
            "importlib.metadata.version", side_effect=Exception("not found")
        ), patch(
            "kazma_cli.update._run_pip",
            return_value=_make_completed_process(stdout=pip_output),
        ):
            assert get_current_version() == "0.4.0"

    def test_falls_back_to_banner_version(self) -> None:
        from kazma_cli.update import get_current_version

        with patch(
            "importlib.metadata.version", side_effect=Exception("not found")
        ), patch(
            "kazma_cli.update._run_pip", side_effect=Exception("pip fail")
        ), patch("kazma_cli.update._get_version", return_value="0.1.0"):
            assert get_current_version() == "0.1.0"


# ---------------------------------------------------------------------------
# Update command — git helpers (mocked subprocess)
# ---------------------------------------------------------------------------

class TestGitHelpers:
    """check_git_behind and get_git_commit with mocked subprocess."""

    def test_check_git_behind_no_commits(self) -> None:
        from kazma_cli.update import check_git_behind

        with patch("kazma_cli.update._find_git_root", return_value=Path("/fake/repo")), patch(
            "kazma_cli.update._run_cmd", return_value=_make_completed_process(stdout="")
        ):
            count, lines = check_git_behind()
        assert count == 0
        assert lines == []

    def test_check_git_behind_has_commits(self) -> None:
        from kazma_cli.update import check_git_behind

        git_log_output = "abc1234 Add feature\ndef5678 Fix bug\n"
        with patch("kazma_cli.update._find_git_root", return_value=Path("/fake/repo")), patch(
            "kazma_cli.update._run_cmd",
            return_value=_make_completed_process(stdout=git_log_output),
        ):
            count, lines = check_git_behind()
        assert count == 2
        assert "abc1234 Add feature" in lines

    def test_check_git_behind_no_git_root(self) -> None:
        from kazma_cli.update import check_git_behind

        with patch("kazma_cli.update._find_git_root", return_value=None):
            count, lines = check_git_behind()
        assert count == 0
        assert lines == []

    def test_get_git_commit_no_root(self) -> None:
        from kazma_cli.update import get_git_commit

        with patch("kazma_cli.update._find_git_root", return_value=None):
            assert get_git_commit() == "unknown"

    def test_get_git_commit_success(self) -> None:
        from kazma_cli.update import get_git_commit

        with patch("kazma_cli.update._find_git_root", return_value=Path("/fake/repo")), patch(
            "kazma_cli.update._run_cmd",
            return_value=_make_completed_process(stdout="abc1234\n"),
        ):
            assert get_git_commit("HEAD") == "abc1234"


# ---------------------------------------------------------------------------
# Update command — routing in main.py
# ---------------------------------------------------------------------------

class TestUpdateRouting:
    """Verify main() dispatches to the update handler."""

    def test_update_routing(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "update", "--check"]
            with patch("kazma_cli.update.run") as mock_run:
                main_mod.main()
            mock_run.assert_called_once()
            passed = mock_run.call_args.args[0]
            assert "--check" in passed
        finally:
            main_mod.sys.argv = original_argv

    def test_update_routing_no_flags(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "update"]
            with patch("kazma_cli.update.run") as mock_run:
                main_mod.main()
            mock_run.assert_called_once()
        finally:
            main_mod.sys.argv = original_argv

    def test_update_routing_force_flag(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "update", "--force"]
            with patch("kazma_cli.update.run") as mock_run:
                main_mod.main()
            passed = mock_run.call_args.args[0]
            assert "--force" in passed
        finally:
            main_mod.sys.argv = original_argv

    def test_update_routing_yes_flag(self) -> None:
        from kazma_cli import main as main_mod

        original_argv = list(main_mod.sys.argv)
        try:
            main_mod.sys.argv = ["kazma", "update", "--yes"]
            with patch("kazma_cli.update.run") as mock_run:
                main_mod.main()
            passed = mock_run.call_args.args[0]
            assert "--yes" in passed
        finally:
            main_mod.sys.argv = original_argv


# ---------------------------------------------------------------------------
# Update command — run() dispatch (sync entry points)
# ---------------------------------------------------------------------------

class TestUpdateRunDispatch:
    """Verify the update run() entry point dispatches correctly."""

    def test_update_run_help_no_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kazma_cli.update import run

        run(["--help"])
        out = capsys.readouterr().out
        assert "Update" in out

    def test_update_run_help_short_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        from kazma_cli.update import run

        run(["-h"])
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_update_run_pip_check_only_up_to_date(self) -> None:
        """With --check and already up to date, should not attempt install."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="pip"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.get_latest_pypi_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.do_pip_update"
        ) as mock_do_update:
            run(["--check"])
        mock_do_update.assert_not_called()

    def test_update_run_pip_check_only_update_available(self) -> None:
        """With --check and update available, should not install."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="pip"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.get_latest_pypi_version", return_value="0.2.0"
        ), patch(
            "kazma_cli.update.do_pip_update"
        ) as mock_do_update:
            run(["--check"])
        mock_do_update.assert_not_called()

    def test_update_run_pip_update_with_yes(self) -> None:
        """With --yes and update available, should install without prompting."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="pip"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.get_latest_pypi_version", return_value="0.2.0"
        ), patch(
            "kazma_cli.update.do_pip_update", return_value=True
        ) as mock_do_update:
            run(["--yes"])
        mock_do_update.assert_called_once()

    def test_update_run_pip_pypi_error_exits(self) -> None:
        """If PyPI fetch fails, run() should exit with code 1."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="pip"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch("kazma_cli.update.get_latest_pypi_version", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                run(["--check"])
        assert exc_info.value.code == 1

    def test_update_run_git_check_only(self) -> None:
        """With --check and git install, should not update."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="git"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.check_git_behind", return_value=(3, ["abc fix", "def feat"])
        ), patch("kazma_cli.update.get_git_commit", return_value="abc1234"), patch(
            "kazma_cli.update.do_git_update"
        ) as mock_do_update:
            run(["--check"])
        mock_do_update.assert_not_called()

    def test_update_run_git_update_with_yes(self) -> None:
        """With --yes and git install behind, should update without prompting."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="git"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.check_git_behind", return_value=(2, ["abc fix", "def feat"])
        ), patch("kazma_cli.update.get_git_commit", return_value="abc1234"), patch(
            "kazma_cli.update.do_git_update", return_value=True
        ) as mock_do_update:
            run(["--yes"])
        mock_do_update.assert_called_once()

    def test_update_run_git_already_up_to_date(self) -> None:
        """With git install and 0 commits behind, should say up to date."""
        from kazma_cli.update import run

        with patch("kazma_cli.update.detect_install_type", return_value="git"), patch(
            "kazma_cli.update.get_current_version", return_value="0.1.0"
        ), patch(
            "kazma_cli.update.check_git_behind", return_value=(0, [])
        ), patch("kazma_cli.update.get_git_commit", return_value="abc1234"), patch(
            "kazma_cli.update.do_git_update"
        ) as mock_do_update:
            run(["--yes"])
        mock_do_update.assert_not_called()


# ---------------------------------------------------------------------------
# Update command — completions
# ---------------------------------------------------------------------------

class TestUpdateCompletions:
    """Verify update is in completions SUBCMDS and scripts."""

    def test_update_in_subcmds(self) -> None:
        assert "update" in SUBCMDS

    def test_bash_script_has_update(self) -> None:
        from kazma_cli.completions import _bash_completion_script

        output = _bash_completion_script()
        assert "update" in output

    def test_zsh_script_has_update(self) -> None:
        from kazma_cli.completions import _zsh_completion_script

        output = _zsh_completion_script()
        assert "update[Check for and install CLI updates]" in output
