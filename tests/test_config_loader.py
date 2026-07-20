"""Tests for shipped + local YAML merge."""

from __future__ import annotations

from pathlib import Path

from kazma_core.config_loader import deep_merge, load_merged_yaml


def test_deep_merge_nested() -> None:
    base = {"a": 1, "b": {"x": 1, "y": 2}}
    overlay = {"b": {"y": 9}, "c": 3}
    out = deep_merge(base, overlay)
    assert out == {"a": 1, "b": {"x": 1, "y": 9}, "c": 3}
    # base not mutated
    assert base["b"]["y"] == 2


def test_load_merged_yaml_local_overrides(tmp_path: Path) -> None:
    shipped = tmp_path / "kazma.yaml"
    local = tmp_path / "kazma.local.yaml"
    shipped.write_text(
        "agent:\n  name: shipped\n  version: '0.6.0'\nui:\n  port: 9090\n",
        encoding="utf-8",
    )
    local.write_text(
        "agent:\n  name: local-machine\nui:\n  port: 9191\n",
        encoding="utf-8",
    )
    raw = load_merged_yaml(shipped)
    assert raw["agent"]["name"] == "local-machine"
    assert raw["agent"]["version"] == "0.6.0"
    assert raw["ui"]["port"] == 9191


def test_load_merged_yaml_missing_local(tmp_path: Path) -> None:
    shipped = tmp_path / "kazma.yaml"
    shipped.write_text("agent:\n  name: only-shipped\n", encoding="utf-8")
    raw = load_merged_yaml(shipped)
    assert raw["agent"]["name"] == "only-shipped"
