"""Regression tests for Wave 2 audit fixes (2026-09-04 audit):
- H1: Settings log tail bounding, async offloading, parameter validation
- H5: TUI async migration — AST guard forbidding sync request_json in async def
- M6: Semantic cache prompt serialization skipped when cache is disabled
- M7: HTTP pool threading.Lock released before awaiting aclose()
- M11: File write tool bounded at 8 MB and offloaded to asyncio.to_thread
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.http_pool import close_http_client
from kazma_core.settings_manager import SettingsManager
from kazma_core.tools.file_write import file_write
from kazma_ui.workspace_api import _scan_recent_files


# ── H1: SettingsManager Log Tail Bounding ───────────────────────────────────


class TestWave2H1LogTail:
    def test_bounded_tail_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """get_logs must only read the tail and clamp lines between 1 and 5000."""
        log_file = tmp_path / "test.log"
        # Write 2000 lines
        lines = [f"2026-09-04 12:00:{i:04d} Log message number {i}\n" for i in range(2000)]
        log_file.write_text("".join(lines), encoding="utf-8")

        monkeypatch.setenv("KAZMA_LOG_FILE", str(log_file))
        mgr = SettingsManager(config_store=get_config_store())

        # Tail 10 lines
        tail10 = mgr.get_logs(10)
        assert len(tail10["lines"]) == 10
        assert "Log message number 1999" in tail10["lines"][-1]
        assert "Log message number 1990" in tail10["lines"][0]

        # Tail 100 lines
        tail100 = mgr.get_logs(100)
        assert len(tail100["lines"]) == 100
        assert "Log message number 1999" in tail100["lines"][-1]

        # Clamped min (0 -> 1)
        tail_min = mgr.get_logs(0)
        assert len(tail_min["lines"]) == 1
        assert "Log message number 1999" in tail_min["lines"][0]

        # Clamped max (6000 -> 2000 available)
        tail_max = mgr.get_logs(6000)
        assert len(tail_max["lines"]) == 2000


# ── H1: Workspace Scan Recent Files ─────────────────────────────────────────


class TestWave2H1WorkspaceScan:
    def test_scan_recent_files(self, tmp_path: Path):
        """_scan_recent_files must sort by mtime and obey limit."""
        for i in range(10):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")

        scanned = _scan_recent_files(tmp_path, limit=5)
        assert len(scanned) == 5
        assert all("path" in item and "time" in item for item in scanned)


# ── M6: Semantic Cache Prompt Serialization ─────────────────────────────────


class TestWave2M6SemanticCache:
    def test_prompt_serialization_guarded(self):
        """Verify prompt serialization in LLMProvider.chat is guarded by cache_enabled."""
        import kazma_core.llm_provider as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        parsed = ast.parse(src)

        # Ensure json.dumps(messages, sort_keys=True) appears inside an If node checking cache_enabled
        found_inside_if = False
        for node in ast.walk(parsed):
            if isinstance(node, ast.If):
                # Check test condition mentions cache_enabled
                test_str = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
                if "cache_enabled" in test_str:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            call_str = ast.unparse(child) if hasattr(ast, "unparse") else ""
                            if "json.dumps" in call_str and "messages" in call_str:
                                found_inside_if = True
                                break
        assert found_inside_if, "json.dumps(messages) must be inside if cache_enabled"


# ── M7: HTTP Pool Lock Hygiene ──────────────────────────────────────────────


class TestWave2M7HttpPoolLock:
    @pytest.mark.asyncio
    async def test_close_http_client_releases_lock(self):
        """close_http_client must release _client_lock before awaiting client.aclose()."""
        import kazma_core.http_pool as hp

        mock_client = AsyncMock()
        lock_held_during_aclose = False

        async def check_lock():
            nonlocal lock_held_during_aclose
            # Try to acquire lock non-blocking; if acquired, lock was NOT held
            acquired = hp._client_lock.acquire(blocking=False)
            if not acquired:
                lock_held_during_aclose = True
            else:
                hp._client_lock.release()

        mock_client.aclose.side_effect = check_lock

        with hp._client_lock:
            hp._client = mock_client

        await close_http_client()
        assert not lock_held_during_aclose, "Lock was held across aclose() await point!"
        assert hp._client is None


# ── M11: File Write Tool ────────────────────────────────────────────────────


class TestWave2M11FileWrite:
    @pytest.mark.asyncio
    async def test_file_write_success(self, tmp_path: Path):
        """file_write writes content asynchronously within the workspace."""
        target = tmp_path / "hello.txt"
        with patch("kazma_core.tools.file_write.resolve_active_root", return_value=tmp_path):
            res = await file_write(str(target), "Hello World\nLine 2")
            assert "Wrote 2 lines" in res
            assert target.read_text(encoding="utf-8") == "Hello World\nLine 2"

    @pytest.mark.asyncio
    async def test_file_write_caps_8mb(self, tmp_path: Path):
        """file_write rejects content exceeding 8 MB with an actionable error."""
        target = tmp_path / "oversize.txt"
        oversized = "x" * (8 * 1024 * 1024 + 1)
        with patch("kazma_core.tools.file_write.resolve_active_root", return_value=tmp_path):
            res = await file_write(str(target), oversized)
            assert "Error: Content exceeds maximum write limit of 8 MB" in res
            assert not target.exists()


# ── H5: TUI Async AST Guard ────────────────────────────────────────────────


class TestWave2H5TuiAsyncAstGuard:
    def test_no_sync_request_json_in_async_def(self):
        """Guard against regression: no async def in kazma-tui may call sync request_json."""
        tui_dir = Path(__file__).resolve().parent.parent / "kazma-tui" / "kazma_tui"
        assert tui_dir.is_dir(), f"TUI dir not found: {tui_dir}"

        violations: list[str] = []

        for py_file in tui_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except Exception as exc:
                violations.append(f"{py_file.name}: failed to parse ({exc})")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            func_name = ""
                            if isinstance(sub.func, ast.Name):
                                func_name = sub.func.id
                            elif isinstance(sub.func, ast.Attribute):
                                func_name = sub.func.attr
                            if func_name == "request_json":
                                violations.append(
                                    f"{py_file.name}:{sub.lineno} inside async def {node.name}() "
                                    "calls sync request_json()"
                                )

        assert not violations, "Found sync request_json inside async def in kazma-tui:\n" + "\n".join(violations)
