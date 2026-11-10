"""SearXNG's secret key can be set from the environment (audit 2026-11-10).

``deploy/searxng/settings.yml`` ships a placeholder key and says "Override via
env SEARXNG_SECRET", but the compose service passed nothing through, so the
override never reached the container. It is a pass-through with a default, not
a required variable: a required one would stop every current one-command
``docker compose --profile search up -d searxng``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _problems(compose_text: str) -> list[str]:
    env = yaml.safe_load(compose_text)["services"]["searxng"].get("environment") or {}
    value = str(env.get("SEARXNG_SECRET", ""))
    problems = []
    if not value:
        problems.append("SEARXNG_SECRET is not passed to the container")
    elif "${SEARXNG_SECRET" not in value:
        problems.append("SEARXNG_SECRET is hard-coded, not read from the environment")
    elif ":?" in value:
        problems.append("SEARXNG_SECRET is required: existing starts would be refused")
    return problems


def test_the_compose_service_passes_the_secret_through_without_requiring_it() -> None:
    assert _problems((ROOT / "docker-compose.yml").read_text(encoding="utf-8")) == []
    assert "SEARXNG_SECRET" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_negative_control_the_old_service_passed_nothing() -> None:
    old = """
services:
  searxng:
    environment:
      SEARXNG_BASE_URL: "${SEARXNG_BASE_URL:-http://127.0.0.1:8088/}"
"""
    assert _problems(old) == ["SEARXNG_SECRET is not passed to the container"]
