"""Provider addresses that reach the provider (providers stage 1, 2026-11-08).

``/v1`` was added to any address whose path did not END in a version, so
DeepInfra's OpenAI-compatible API (``.../v1/openai``) became
``.../v1/openai/v1`` -- a 404 on every call. And the Cohere preset named
Cohere's own API (``api.cohere.ai/v1``), which has no ``/chat/completions``;
its OpenAI-compatible API is ``/compatibility/v1``. A row saved with the old
preset address moves at boot; an address the owner typed stays.
"""

from __future__ import annotations

import re

import pytest
from kazma_core.url_utils import normalize_provider_url


@pytest.mark.parametrize("given,expected", [
    ("https://api.deepinfra.com/v1/openai", "https://api.deepinfra.com/v1/openai"),
    ("https://host.example/v1beta/openai", "https://host.example/v1beta/openai"),
    ("https://api.z.ai/api/paas/v4", "https://api.z.ai/api/paas/v4"),
    ("https://openrouter.ai/api", "https://openrouter.ai/api/v1"),
    ("https://api.example.com", "https://api.example.com/v1"),
])
def test_v1_is_added_only_to_an_address_with_no_version(given, expected):
    assert normalize_provider_url(given) == expected


def test_control_the_old_rule_doubled_deepinfras_version():
    path = "/v1/openai"
    assert re.search(r"/v\d+[a-z0-9]*$", path) is None  # the old rule: "not versioned" -> /v1 added


def test_a_preset_row_on_its_old_address_moves_and_a_typed_one_stays():
    from kazma_core.model_registry_store import seed_missing_presets
    from kazma_core.providers import PROVIDER_PRESETS

    stored = [
        {"name": "cohere", "base_url": "https://api.cohere.ai/v1/", "api_key": "", "models": [], "enabled": False},
        {"name": "together", "base_url": "https://my-proxy.example/v1", "api_key": "", "models": [], "enabled": False},
    ]
    rows, changed = seed_missing_presets([dict(r) for r in stored])
    by_name = {r["name"]: r for r in rows}
    assert changed is True
    assert by_name["cohere"]["base_url"] == PROVIDER_PRESETS["cohere"]["base_url"] == (
        "https://api.cohere.ai/compatibility/v1")
    assert by_name["together"]["base_url"] == "https://my-proxy.example/v1"
    # Run again: nothing left to move.
    again = [r for r in rows if r["name"] in ("cohere", "together")]
    _, changed_again = seed_missing_presets(again + [r for r in rows if r["name"] not in ("cohere", "together")])
    assert changed_again is False
