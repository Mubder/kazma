"""Tests for the event-loop / performance fix round (2026-08-27).

Covers:
  1. env_context — ``build_env_context`` is now an async facade over the
     blocking ``_build_env_context_sync`` (git subprocess probes must run
     off the event loop); outputs are identical.
  2. /health/deep — every blocking check runs via ``asyncio.to_thread``
     (the handler must never block the loop); structure unchanged.
  3. time_travel — snapshots DB path resolves through
     ``kazma_core.paths`` (data-dir anchored, CWD-independent): two
     processes started from different working directories write the SAME
     file, and maintenance defaults to it.
  4. file_read — bounded byte budget (no full-file read of oversized
     files), access/grant validation happens BEFORE serving cached reads,
     and ``clear_read_cache()`` is exported.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════
# 1. env_context: async facade equals sync output
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def isolated_ws(tmp_path: Path, monkeypatch):
    """Isolated workspace with an ACTIVE WorkspaceStore row pinned to tmp.

    Mirrors kazma-core/tests/test_env_context.py: an empty WorkspaceStore
    auto-seeds "Default Workspace" at cwd, so env vars alone do not move
    the binding — an explicit active row does.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(ws))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

    import kazma_core.stores.workspaces as wsmod
    from kazma_core.stores import reset_workspace_store
    from kazma_core.tools.file_write import configure_workspace

    reset_workspace_store()
    store = wsmod.WorkspaceStore(str(tmp_path / "settings.db"))
    wsmod._workspace_store = store
    rec = store.create_workspace("test-ws", str(ws))
    store.set_active_workspace(rec["id"])
    configure_workspace(workspace=str(ws))
    yield ws
    store.close()
    reset_workspace_store()
    configure_workspace(workspace=None)


async def test_env_context_async_matches_sync(isolated_ws):
    """Awaited build_env_context() output is identical to the sync builder."""
    from kazma_core.ide.env_context import (
        _build_env_context_sync,
        build_env_context,
    )

    coro = build_env_context()
    assert asyncio.iscoroutine(coro), "build_env_context must be awaitable"
    result = await coro
    assert isinstance(result, str) and result.strip()

    sync_result = _build_env_context_sync()
    assert result == sync_result
    assert str(isolated_ws) in result  # graceful degradation names the root


async def test_env_context_async_workspace_id_passthrough(tmp_path, monkeypatch):
    """The workspace_id kwarg flows through to the blocking builder."""
    ws_a = tmp_path / "repoA"
    ws_b = tmp_path / "repoB"
    ws_a.mkdir()
    ws_b.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

    from kazma_core.ide.env_context import (
        _build_env_context_sync,
        build_env_context,
    )
    import kazma_core.stores.workspaces as wsmod
    from kazma_core.stores import reset_workspace_store

    reset_workspace_store()
    store = wsmod.WorkspaceStore(str(tmp_path / "settings.db"))
    wsmod._workspace_store = store
    rec_b = store.create_workspace("Repo B", str(ws_b))
    store.set_active_workspace(rec_b["id"])
    try:
        async_block = await build_env_context(workspace_id=rec_b["id"])
        sync_block = _build_env_context_sync(workspace_id=rec_b["id"])
        assert async_block == sync_block
        assert str(ws_b) in async_block
    finally:
        store.close()
        reset_workspace_store()


# ═══════════════════════════════════════════════════════════════════
# 2. /health/deep: blocking checks execute off the event loop
# ═══════════════════════════════════════════════════════════════════


def _make_recalls_ok():
    """A no-op async memory_recall replacement (coroutine-returning lambda)."""
    async def recall_ok():
        return {"status": "ok", "component": "memory_recall"}

    def _fn():
        return recall_ok()

    return _fn


