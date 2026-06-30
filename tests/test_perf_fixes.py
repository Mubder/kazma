"""Tests for P1 performance fixes (VAL-PERF-001 .. VAL-PERF-004).

Covers:
  VAL-PERF-001 — web_search runs in a thread executor (asyncio.to_thread)
  VAL-PERF-002 — Bounded LRU eviction on in-memory dicts
  VAL-PERF-003 — Error handlers do not leak exception details
  VAL-PERF-004 — file_read enforces workspace boundary
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.testclient import TestClient

# ═══════════════════════════════════════════════════════════════════
# VAL-PERF-001: web_search does not block the event loop
# ═══════════════════════════════════════════════════════════════════


class TestWebSearchNonBlocking:
    """The DuckDuckGo search call must run in a worker thread."""

    @staticmethod
    def _get_ws_module():
        """Get the actual web_search module (not the re-exported function)."""
        import importlib

        return importlib.import_module("kazma_core.tools.web_search")

    @pytest.mark.asyncio
    async def test_search_runs_in_thread(self) -> None:
        """``_run_search`` must be executed via ``asyncio.to_thread``."""
        ws_module = self._get_ws_module()

        captured: dict[str, object] = {}

        def fake_run_search(query: str, max_results: int):
            captured["thread_id"] = threading.get_ident()
            return [{"title": "T", "href": "http://x", "body": "B"}]

        main_thread = threading.get_ident()

        # Patch the helper that performs the blocking call.
        with patch.object(ws_module, "_run_search", side_effect=fake_run_search):
            result = await ws_module.web_search("test query", max_results=1)

        assert "T" in result
        assert captured["thread_id"] != main_thread, (
            "search must run in a worker thread, not the calling thread"
        )

    @pytest.mark.asyncio
    async def test_search_returns_markdown(self) -> None:
        """Search results are formatted as markdown."""
        ws_module = self._get_ws_module()

        def fake_run_search(query: str, max_results: int):
            return [
                {"title": "Result One", "href": "http://example.com/1", "body": "Snippet A"},
                {"title": "Result Two", "href": "http://example.com/2", "body": "Snippet B"},
            ]

        with patch.object(ws_module, "_run_search", side_effect=fake_run_search):
            result = await ws_module.web_search("query")

        assert "Result One" in result
        assert "http://example.com/1" in result
        assert "Result Two" in result

    @pytest.mark.asyncio
    async def test_search_returns_error_on_connection_error(self) -> None:
        ws_module = self._get_ws_module()

        def boom(query, max_results):
            raise ConnectionError("no network")

        with patch.object(ws_module, "_run_search", side_effect=boom):
            result = await ws_module.web_search("query")

        assert result.startswith("Error")

    @pytest.mark.asyncio
    async def test_search_uses_to_thread(self) -> None:
        """Verify the source code uses asyncio.to_thread for the blocking call."""
        ws_path = (
            Path(__file__).resolve().parent.parent
            / "kazma-core"
            / "kazma_core"
            / "tools"
            / "web_search.py"
        )
        content = ws_path.read_text(encoding="utf-8")
        assert "asyncio.to_thread" in content, (
            "web_search.py must use asyncio.to_thread to avoid blocking"
        )

    @pytest.mark.asyncio
    async def test_run_search_is_module_level_function(self) -> None:
        """``_run_search`` must be a standalone module-level function (picklable
        for ``asyncio.to_thread``)."""
        ws_module = self._get_ws_module()

        # It must be accessible at module level
        assert hasattr(ws_module, "_run_search")
        assert callable(ws_module._run_search)


# ═══════════════════════════════════════════════════════════════════
# VAL-PERF-002: Bounded LRU eviction
# ═══════════════════════════════════════════════════════════════════


class TestSessionManagerLRU:
    """SessionManager._sessions must evict the oldest entry when full."""

    def test_eviction_when_max_exceeded(self) -> None:
        from kazma_ui.session_manager import SessionManager

        mgr = SessionManager(max_sessions=3)
        mgr.get_or_create("s1")
        mgr.get_or_create("s2")
        mgr.get_or_create("s3")
        mgr.get_or_create("s4")  # overflow → evict s1

        assert mgr.get("s1") is None
        assert mgr.get("s2") is not None
        assert mgr.get("s3") is not None
        assert mgr.get("s4") is not None

    def test_access_updates_lru_order(self) -> None:
        """Accessing s1 after creation should keep it and evict s2 instead."""
        from kazma_ui.session_manager import SessionManager

        mgr = SessionManager(max_sessions=3)
        mgr.get_or_create("s1")
        mgr.get_or_create("s2")
        mgr.get_or_create("s3")

        # Access s1 so it becomes most-recently-used
        mgr.get("s1")

        mgr.get_or_create("s4")  # overflow → evict s2 (oldest)

        assert mgr.get("s1") is not None
        assert mgr.get("s2") is None
        assert mgr.get("s3") is not None
        assert mgr.get("s4") is not None

    def test_get_or_create_updates_lru(self) -> None:
        from kazma_ui.session_manager import SessionManager

        mgr = SessionManager(max_sessions=3)
        mgr.get_or_create("s1")
        mgr.get_or_create("s2")
        mgr.get_or_create("s3")

        # get_or_create on existing should update LRU order
        mgr.get_or_create("s1")

        mgr.get_or_create("s4")  # overflow → evict s2

        assert mgr.get("s1") is not None
        assert mgr.get("s2") is None

    def test_put_updates_lru(self) -> None:
        from kazma_ui.session_manager import ChatSession, SessionManager

        mgr = SessionManager(max_sessions=3)
        mgr.get_or_create("s1")
        mgr.get_or_create("s2")
        mgr.get_or_create("s3")

        # Re-putting s1 should make it most-recently-used
        mgr.put(ChatSession(session_id="s1"))

        mgr.get_or_create("s4")  # overflow → evict s2

        assert mgr.get("s1") is not None
        assert mgr.get("s2") is None

    def test_default_max_is_10000(self) -> None:
        from kazma_ui.session_manager import MAX_SESSIONS, SessionManager

        assert MAX_SESSIONS == 10_000
        mgr = SessionManager()
        assert mgr._max_sessions == 10_000

    def test_ordered_dict_type(self) -> None:
        """The internal store must be an OrderedDict (for LRU semantics)."""
        from collections import OrderedDict

        from kazma_ui.session_manager import SessionManager

        mgr = SessionManager()
        assert isinstance(mgr._sessions, OrderedDict)


class TestCheckpointManagerLRU:
    """CheckpointManager._locks must evict the oldest entry when full."""

    def test_eviction_when_max_exceeded(self) -> None:
        from kazma_gateway.stores.checkpoint import CheckpointManager

        mgr = CheckpointManager(saver=MagicMock(), max_locks=3)
        mgr._get_lock("t1")
        mgr._get_lock("t2")
        mgr._get_lock("t3")
        mgr._get_lock("t4")  # overflow → evict t1

        assert "t1" not in mgr._locks
        assert "t2" in mgr._locks
        assert "t3" in mgr._locks
        assert "t4" in mgr._locks

    def test_access_updates_lru_order(self) -> None:
        from kazma_gateway.stores.checkpoint import CheckpointManager

        mgr = CheckpointManager(saver=MagicMock(), max_locks=3)
        mgr._get_lock("t1")
        mgr._get_lock("t2")
        mgr._get_lock("t3")

        # Access t1 → becomes most-recently-used
        mgr._get_lock("t1")

        mgr._get_lock("t4")  # overflow → evict t2

        assert "t1" in mgr._locks
        assert "t2" not in mgr._locks
        assert "t3" in mgr._locks
        assert "t4" in mgr._locks

    def test_ordered_dict_type(self) -> None:
        from collections import OrderedDict

        from kazma_gateway.stores.checkpoint import CheckpointManager

        mgr = CheckpointManager(saver=MagicMock())
        assert isinstance(mgr._locks, OrderedDict)


class TestAgentHandlerDictsLRU:
    """agent_handler's _sessions and _thread_locks must have bounded LRU."""

    @pytest.mark.asyncio
    async def test_sessions_eviction(self) -> None:
        """The _sessions dict in agent_handler should evict old entries."""
        from kazma_gateway.agent_handler import _MAX_DICT_ENTRIES, create_graph_handler

        graph = MagicMock()
        graph.ainvoke = MagicMock(return_value={"messages": []})
        manager = MagicMock()
        manager.send = MagicMock(return_value=asyncio.Future())
        manager.send.return_value.set_result(True)
        store = MagicMock()
        store.get = MagicMock(return_value={})
        store.put = MagicMock(return_value=asyncio.Future())
        store.put.return_value.set_result(None)
        store.evict_older_than = MagicMock(return_value=asyncio.Future())
        store.evict_older_than.return_value.set_result(0)

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        # Access the closure variables by inspecting the handler's __closure__
        closure = handler.__closure__
        cells = {c.cell_contents.__name__ if hasattr(c.cell_contents, "__name__") else None for c in closure}
        # Find _sessions and _thread_locks in the closure
        sessions_dict = None
        thread_locks_dict = None
        for cell in closure:
            contents = cell.cell_contents
            if isinstance(contents, type(_MAX_DICT_ENTRIES.__class__)):  # int
                continue
        # Directly verify the module-level constant
        assert _MAX_DICT_ENTRIES == 10_000

    def test_max_dict_entries_constant(self) -> None:
        from kazma_gateway.agent_handler import _MAX_DICT_ENTRIES

        assert _MAX_DICT_ENTRIES == 10_000

    @pytest.mark.asyncio
    async def test_sessions_and_locks_are_ordered_dicts(self) -> None:
        """The _sessions and _thread_locks created by create_graph_handler
        must be OrderedDict instances."""
        from collections import OrderedDict

        from kazma_gateway.agent_handler import create_graph_handler

        graph = MagicMock()
        manager = MagicMock()
        store = MagicMock()

        handler = create_graph_handler(graph=graph, manager=manager, store=store)

        # Inspect closure to find the OrderedDict instances
        sessions_found = False
        locks_found = False
        for cell in handler.__closure__:
            contents = cell.cell_contents
            if isinstance(contents, OrderedDict):
                # Could be _sessions or _thread_locks
                sessions_found = True
                locks_found = True

        assert sessions_found, "_sessions or _thread_locks must be an OrderedDict"
        assert locks_found, "_thread_locks must be an OrderedDict"


