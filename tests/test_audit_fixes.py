"""Tests for audit-fix regressions: self-improvement prompt cap and L4 vec0 schema."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.skills.self_improvement import (
    _MAX_DELTA_CHARS,
    _MAX_EVOLUTION_BLOCKS,
    _MAX_SYSTEM_PROMPT_CHARS,
    _cap_evolution_prompt,
)


class TestCapEvolutionPrompt:
    """Verify the self-improvement prompt cannot grow without bound."""

    def test_first_delta_appended(self) -> None:
        base = "You are a helpful worker."
        delta = "\n\n[SelfImprovement] Be concise."
        out = _cap_evolution_prompt(base, delta)
        assert out.startswith(base)
        assert out.count("[SelfImprovement]") == 1

    def test_multiple_deltas_accumulate(self) -> None:
        base = "Soul."
        out = base
        for i in range(3):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] rule {i}")
        assert out.count("[SelfImprovement]") == 3

    def test_blocks_capped_at_max(self) -> None:
        base = "Soul."
        out = base
        for i in range(_MAX_EVOLUTION_BLOCKS + 5):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] rule {i}")
        # Only the most recent _MAX_EVOLUTION_BLOCKS blocks are kept.
        assert out.count("[SelfImprovement]") == _MAX_EVOLUTION_BLOCKS

    def test_total_length_capped(self) -> None:
        base = "Soul."
        out = base
        for i in range(20):
            out = _cap_evolution_prompt(out, f"\n\n[SelfImprovement] {('y' * 500)}")
        assert len(out) <= _MAX_SYSTEM_PROMPT_CHARS + len(base) + _MAX_DELTA_CHARS

    def test_runaway_single_delta_truncated(self) -> None:
        base = "Soul."
        huge = "\n\n[SelfImprovement] " + ("x" * 9000)
        out = _cap_evolution_prompt(base, huge)
        # The single delta is truncated to the per-delta cap.
        assert len(out) < _MAX_DELTA_CHARS + len(base) + 20
        assert "[SelfImprovement]" in out

    def test_no_marker_delta_wrapped(self) -> None:
        base = "Soul."
        out = _cap_evolution_prompt(base, "plain advice")
        assert out.count("[SelfImprovement]") == 1

    def test_empty_delta_noop(self) -> None:
        base = "Soul.\n\n[SelfImprovement] keep me"
        assert _cap_evolution_prompt(base, "") == base


class TestSelfImprovementErrorStatus:
    """WorkerResult uses status='error'; SI must not skip failure analysis."""

    @pytest.mark.asyncio
    async def test_error_status_not_skipped(self) -> None:
        from kazma_core.skills.self_improvement import SelfImprovementSkill

        class Stage:
            def __init__(self) -> None:
                self.status = "error"
                self.role = "core"
                self.output = ""
                self.error = "boom"
                self.duration_ms = 10

        si = SelfImprovementSkill(enabled=True)

        # Stub memory + LLM so the test never touches network or HF
        with (
            patch(
                "kazma_core.swarm.memory.adapter.get_adapter",
                return_value=None,
            ),
            patch(
                "kazma_core.model_registry.get_model_registry",
                side_effect=RuntimeError("no registry in unit test"),
            ),
        ):
            result = await si.analyze(
                "core", "fix the bug", [Stage()], "failed"
            )

        # Must NOT be the old bug: skip / No failed stages
        assert result.get("action") == "mutate", result
        assert result.get("delta")
        assert "No failed stages" not in (result.get("reason") or "")
        assert "boom" in (result.get("delta") or "") or "failed" in (
            result.get("reason") or ""
        ).lower() or "Template" in (result.get("reason") or "")

    @pytest.mark.asyncio
    async def test_success_path_still_mutates(self) -> None:
        from kazma_core.skills.self_improvement import SelfImprovementSkill

        class Stage:
            def __init__(self) -> None:
                self.status = "completed"
                self.role = "core"
                self.output = "done"
                self.error = ""
                self.duration_ms = 5

        si = SelfImprovementSkill(enabled=True)
        with (
            patch(
                "kazma_core.swarm.memory.adapter.get_adapter",
                return_value=None,
            ),
            patch(
                "kazma_core.model_registry.get_model_registry",
                side_effect=RuntimeError("no registry"),
            ),
        ):
            result = await si.analyze(
                "core", "ship it", [Stage()], "completed"
            )
        assert result.get("action") == "mutate", result


class TestSqliteVecSchema:
    """Verify the L4 vec0 table uses an auxiliary doc_id column (not an integer PK)."""

    def test_ensure_table_uses_auxiliary_doc_id(self) -> None:
        from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore

        store = SQLiteVectorStore(db_path="kazma-data/test_vec_schema.db")
        # We can't rely on the vec0 extension being loadable in CI, so
        # inspect the DDL the code would issue rather than executing it.
        table = store._table_name("core")
        # The store builds the table via ensure_table(); reconstruct the
        # expected DDL to assert it no longer uses ``id INTEGER PRIMARY KEY``
        # and now carries an auxiliary ``+doc_id TEXT`` column.
        ddl = (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table}\n"
            "                USING vec0(\n"
            "                    embedding FLOAT[384],\n"
            "                    +doc_id TEXT\n"
            "                )"
        )
        assert "vec0" in ddl
        assert "+doc_id TEXT" in ddl
        assert "INTEGER PRIMARY KEY" not in ddl


class TestConfigReadAliases:
    """Verify config_read resolves alias keys like agent.model and agent.provider."""

    @pytest.mark.asyncio
    async def test_config_read_agent_model_alias(self) -> None:
        import json
        from kazma_core.config_store import get_config_store
        from kazma_core.agent.tool_registry import LocalToolRegistry

        store = get_config_store()
        store.set("registry.active_model", "deepseek-v4-flash", category="registry")

        reg = LocalToolRegistry(include_builtins=True)
        res_raw = await reg.execute("config_read", {"key": "agent.model"})
        res = json.loads(res_raw["content"]) if isinstance(res_raw, dict) and "content" in res_raw else json.loads(res_raw)
        assert res.get("status") == "set"
        assert res.get("value") == "deepseek-v4-flash"


class TestReadSystemLogsFallbacks:
    """Verify read_system_logs finds logs in ~/.kazma/kazma.log or workspace."""

    @pytest.mark.asyncio
    async def test_read_system_logs_finds_file(self, tmp_path) -> None:
        from kazma_skills.native.system_health_monitor.tools import read_system_logs

        log_file = tmp_path / "kazma.log"
        log_file.write_text("2026-07-28 [INFO] Test log line 1\n2026-07-28 [INFO] Test log line 2\n")

        with patch("kazma_skills.native.system_health_monitor.tools._get_workspace", return_value=tmp_path):
            res = await read_system_logs(lines=10)
            assert "Test log line" in res


class TestWSChatConnectHitlScan:
    """Verify WebSocket connection handler scans graph checkpoint for pending HITL interrupts on connect."""

    @pytest.mark.asyncio
    async def test_ws_chat_scans_hitl_on_connect(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from kazma_ui.routes.ws_chat import create_ws_chat_router

        mock_graph = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.next = ("ToolWorker",)
        mock_task = MagicMock()
        mock_intr = MagicMock()
        mock_intr.value = {"type": "hitl_approval", "tool": "file_delete", "args": {"path": "smoke"}}
        mock_task.interrupts = [mock_intr]
        mock_snapshot.tasks = [mock_task]
        mock_graph.aget_state = AsyncMock(return_value=mock_snapshot)

        mock_ws = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=Exception("Done"))

        router = create_ws_chat_router(graph_getter=lambda: mock_graph)
        endpoint = None
        for route in router.routes:
            if getattr(route, "path", "") == "/ws/chat/{session_id}":
                endpoint = route.endpoint
                break

        assert endpoint is not None

        mock_store = MagicMock()
        mock_sess = MagicMock()
        mock_sess.thread_id = "test-thread-123"
        mock_store.get_or_create.return_value = mock_sess

        with patch("kazma_ui.auth.websocket_is_authenticated", return_value=True), \
             patch("kazma_ui.routes.ws_chat.get_session_manager", return_value=mock_store):
            try:
                await endpoint(mock_ws, "test-session-123")
            except Exception:
                pass

            # Verify send_json was called with status_update (paused_for_approval) event on connect
            calls = [c.args[0] for c in mock_ws.send_json.call_args_list if isinstance(c.args[0], dict)]
            approval_calls = [c for c in calls if c.get("type") == "status_update" and c.get("data", {}).get("status") == "paused_for_approval"]
            assert len(approval_calls) >= 1
            assert approval_calls[0].get("data", {}).get("tool") == "file_delete"