def test_deep_canary_checks_run_offloop(monkeypatch):
    """Every sync deep check executes inside a worker thread (to_thread).

    Pragmatic non-blocking proof: monkeypatch each check with a stub that
    records its thread ident, drive the coroutine directly with
    asyncio.run, and compare against the thread running the loop.
    """
    from kazma_ui import health as health_mod

    health_mod._deep_cache.update(ts=0.0, payload=None)
    main_ident = threading.get_ident()
    seen: dict[str, int] = {}

    def make_sync(name):
        def _fn():
            seen[name] = threading.get_ident()
            return {"status": "ok", "component": name}

        return _fn

    def memory_recall_stub():
        coro_ok = {"status": "ok", "component": "memory_recall"}

        async def _inner():
            seen["memory_recall"] = threading.get_ident()
            return coro_ok

        return _inner()

    monkeypatch.setattr(health_mod, "_check_config_roundtrip", make_sync("config_roundtrip"))
    monkeypatch.setattr(health_mod, "_check_memory_recall", memory_recall_stub)
    monkeypatch.setattr(health_mod, "_check_workspace_binding", make_sync("workspace_binding"))
    monkeypatch.setattr(health_mod, "_check_research_stack", make_sync("research_stack"))
    monkeypatch.setattr(health_mod, "_check_brain_imports", make_sync("brain_imports"))
    monkeypatch.setattr(health_mod, "check_database", make_sync("database"))

    resp = asyncio.run(health_mod.deep_canary())

    import json as _json

    data = _json.loads(resp.body)
    assert resp.status_code == 200
    for name in (
        "config_roundtrip",
        "workspace_binding",
        "research_stack",
        "brain_imports",
        "database",
    ):
        assert name in data["checks"], f"missing check {name}"
        assert data["checks"][name]["status"] == "ok"
        # Sync checks were wrapped in asyncio.to_thread → NOT the loop thread.
        assert seen[name] != main_ident, f"{name} ran on the event-loop thread"
    # The async recall check runs ON the loop thread by design.
    assert data["checks"]["memory_recall"]["status"] == "ok"
    assert seen["memory_recall"] == main_ident
    # Cache is populated for TTL reuse.
    assert health_mod._deep_cache["payload"]["status"] == "healthy"


def test_deep_canary_structure_unchanged(monkeypatch):
    """Same six keys in the same order; failed checks still 503."""
    from kazma_ui import health as health_mod

    health_mod._deep_cache.update(ts=0.0, payload=None)

    def fail_binding():
        return {"status": "failed", "component": "workspace_binding", "error": "boom"}

    monkeypatch.setattr(
        health_mod, "_check_config_roundtrip", lambda: {"status": "ok", "component": "config_roundtrip"}
    )
    monkeypatch.setattr(health_mod, "_check_memory_recall", _make_recalls_ok())
    monkeypatch.setattr(health_mod, "_check_workspace_binding", fail_binding)
    monkeypatch.setattr(
        health_mod, "_check_research_stack", lambda: {"status": "degraded", "component": "research_stack"}
    )
    monkeypatch.setattr(
        health_mod, "_check_brain_imports", lambda: {"status": "ok", "component": "brain_imports"}
    )
    monkeypatch.setattr(
        health_mod, "check_database", lambda: {"status": "ok", "component": "database"}
    )

    resp = asyncio.run(health_mod.deep_canary())
    import json as _json

    data = _json.loads(resp.body)
    assert list(data["checks"].keys()) == [
        "config_roundtrip",
        "memory_recall",
        "workspace_binding",
        "research_stack",
        "brain_imports",
        "database",
    ]
    assert data["failed"] == ["workspace_binding"]
    assert resp.status_code == 503
    assert data["status"] == "unhealthy"
    health_mod._deep_cache.update(ts=0.0, payload=None)


