"""A health or status read never builds the embedder (2026-10-15).

``build_v2_health`` (behind ``/api/system/status`` and the Memory page) and
the memory quality score asked ``get_embedder()`` whether the embedder was
ready -- and ``get_embedder()`` BUILDS it, loading a multi-gigabyte local
model on a status poll. The page tours caught it once the test egress guard
refused the real model. ``embedder_readiness`` answers from what the process
already holds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kazma_core.memory import embedder as embedder_mod

REPO = Path(__file__).resolve().parents[1]
_PRODUCT = [REPO / f"{pkg}/{pkg.replace('-', '_')}" for pkg in (
    "kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills",
)]
# A function that answers "how is it" reads state; it must not build the
# embedder to do so.
_READER_WORDS = ("health", "status", "quality", "readiness", "ready", "probe")


@pytest.fixture
def no_build(monkeypatch):
    """No embedder held; any build is recorded and refused."""
    builds: list[str] = []

    def _refuse() -> None:
        builds.append("built")
        raise AssertionError("a health read built the embedder")

    monkeypatch.setattr(embedder_mod, "_embedder", None)
    monkeypatch.setattr(embedder_mod, "_create_embedder", _refuse)
    return builds


def test_memory_health_builds_no_embedder(no_build):
    from kazma_core.memory.v2_health import build_v2_health

    out = build_v2_health()
    assert no_build == []
    assert out["embedder_ready"] is False
    assert out["embedder_state"] == "not_loaded"


def _embedder_component(data: dict) -> dict:
    return next(c for c in data["components"] if c["id"] == "embedder")


def test_the_memory_board_builds_no_embedder(no_build):
    """``build_memory_health`` -- polled through /api/system/status by the
    Memory page -- probes a held embedder and builds none."""
    from kazma_core.memory.health import build_memory_health

    comp = _embedder_component(build_memory_health())
    assert no_build == []
    assert comp["status"] == "ok" and "Not loaded yet" in comp["detail"]


def test_a_remote_provider_without_a_key_is_said_without_a_build(no_build, monkeypatch):
    from kazma_core.memory.health import build_memory_health

    monkeypatch.setattr(embedder_mod, "get_embedding_config", lambda: {
        "provider": "openai-compatible", "model": "remote-embed", "dim": 1024,
        "api_key_env": "KAZMA_EMBED_API_KEY", "base_url": "",
    })
    for name in ("KAZMA_EMBED_API_KEY", "NVIDIA_API_KEY", "NGC_API_KEY", "OPENAI_API_KEY", "KAZMA_EMBED_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    comp = _embedder_component(build_memory_health())
    assert no_build == []
    assert comp["meta"]["provider"] == "openai-compatible"
    assert comp["status"] == "warn" and "fall back to the local model" in comp["detail"]


def test_the_old_readiness_question_built_it(no_build):
    """Negative control: the call health used to make builds the model."""
    with pytest.raises(AssertionError, match="built the embedder"):
        embedder_mod.get_embedder()
    assert no_build == ["built"]


class _Breaker:
    def __init__(self, state: str) -> None:
        self._state = state

    def state(self) -> dict:
        return {"state": self._state}


class _Held:
    def __init__(self, state: str) -> None:
        self.breaker = _Breaker(state)


@pytest.mark.parametrize(("breaker", "state", "ready"), [
    ("closed", "loaded", True),
    ("half_open", "loaded", True),
    ("open", "open", False),
])
def test_readiness_reads_the_held_embedder(monkeypatch, breaker, state, ready):
    monkeypatch.setattr(embedder_mod, "_embedder", _Held(breaker))
    assert embedder_mod.embedder_readiness() == {"state": state, "ready": ready}


def _readers_that_build(source: str, path: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(word in node.name.lower() for word in _READER_WORDS):
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                fn = call.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                if name == "get_embedder":
                    found.append(f"{path}:{call.lineno} {node.name}")
    return found


def test_no_health_or_status_reader_builds_the_embedder():
    found = []
    for root in _PRODUCT:
        for path in root.rglob("*.py"):
            if "_tests" in path.parts or "tests" in path.parts:
                continue
            found += _readers_that_build(path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))
    assert not found, "a health/status reader builds the embedder:\n" + "\n".join(found)


def test_the_gate_flags_the_old_health_code():
    old = (
        "def build_v2_health():\n"
        "    from kazma_core.memory.embedder import get_embedder\n"
        "    emb = get_embedder()\n"
        "    return {'embedder_ready': emb is not None}\n"
    )
    assert _readers_that_build(old, "v2_health.py") == ["v2_health.py:3 build_v2_health"]
