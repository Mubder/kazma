"""Hosted embed-only fleet defaults (#78)."""

from __future__ import annotations

from kazma_core.memory.embedder import apply_hosted_fleet_defaults


def test_local_stays_local_without_flag() -> None:
    out = apply_hosted_fleet_defaults(
        {"provider": "local", "model": "BAAI/bge-m3", "dim": 1024, "base_url": ""},
        environ={"OPENAI_API_KEY": "sk-test"},
    )
    assert out["provider"] == "local"


def test_fleet_flag_picks_openai() -> None:
    out = apply_hosted_fleet_defaults(
        {"provider": "local", "model": "BAAI/bge-m3", "dim": 1024, "base_url": ""},
        environ={"KAZMA_EMBED_FLEET": "1", "OPENAI_API_KEY": "sk-test"},
    )
    assert out["provider"] == "openai-compatible"
    assert out["base_url"] == "https://api.openai.com/v1"
    assert out["model"] == "text-embedding-3-small"
    assert out["dim"] == 1536


def test_named_voyage_fills_url() -> None:
    out = apply_hosted_fleet_defaults(
        {"provider": "voyage", "model": "BAAI/bge-m3", "dim": 1024, "base_url": ""},
        environ={"VOYAGE_API_KEY": "v-test"},
    )
    assert out["provider"] == "openai-compatible"
    assert "voyageai" in out["base_url"]
    assert out["model"] == "voyage-3"
    assert out["dim"] == 1024


def test_fleet_without_keys_stays_local() -> None:
    out = apply_hosted_fleet_defaults(
        {"provider": "local", "model": "BAAI/bge-m3", "dim": 1024},
        environ={"KAZMA_EMBED_FLEET": "1"},
    )
    assert out["provider"] == "local"