# ═══════════════════════════════════════════════════════════════════
# 3. time_travel: data-dir-anchored snapshot DB (H19)
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotPathDeterminism:
    def test_default_recorder_path_is_data_dir(self, tmp_path, monkeypatch):
        """SnapshotRecorder() default resolves under KAZMA_DATA_DIR."""
        from kazma_core.time_travel import SnapshotRecorder

        monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
        rec = SnapshotRecorder(enabled=True)
        assert Path(rec.db_path) == tmp_path / "snapshots.db"

    def test_legacy_relative_literal_is_reanchored(self, tmp_path, monkeypatch):
        """The shipped kazma.yaml literal normalizes into the data dir."""
        from kazma_core.time_travel import SnapshotRecorder

        monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
        rec = SnapshotRecorder(enabled=True, db_path="kazma-data/snapshots.db")
        assert Path(rec.db_path).is_absolute()
        assert Path(rec.db_path) == tmp_path / "snapshots.db"

    def test_absolute_override_wins(self, tmp_path, monkeypatch):
        from kazma_core.time_travel import SnapshotStore

        monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
        explicit = tmp_path / "elsewhere" / "snap.db"
        store = SnapshotStore(db_path=explicit)
        assert Path(store.db_path) == explicit
        store.close()

    def test_two_processes_different_cwd_share_one_file(self, tmp_path):
        """Runtime proof: two interpreters from different cwds write the
        SAME snapshots.db (via KAZMA_DATA_DIR — like production layout
        overrides)."""
        data_dir = tmp_path / "data"
        cwd_a = tmp_path / "a"
        cwd_b = tmp_path / "b"
        for d in (data_dir, cwd_a, cwd_b):
            d.mkdir(parents=True)

        script = "\n".join(
            [
                "import sys",
                "from kazma_core.time_travel import SnapshotRecorder",
                f"thread = sys.argv[1]",
                "r = SnapshotRecorder(enabled=True)",
                'r.capture({"thread_id": thread, "iteration": 3})',
                "print(r.db_path)",
            ]
        )
        script_file = tmp_path / "capture_snapshot.py"
        script_file.write_text(script, encoding="utf-8")

        env = os.environ.copy()
        env["KAZMA_DATA_DIR"] = str(data_dir)
        env["PYTHONPATH"] = str(Path("kazma-core").resolve()) + os.pathsep + env.get("PYTHONPATH", "")

        outs = []
        for cwd, thread in ((cwd_a, "from-a"), (cwd_b, "from-b")):
            proc = subprocess.run(
                [sys.executable, str(script_file), thread],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert proc.returncode == 0, proc.stderr[-800:]
            outs.append(proc.stdout.strip().splitlines()[-1])

        resolved = str((data_dir / "snapshots.db").resolve())
        assert outs[0].lower() == resolved.lower(), outs
        assert outs[1].lower() == resolved.lower(), outs
        # No stray cwd-relative DB was created next to either interpreter cwd.
        assert not (cwd_a / "kazma-data" / "snapshots.db").exists()
        assert not (cwd_b / "kazma-data" / "snapshots.db").exists()

        # Both captures landed in ONE database.
        from kazma_core.time_travel import SnapshotStore

        store = SnapshotStore(db_path=data_dir / "snapshots.db")
        threads = sorted(store.list_distinct_threads())
        assert threads == ["from-a", "from-b"]
        store.close()

    def test_maintain_snapshots_default_targets_data_dir(self, tmp_path, monkeypatch):
        """maintain_snapshots(None) prunes the data-dir DB, not ./kazma-data."""
        from datetime import UTC, datetime, timedelta

        from kazma_core.time_travel import SnapshotRecord, SnapshotStore, maintain_snapshots

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))

        store = SnapshotStore()  # default → data dir
        old = SnapshotRecord(
            thread_id="old",
            iteration=0,
            state_json="{}",
            timestamp=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        )
        store.save(old)
        store.save(SnapshotRecord(thread_id="new", iteration=1, state_json="{}"))
        store.close()

        stats = maintain_snapshots(None, retention_days=30)
        assert stats["deleted"] >= 1
        assert stats["prune"] == "ok"
        # And nothing was written to a cwd-relative kazma-data/ tree.
        assert not (tmp_path / "kazma-data" / "snapshots.db").exists()

    def test_maintenance_start_loop_signature_backcompat(self):
        """start_snapshot_maintenance_loop accepts db_path=None."""
        import inspect

        from kazma_core.time_travel import start_snapshot_maintenance_loop

        sig = inspect.signature(start_snapshot_maintenance_loop)
        assert sig.parameters["db_path"].default is None


# ═══════════════════════════════════════════════════════════════════
# 4. file_read: bounded streaming + cache-after-access-check ordering
# ═══════════════════════════════════════════════════════════════════


class _AllowAll:
    allowed = True


@pytest.fixture
def allow_any_read(monkeypatch):
    """Permit all reads in this test module's scope (fake policy)."""
    from kazma_core.workspace import path_policy

    monkeypatch.setattr(path_policy, "check_path_access", lambda p, m, *a, **k: _AllowAll())


@pytest.fixture(autouse=True)
def _cold_read_cache():
    """Each test starts with a cold dedup cache (process-global state)."""
    from kazma_core.tools.file_read import clear_read_cache

    clear_read_cache()
    yield
    clear_read_cache()


# NOTE: do NOT use ``import kazma_core.tools.file_read as fr`` — the
# tools/__init__ package re-exports the file_read FUNCTION under the same
# attribute name, and ``import pkg.mod as x`` binds via getattr. Use
# importlib.import_module (as below) to obtain the actual module.


