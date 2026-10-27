"""Server rows of the 2026-10-26 audit's AUD-082 (dead and wrong code).

* ``/api/system/status`` closed its FTS connection three times (in the body,
  the handler and the ``finally``), the last two swallowing what they hit.
* The telemetry stream swallowed ``CancelledError``: the generator ended as
  if it had finished, and whoever cancelled it never saw the cancel.
* "Backup Now" ran in a background task named ``memory-maintenance``.
* The setup route filtered an ``api_key`` its status can never hold (the
  status itself is held by ``tests/test_setup_api.py``).

Each has a negative control built from the code that shipped.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

REPO = Path(__file__).resolve().parents[1]


# ── /api/system/status: one close ──────────────────────────────────────


class _CountingConnection:
    def __init__(self, inner: sqlite3.Connection, closes: list[int]) -> None:
        self._inner = inner
        self._closes = closes

    def cursor(self):  # noqa: ANN201
        return self._inner.cursor()

    def close(self) -> None:
        self._closes.append(1)
        self._inner.close()


def _status_closes(monkeypatch, tmp_path, register) -> int:
    from kazma_core.system import maintenance

    db = tmp_path / "memory.db"
    with sqlite3.connect(db) as seed:
        seed.execute("CREATE TABLE memories (x)")
        seed.execute("INSERT INTO memories VALUES (1)")
    seed.close()
    monkeypatch.setattr(maintenance, "get_memory_paths",
                        lambda: (db, tmp_path / "vectors", None))
    closes: list[int] = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: _CountingConnection(real_connect(*a, **k), closes))
    app = FastAPI()
    register(SimpleNamespace(app=app))
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/system/status")
    try:
        route.endpoint()
    except Exception:  # the rest of the status is not under test
        pass
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    return len(closes)


def test_the_status_closes_its_connection_once(monkeypatch, tmp_path) -> None:
    from kazma_ui.routes_direct.system import register_system_routes

    assert _status_closes(monkeypatch, tmp_path, register_system_routes) == 1


def test_negative_control_the_shipped_status_closed_it_again(monkeypatch, tmp_path) -> None:
    """The shipped shape: closed in the body and again in the ``finally``
    (and a third time in the handler when the count failed)."""
    import kazma_ui.routes_direct.system as system

    src = (REPO / "kazma-ui" / "kazma_ui" / "routes_direct" / "system.py").read_text(encoding="utf-8")
    fixed = src[src.index("            try:\n                with closing(sqlite3.connect(fts5_path)) as conn:"):
                src.index("                logger.debug(\"fts5 count failed: %s\", _e)\n") + len(
                    "                logger.debug(\"fts5 count failed: %s\", _e)\n")]
    shipped = (
        "            try:\n"
        "                conn = sqlite3.connect(fts5_path)\n"
        "                cursor = conn.cursor()\n"
        "                cursor.execute(\"SELECT COUNT(*) FROM [memories]\")\n"
        "                fts5_count = int(cursor.fetchone()[0] or 0)\n"
        "                conn.close()\n"
        "            except Exception as _e:\n"
        "                logger.debug(\"fts5 count failed: %s\", _e)\n"
        "                try:\n"
        "                    conn.close()\n"
        "                except Exception:\n"
        "                    pass\n"
        "            finally:\n"
        "                try:\n"
        "                    conn.close()\n"
        "                except Exception:\n"
        "                    pass\n"
    )
    namespace: dict = {}
    exec(compile(src.replace(fixed, shipped), system.__file__, "exec"), namespace)  # noqa: S102
    assert _status_closes(monkeypatch, tmp_path, namespace["register_system_routes"]) == 2


# ── the telemetry stream keeps a cancel ─────────────────────────────────


class _CancelledMonitor:
    async def stream(self, interval: float = 1.0):  # noqa: ARG002
        raise asyncio.CancelledError
        yield  # pragma: no cover


async def _drain(router) -> None:
    route = next(r for r in router.routes if r.path == "/api/telemetry/stream")
    response = await route.endpoint()
    async for _ in response.body_iterator:
        pass


def test_the_telemetry_stream_passes_a_cancel_on() -> None:
    from kazma_ui.telemetry_route import create_telemetry_router

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_drain(create_telemetry_router(monitor=_CancelledMonitor())))


def test_negative_control_the_shipped_stream_swallowed_it() -> None:
    import kazma_ui.telemetry_route as telemetry

    src = Path(telemetry.__file__).read_text(encoding="utf-8")
    start = src.index("                # The cancel belongs to whoever cancelled")
    end = src.index("                raise\n", start) + len("                raise\n")
    namespace: dict = {}
    exec(compile(src[:start] + src[end:], telemetry.__file__, "exec"), namespace)  # noqa: S102
    asyncio.run(_drain(namespace["create_telemetry_router"](monitor=_CancelledMonitor())))


# ── names and dead checks ───────────────────────────────────────────────


def _background_task_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "spawn_background":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.append(kw.value.value)
    return names


def test_backup_now_runs_under_its_own_name() -> None:
    path = REPO / "kazma-ui" / "kazma_ui" / "routes_direct" / "backup.py"
    names = _background_task_names(path)
    assert "manual-universal-backup" in names
    assert "memory-maintenance" not in names


def test_negative_control_the_shipped_name_is_caught(tmp_path) -> None:
    planted = tmp_path / "backup.py"
    planted.write_text('spawn_background(_run(), name="memory-maintenance")\n', encoding="utf-8")
    assert _background_task_names(planted) == ["memory-maintenance"]
