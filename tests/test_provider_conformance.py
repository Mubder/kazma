"""Provider conformance — the contract every provider must satisfy.

Phase 1 of `docs/plans/PROVIDER_LAYER_PLAN.md`.

Adding a provider should be filling in a form. Today it is filling in a form and
then discovering, over the following weeks, which code paths it breaks. Three
providers have now hit the *same* defect:

* **Google** — `/v1beta/openai` had `/v1` appended, 404 on every call. Patched
  with a hostname exemption.
* **Z.AI** — `/api/paas/v4` had `/v1` appended, 404 on every call, 84 of 84
  benchmark requests failed. Patched by fixing the rule.
* **Perplexity** — declares `https://api.perplexity.ai`, whose OpenAI-compatible
  path is `/chat/completions` with no version segment. Still mutated to `/v1`.

Each was found by a human running into it. This file is the check that finds the
next one at the moment the provider is added.

These tests make **no network calls** and need no key, so they run in CI on every
commit. The live probes — does a real completion come back, is the system turn
honoured, does a tool call round-trip — live in `scripts/provider_conformance.py`
and are opt-in, because they cost money.

The division matters: a static check catches *what we declared wrong*, and a live
check catches *what the provider does differently from what we assumed*. Both
have now bitten.
"""

from __future__ import annotations

import pytest
from kazma_core.providers import PROVIDER_PRESETS
from kazma_core.url_utils import normalize_provider_url

#: Presets that intentionally carry no `base_url`: the URL is constructed at
#: runtime from a deployment, region or account, so there is nothing to declare.
_NO_BASE_URL = {"azure", "bedrock", "google", "custom"}

_WITH_BASE_URL = sorted(
    name for name, entry in PROVIDER_PRESETS.items()
    if name not in _NO_BASE_URL and (entry.get("base_url") or "").strip()
)


def test_the_preset_table_is_not_empty():
    """A guard on the guards: if the import shape changes, every parametrised
    test below would silently collapse to zero cases and pass."""
    assert len(PROVIDER_PRESETS) >= 15
    assert len(_WITH_BASE_URL) >= 10


@pytest.mark.parametrize("name", _WITH_BASE_URL)
def test_a_declared_base_url_is_used_verbatim(name):
    """What a preset declares is what gets called.

    `normalize_provider_url` exists to rescue a URL a *human typed* into the
    Settings box — "localhost:1234" should become "http://localhost:1234/v1".
    Applying that guesswork to a URL we shipped ourselves is not a rescue, it is
    an override: the preset is the most authoritative statement we have about
    where that provider's API lives, and inference cannot know better.

    Every provider that has hit this bug hit it the same way — a path ending in
    something other than `/v1`, silently extended, 404 on every call, and a UI
    that reported the provider as healthy because the models endpoint answered.
    """
    declared = PROVIDER_PRESETS[name]["base_url"].strip().rstrip("/")
    resolved = normalize_provider_url(declared).rstrip("/")
    assert resolved == declared, (
        f"{name}: preset declares {declared!r} but the client will call "
        f"{resolved!r}. A declared base_url must be used as written."
    )


@pytest.mark.parametrize("name", _WITH_BASE_URL)
def test_every_base_url_is_absolute_and_https_unless_local(name):
    """A scheme-less or http:// cloud endpoint is a misconfiguration that only
    shows up as a connection error much later."""
    url = PROVIDER_PRESETS[name]["base_url"].strip()
    assert url.startswith(("http://", "https://")), f"{name}: {url!r} has no scheme"
    if not any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0")):
        assert url.startswith("https://"), f"{name}: cloud endpoint {url!r} is not https"


@pytest.mark.parametrize("name", sorted(PROVIDER_PRESETS))
def test_every_preset_declares_the_fields_the_client_needs(name):
    """Missing fields become runtime `None`s deep inside the transport, where
    the traceback names neither the provider nor the field."""
    entry = PROVIDER_PRESETS[name]
    assert entry.get("name"), f"{name}: no display name"
    assert "auth_header" in entry, f"{name}: no auth_header (use '' for none)"
    if name not in _NO_BASE_URL:
        assert entry.get("models_endpoint"), f"{name}: no models_endpoint"


@pytest.mark.parametrize("name", sorted(PROVIDER_PRESETS))
def test_no_preset_carries_a_secret(name):
    """A key committed in a preset would ship to every install. Cheap to check,
    catastrophic to miss."""
    entry = PROVIDER_PRESETS[name]
    for field, value in entry.items():
        if not isinstance(value, str):
            continue
        assert not value.startswith(("sk-", "gsk_", "pplx-")), (
            f"{name}.{field} looks like an API key"
        )


# ── the normaliser keeps doing its actual job ───────────────────────────────


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("localhost:1234", "http://localhost:1234/v1"),
        ("http://localhost:1234", "http://localhost:1234/v1"),
        ("https://gw.example.com/api", "https://gw.example.com/api/v1"),
        ("https://api.openai.com/v1/v1", "https://api.openai.com/v1"),
        ("https://api.example.com/v4", "https://api.example.com/v4"),
        ("https://api.example.com/v1beta", "https://api.example.com/v1beta"),
    ],
)
def test_inference_still_helps_a_url_a_human_typed(typed, expected):
    """Narrowing where inference applies must not remove it. A custom endpoint
    someone pastes into Settings still gets the `/v1` it almost certainly needs,
    and an already-versioned path is still left alone."""
    assert normalize_provider_url(typed) == expected


# ── capabilities are declared, and unknowns stay honest ─────────────────────


@pytest.mark.parametrize("name", sorted(PROVIDER_PRESETS))
def test_every_provider_resolves_a_complete_capability_shape(name):
    """Callers must never branch on a missing key. `capabilities()` always
    returns the full shape, so a provider nobody has configured behaves like
    one that was, minus the facts."""
    from kazma_core.providers import capabilities

    caps = capabilities(name)
    assert caps["api_style"] in {"openai", "anthropic", "bedrock", "google", "azure"}
    assert isinstance(caps["system_role"], str) and caps["system_role"]
    assert set(caps["supports"]) == {"tools", "streaming", "json_mode", "vision"}


@pytest.mark.parametrize("name", sorted(PROVIDER_PRESETS))
def test_an_unverified_capability_is_none_not_false(name):
    """`None` means nobody has checked; `False` means someone checked and it
    does not work. Collapsing the two would trade a silent assumption for the
    same assumption wearing a schema — and the UI could no longer tell a
    reader which it is looking at."""
    from kazma_core.providers import capabilities

    for capability, value in capabilities(name)["supports"].items():
        assert value is None or isinstance(value, bool), (
            f"{name}.{capability} is {value!r}; use None for unverified"
        )


def test_a_measured_capability_names_where_it_came_from():
    """An override is either structural (which adapter serves it) or measured.
    If someone adds a bare `True`, the comment block above CAPABILITY_OVERRIDES
    is the thing that stops it being an assumption, so it has to stay."""
    import inspect

    from kazma_core import providers

    src = inspect.getsource(providers)
    assert "NOT VERIFIED" in src
    assert "provider_conformance" in src, (
        "the overrides no longer point at the script that turns None into a bool"
    )


def test_zai_is_a_preset_not_a_hand_typed_custom_entry():
    """It is in production use. A provider configured by hand in the UI carries
    no capabilities and no declared base URL, which is how its /v4 path got a
    /v1 appended to it in the first place."""
    from kazma_core.providers import capabilities

    entry = PROVIDER_PRESETS["zai"]
    assert entry["base_url"] == "https://api.z.ai/api/paas/v4"
    assert capabilities("zai")["supports"]["tools"] is True