class TestFileReadStreamingBudget:
    async def test_oversized_file_streams_window_with_notice(
        self, tmp_path, monkeypatch, allow_any_read
    ):
        """A file larger than the (monkeypatched) budget streams lazily:
        correct line window + truncation notice + bounded memory path."""
        fr = importlib.import_module("kazma_core.tools.file_read")

        big = tmp_path / "big.log"
        row = "x" * 512 + "\n"
        lines_total = 4000  # ≈2 MB >> 64 KiB budget
        big.write_text(row * lines_total, encoding="utf-8")

        monkeypatch.setattr(fr, "MAX_READ_BUDGET", 64 * 1024)

        result = await fr.file_read(str(big), offset=10, limit=5)
        assert "10|" in result and "14|" in result
        assert "15|" not in result
        assert "truncated" in result.lower()  # per-read-budget notice
        assert "[ALREADY READ" not in result

    async def test_offset_exceeds_huge_file_reports_seen_lines(
        self, tmp_path, monkeypatch, allow_any_read
    ):
        """An offset unreachable within the byte budget (on an oversized
        file) degrades to the explicit budget message — the exact line
        count cannot be proven without scanning past the budget."""
        fr = importlib.import_module("kazma_core.tools.file_read")

        big = tmp_path / "big2.log"
        big.write_text(("line\n") * 50_000, encoding="utf-8")  # ≈240 KiB
        monkeypatch.setattr(fr, "MAX_READ_BUDGET", 64 * 1024)

        result = await fr.file_read(str(big), offset=99_999, limit=10)
        assert result.startswith("Error: file exceeds the 0.1 MiB")
        assert "per-read budget before line 99999" in result

    async def test_small_files_keep_exact_legacy_semantics(
        self, tmp_path, allow_any_read
    ):
        fr = importlib.import_module("kazma_core.tools.file_read")

        f = tmp_path / "small.txt"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        out = await fr.file_read(str(f), offset=2, limit=1)
        assert out.splitlines() == ["2|beta"]

        out_all = await fr.file_read(str(f), offset=0, limit=500)
        assert out_all.splitlines()[0] == "1|alpha"
        assert out_all.splitlines()[-1] == "3|gamma"

        missing = await fr.file_read(str(f), offset=99, limit=5)
        assert missing.startswith("Error: offset 99 exceeds file length (3 lines)")

    async def test_window_complete_via_multi_byte_safe_decoder(
        self, tmp_path, monkeypatch, allow_any_read
    ):
        """Multi-byte UTF-8 split across chunk boundaries decodes cleanly.

        Budget floor-clamps at 64 KiB, so the fixture file must exceed that
        to take the streaming path; _STREAM_CHUNK_SIZE=7 guarantees the
        incremental decoder sees partial multi-byte sequences.
        """
        fr = importlib.import_module("kazma_core.tools.file_read")

        f = tmp_path / "unicode.txt"
        content = "αααα\nββββ\nγγγγ\nδδδδ\n" * 2000  # multi-byte, ≈66 KiB
        f.write_text(content, encoding="utf-8")
        assert f.stat().st_size > 64 * 1024

        monkeypatch.setattr(fr, "MAX_READ_BUDGET", 64 * 1024)
        monkeypatch.setattr(fr, "_STREAM_CHUNK_SIZE", 7)

        out = await fr.file_read(str(f), offset=3, limit=2)
        assert "truncated" in out.lower()
        numbered = [ln for ln in out.splitlines() if "|" in ln]
        assert len(numbered) == 2
        got = [ln.split("|", 1)[1] for ln in numbered]
        assert got[0] == "γγγγ"
        assert got[1] == "δδδδ"


class TestFileReadCacheOrdering:
    async def test_revoked_grant_not_served_from_cache(
        self, tmp_path, monkeypatch, allow_any_read
    ):
        """A grant revoked after the first read must NOT be bypassed by the
        per-turn cache: access validation runs BEFORE cache lookup (M31/H16)."""
        fr = importlib.import_module("kazma_core.tools.file_read")
        from kazma_core.workspace import path_policy

        f = tmp_path / "secret.txt"
        f.write_text("top secret contents\n", encoding="utf-8")

        first = await fr.file_read(str(f))
        assert "top secret contents" in first

        real_policy = path_policy.check_path_access

        def denying(p, mode, *a, **k):
            if Path(str(p)) == f.resolve():
                return path_policy.PathAccessResult(
                    allowed=False,
                    reason="grant revoked mid-turn",
                    mode="read",
                    via="denied",
                    resolved=str(f),
                    workspace=str(tmp_path),
                )
            return real_policy(p, mode, *a, **k)

        monkeypatch.setattr(path_policy, "check_path_access", denying)

        second = await fr.file_read(str(f))
        assert "ALREADY READ THIS TURN" not in second
        assert "not allowed" in second or "Safety:" in second

    async def test_cached_hit_still_works_when_access_allowed(
        self, tmp_path, allow_any_read
    ):
        fr = importlib.import_module("kazma_core.tools.file_read")

        f = tmp_path / "ok.txt"
        f.write_text("stable body\n", encoding="utf-8")

        await fr.file_read(str(f))
        again = await fr.file_read(str(f))
        assert again.startswith("[ALREADY READ THIS TURN")
        assert "stable body" in again

    def test_clear_read_cache_exported_and_clears(self, tmp_path, allow_any_read):
        import asyncio

        fr = importlib.import_module("kazma_core.tools.file_read")

        f = tmp_path / "c.txt"
        f.write_text("body\n", encoding="utf-8")
        asyncio.run(fr.file_read(str(f)))

        assert hasattr(fr, "clear_read_cache")
        fr.clear_read_cache()
        assert fr._turn_read_cache == {}
        assert fr._turn_read_cache_order == []
        # legacy alias remains for graph_respond
        assert fr.clear_turn_read_cache is fr.clear_read_cache
