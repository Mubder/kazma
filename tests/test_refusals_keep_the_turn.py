"""A refusal made before anything changed keeps the turn going
(audit 2026-10-26 AUD-008, AUD-039).

The effect journal ends a turn whose mutating tool failed after it ran
("effects unknown; this call must not be repeated"). ``file_apply_patch``
returned its refusals -- the commonest being an ``old_string`` that does not
match -- as plain error strings, and ``knowledge_ingest_site`` a crawl refused
at admission, so both failed the turn instead of letting the model correct
the call. Both now raise ``RefusedBeforeEffect``; these tests go through
``execute_effect``, the wrapper the chat graph uses.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kazma_core.agent.effect_journal import EffectUncertain, execute_effect
from kazma_core.agent.tool_registry import LocalToolRegistry


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(ws))
    from kazma_core.workspace import binding

    monkeypatch.setattr(binding, "resolve_active_root", lambda *a, **k: ws)
    return ws


def _state(n: int) -> dict:
    return {"thread_id": "refusal-thread", "tenant_id": "owner",
            "created_at": f"turn-{n}", "iteration": 1}


def _run(registry, name: str, args: dict, n: int = 1) -> dict:
    call = {"id": f"call-{n}", "name": name, "arguments": args}
    return asyncio.run(execute_effect(registry, _state(n), call, args))


def test_a_patch_that_does_not_match_is_a_correctable_error(workspace: Path) -> None:
    target = workspace / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = LocalToolRegistry(include_builtins=True)
    out = _run(registry, "file_apply_patch", {
        "path": str(target), "old_string": "gamma", "new_string": "delta",
    })
    assert out.get("is_error")
    assert str(out.get("content", "")).startswith("Error")
    assert not out.get("effect_uncertain")
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_a_missing_file_is_a_correctable_error(workspace: Path) -> None:
    registry = LocalToolRegistry(include_builtins=True)
    out = _run(registry, "file_apply_patch", {
        "path": str(workspace / "absent.txt"), "old_string": "x", "new_string": "y",
    }, n=2)
    assert "File not found" in str(out.get("content", ""))
    assert not out.get("effect_uncertain")


def test_the_old_returned_string_failed_the_turn(workspace: Path) -> None:
    """Negative control: the same refusal RETURNED by a mutating tool."""
    registry = LocalToolRegistry(include_builtins=False)

    async def file_apply_patch(path: str = "", old_string: str = "", new_string: str = "") -> str:
        return "Error: old_string not found"

    registry.register_function("file_apply_patch", file_apply_patch)
    with pytest.raises(EffectUncertain):
        _run(registry, "file_apply_patch", {"path": "x", "old_string": "a", "new_string": "b"}, n=3)


def test_a_crawl_refused_at_admission_keeps_the_turn(workspace: Path) -> None:
    registry = LocalToolRegistry(include_builtins=True)
    out = _run(registry, "knowledge_ingest_site", {
        "library_id": "probe", "seed_url": "http://127.0.0.1:9/", "max_pages": 1,
    }, n=4)
    assert out.get("is_error"), out
    assert "refused" in str(out.get("content", "")).lower()
    assert not out.get("effect_uncertain")
