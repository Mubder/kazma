"""The embedder rebuild route keeps its settings work off the event loop (2026-10-01).

``POST /api/settings/embedder/rebuild`` read the rebuild status and the model
name, wrote "running", and its background task wrote "done" or "error" -- all
settings-store calls, all on the loop (``_get_sm()._cs.set`` is a receiver
the settings-store gate does not name). Each now runs in a thread.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from kazma_core.config_store import ConfigStore


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from kazma_core.memory import embedder, reembed

    cs = ConfigStore(db_path=str(tmp_path / "s.db"))
    writes: list[tuple[str, bool]] = []
    done = threading.Event()
    release = threading.Event()
    real_set = cs.set

    def recording_set(key: str, value: Any, category: str = "general") -> None:
        if key == reembed.REBUILD_STATUS_KEY:
            writes.append((value.get("state"), _on_loop()))
            if value.get("state") in ("done", "error"):
                done.set()
        real_set(key, value, category=category)

    monkeypatch.setattr(cs, "set", recording_set)
    monkeypatch.setattr(reembed, "get_rebuild_status", lambda: cs.get(reembed.REBUILD_STATUS_KEY) or {})
    monkeypatch.setattr(embedder, "get_embedding_model_name", lambda: "test-model")

    def fake_rebuild(progress):
        progress(1, 2)
        release.wait(5)
        return {"model": "test-model", "episodes": 2, "beliefs": 1,
                "started_at": "a", "finished_at": "b"}

    monkeypatch.setattr(reembed, "rebuild_embeddings", fake_rebuild)
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    agent = MagicMock()
    from kazma_ui.settings import create_settings_router

    app = FastAPI()
    app.include_router(create_settings_router(agent, cs, Jinja2Templates(directory=str(templates_dir))))

    @app.post("/test/old-shape")
    async def old_shape() -> dict:
        # What the route did before: the status write on the loop.
        cs.set(reembed.REBUILD_STATUS_KEY, {"state": "running"}, category="embedding")
        return {}

    with TestClient(app) as client:
        yield client, writes, done, release
    cs.close()


def test_the_rebuild_writes_its_status_off_the_loop(rig) -> None:
    client, writes, done, release = rig
    first = client.post("/api/settings/embedder/rebuild").json()
    assert first == {"status": "ok", "model": "test-model"}
    again = client.post("/api/settings/embedder/rebuild").json()
    assert again["status"] == "already"
    release.set()
    assert done.wait(5)
    states = [state for state, _ in writes]
    assert states[0] == "running" and states[-1] == "done"
    assert [state for state, on_loop in writes if on_loop] == []


def test_a_status_write_on_the_loop_would_be_seen(rig) -> None:
    """Negative control: the old shape, a write from an async handler, is
    recorded as made on the loop."""
    client, writes, done, release = rig
    release.set()
    client.post("/test/old-shape")
    assert writes == [("running", True)]