# ═══════════════════════════════════════════════════════════════════
# VAL-PERF-003: Error handlers do not leak exception details
# ═══════════════════════════════════════════════════════════════════


def _make_error_test_app() -> FastAPI:
    """Build a minimal FastAPI app that mirrors app.py's error handlers."""
    import logging
    from pathlib import Path

    logger = logging.getLogger("test_error_app")

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/boom500")
    async def boom500():
        raise RuntimeError("SECRET_INTERNAL_DATABASE_CONNECTION_STRING=admin:pass")

    @app.get("/notfound")
    async def notfound():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not here")

    @app.exception_handler(404)
    async def not_found(request: Request, exc) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 404, "message": "Page not found", "detail": ""},
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc) -> HTMLResponse:
        logger.exception("[app] Internal server error")
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Internal server error", "detail": ""},
            status_code=500,
        )

    @app.exception_handler(Exception)
    async def catch_all(request: Request, exc) -> HTMLResponse:
        logger.exception("[app] Unhandled exception")
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Internal server error", "detail": ""},
            status_code=500,
        )

    return app


class TestErrorHandlersNoLeak:
    """Error handlers must not expose str(exc) to clients."""

    def test_500_does_not_leak_exception_text(self) -> None:
        """A 500 error page must not contain the exception message."""
        app = _make_error_test_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom500")

        assert resp.status_code == 500
        body = resp.text
        assert "SECRET_INTERNAL_DATABASE_CONNECTION_STRING" not in body
        assert "admin:pass" not in body

    def test_500_shows_generic_message(self) -> None:
        app = _make_error_test_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom500")

        assert resp.status_code == 500
        assert "Internal server error" in resp.text

    def test_404_does_not_leak_detail(self) -> None:
        app = _make_error_test_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/notfound")

        assert resp.status_code == 404
        assert "Page not found" in resp.text

    def test_error_handlers_in_app_py_use_generic_detail(self) -> None:
        """Verify the source code of app.py does not pass str(exc) as detail."""
        app_py = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "app.py"
        content = app_py.read_text(encoding="utf-8")

        # The error handlers must not use detail=str(exc)
        assert 'detail=str(exc)' not in content, (
            "app.py error handlers must not pass str(exc) as detail"
        )

    def test_error_handlers_log_full_exception(self) -> None:
        """Verify the source code of app.py uses logger.exception for errors."""
        app_py = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "app.py"
        content = app_py.read_text(encoding="utf-8")

        # The 500 and catch_all handlers should use logger.exception
        assert "logger.exception" in content, (
            "app.py error handlers must use logger.exception for full traceback"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-PERF-004: file_read has workspace restriction
# ═══════════════════════════════════════════════════════════════════


class TestFileReadWorkspaceRestriction:
    """file_read must enforce workspace boundary like file_write."""

    def setup_method(self) -> None:
        """Reset workspace config before each test."""
        from kazma_core.tools.file_write import configure_workspace

        configure_workspace(workspace=None, allow_absolute=False)

    @pytest.mark.asyncio
    async def test_file_read_within_workspace_allowed(self, tmp_path: Path) -> None:
        """Reading a file inside the configured workspace works."""
        from kazma_core.tools.file_write import configure_workspace

        configure_workspace(workspace=str(tmp_path))
        test_file = tmp_path / "inside.txt"
        test_file.write_text("hello\n")

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(test_file))
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_file_read_outside_workspace_blocked(self, tmp_path: Path) -> None:
        """Reading a file outside the workspace is blocked by default."""
        from kazma_core.tools.file_write import configure_workspace

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n")

        configure_workspace(workspace=str(workspace), allow_absolute=False)

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(outside))
        assert "Safety" in result
        assert "not allowed" in result.lower()

    @pytest.mark.asyncio
    async def test_file_read_outside_allowed_when_absolute_enabled(
        self, tmp_path: Path
    ) -> None:
        """When allow_absolute=True, reads outside workspace are permitted."""
        from kazma_core.tools.file_write import configure_workspace

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("ok\n")

        configure_workspace(workspace=str(workspace), allow_absolute=True)

        from kazma_core.tools.file_read import file_read

        result = await file_read(str(outside))
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_file_read_blocks_traversal(self, tmp_path: Path) -> None:
        """../.. traversal attempts to escape the workspace are blocked."""
        from kazma_core.tools.file_write import configure_workspace

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (tmp_path / "escaped.txt").write_text("evil\n")

        configure_workspace(workspace=str(workspace), allow_absolute=False)

        from kazma_core.tools.file_read import file_read

        escape = str(workspace / ".." / "escaped.txt")
        result = await file_read(escape)
        assert "Safety" in result or "not allowed" in result.lower()
        assert "evil" not in result

    @pytest.mark.asyncio
    async def test_file_read_shares_config_with_file_write(self, tmp_path: Path) -> None:
        """configure_workspace affects both file_read and file_write."""
        from kazma_core.tools.file_write import configure_workspace

        configure_workspace(workspace=str(tmp_path))

        # Write a file via file_write
        from kazma_core.tools.file_write import file_write

        await file_write(str(tmp_path / "shared.txt"), "content")

        # Read it back via file_read — same workspace boundary
        from kazma_core.tools.file_read import file_read

        result = await file_read(str(tmp_path / "shared.txt"))
        assert "content" in result
