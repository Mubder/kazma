"""AUD-070: a dispatch with no swarm engine is a refusal that records nothing.

The route answered 200 ``warning`` "The task was recorded, but no worker will
run it." Nothing is recorded on that path (no task store row, no history),
and the page counts a ``warning`` as dispatched: it toasted "dispatched to 0
workers" and cleared the prompt the user had typed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, *, core: bool) -> TestClient:
    from kazma_ui.swarm_panel import routes_tasks

    class _Svc:
        def resolve_engine(self, m: Any = None) -> Any:
            return None

        def has_swarm_core(self) -> bool:
            return core

    monkeypatch.setattr(routes_tasks, "get_swarm_service", lambda: _Svc())
    router = APIRouter()
    routes_tasks.register_tasks_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_no_engine_is_a_refusal_that_says_nothing_was_recorded(monkeypatch) -> None:
    with _client(monkeypatch, core=False) as client:
        r = client.post("/api/swarm/dispatch", json={"task": "t", "workers": ["w"]})
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "error"
    assert body["code"] == "swarm_engine_unavailable"
    assert "The task was recorded" not in body["message"]
    assert "Nothing was recorded or run" in body["message"]
    assert body["dispatched"] == []


def test_the_page_words_the_refusal_from_the_catalog() -> None:
    """The page reads the code, in both languages (not the English message)."""
    from pathlib import Path

    from kazma_ui.i18n.catalog.swarm import TRANSLATIONS as _S

    entry = _S["swarm.engine_unavailable"]
    assert entry["en"] and entry["ar"]
    js = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "swarm.js"
    ).read_text(encoding="utf-8")
    assert "data.code === 'swarm_engine_unavailable'" in js
    assert "t('swarm.engine_unavailable')" in js
