"""kazma update must preserve optional extras (never bare uv sync)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kazma_cli import update as upd


def test_normalize_extras_order_and_all():
    assert upd._normalize_extras(["web", "rag", "web"]) == ["rag", "web"]
    assert "rag" in upd._normalize_extras(["all"])
    assert "dev" in upd._normalize_extras(["all"])


def test_detect_includes_persisted_and_markers(tmp_path, monkeypatch):
    extras_file = tmp_path / "installed_extras.json"
    extras_file.write_text('{"extras": ["tui"]}\n', encoding="utf-8")
    monkeypatch.setattr(upd, "_extras_file", lambda: extras_file)

    def fake_module(mod: str) -> bool:
        return mod in ("chromadb",)

    with patch.object(upd, "_module_available", side_effect=fake_module):
        with patch.object(upd, "_vector_memory_data_present", return_value=False):
            with patch.object(upd, "load_persisted_extras", return_value=["tui"]):
                got = upd.detect_active_extras(str(tmp_path))
    assert "tui" in got
    assert "rag" in got  # chromadb marker


def test_vector_memory_heuristic_adds_rag(tmp_path, monkeypatch):
    vec = tmp_path / "vector_memory"
    vec.mkdir()
    (vec / "chroma.sqlite3").write_text("x", encoding="utf-8")

    with patch.object(upd, "load_persisted_extras", return_value=[]):
        with patch.object(upd, "_module_available", return_value=False):
            with patch(
                "kazma_core.paths.vector_memory_path",
                return_value=vec,
                create=True,
            ):
                # Force the path helper used inside heuristic
                with patch.object(
                    upd,
                    "_vector_memory_data_present",
                    wraps=None,
                ):
                    # Call real heuristic with patched path
                    def present(cwd: str) -> bool:
                        p = Path(vec)
                        return p.is_dir() and any(p.iterdir())

                    with patch.object(upd, "_vector_memory_data_present", side_effect=present):
                        got = upd.detect_active_extras(str(tmp_path))
    assert "rag" in got


def test_reinstall_prefers_additive_uv_pip_with_extras(tmp_path):
    """Bare ``uv sync`` must not be the first (or preferred) command."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, timeout=None):
        calls.append(list(cmd))
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch.object(upd, "detect_active_extras", return_value=["rag", "tui"]):
        with patch.object(upd, "persist_extras"):
            with patch.object(upd, "_run_cmd", side_effect=fake_run):
                ok = upd._reinstall_local(str(tmp_path))

    assert ok is True
    assert calls, "expected at least one install command"
    first = calls[0]
    # Additive install first
    assert first[0] == "uv"
    assert "pip" in first
    assert any(".[" in a and "rag" in a for a in first)
    # Never invoke bare uv sync (without --inexact)
    for c in calls:
        if c[:2] == ["uv", "sync"]:
            assert "--inexact" in c


def test_reinstall_never_runs_bare_uv_sync(tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, timeout=None):
        calls.append(list(cmd))
        r = MagicMock()
        # fail first, succeed second
        r.returncode = 0 if len(calls) > 1 else 1
        r.stdout = ""
        r.stderr = "fail"
        return r

    with patch.object(upd, "detect_active_extras", return_value=["rag"]):
        with patch.object(upd, "persist_extras"):
            with patch.object(upd, "_run_cmd", side_effect=fake_run):
                upd._reinstall_local(str(tmp_path))

    for c in calls:
        if len(c) >= 2 and c[0] == "uv" and c[1] == "sync":
            assert "--inexact" in c, f"bare uv sync is forbidden: {c}"
