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


# ── the Settings page must test the path the product uses ───────────────────


class TestTheTestButtonExercisesChat:
    """`POST /api/providers/{name}/test` used to query the model list only.

    Measured on a live provider whose base URL had a version segment wrongly
    appended: `/models` returned 200 while `/chat/completions` returned 404. So
    the page reported a paid provider as healthy and not one message ever
    reached it. The operator configured it, tested it, saw green, and it had
    never worked.
    """

    def test_the_endpoint_sends_a_completion_before_claiming_success(self):
        import inspect

        from kazma_ui import providers as mod

        src = inspect.getsource(mod)
        assert "_probe_chat_completion" in src
        # and it must be called from the success path, not merely defined
        assert src.count("_probe_chat_completion") >= 2, (
            "the chat probe is defined but never called"
        )

    def test_a_reachable_but_broken_provider_is_not_success(self):
        """The state that had no representation: model list fine, chat dead."""
        import inspect

        from kazma_ui import providers as mod

        src = inspect.getsource(mod)
        assert '"chat_ok": False' in src
        assert "Reachable, but chat is failing" in src
        assert '"reachable": True' in src

    def test_the_probe_budgets_tokens_for_a_reasoning_model(self):
        """glm-5.3 spent 16 reasoning tokens before its first content token. A
        tight budget returns an empty completion and blames the provider for
        the probe's own mistake."""
        from kazma_core.provider_probe import PROBE_MAX_TOKENS

        assert PROBE_MAX_TOKENS >= 120, "the probe's token budget is too tight to be safe"

    def test_an_empty_completion_counts_as_a_failure(self):
        """A 200 with no content is not a working provider."""
        import inspect

        from kazma_core.provider_probe import probe_chat_completion

        assert "empty completion" in inspect.getsource(probe_chat_completion)


# ── the frontend can express the third state ────────────────────────────────


class TestTheUIHasThreeStates:
    """Two states cannot describe a provider that answers its model list and
    fails every message. That combination is not an edge case — it is what a
    wrongly-appended API version produces, and it happened three times."""

    @staticmethod
    def _js() -> str:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "static" / "js" / "providers.js"
        )
        assert path.exists(), "providers.js is gone"
        return path.read_text(encoding="utf-8")

    def test_it_classifies_into_three_states(self):
        js = self._js()
        for state in ("working", "chat_failing", "unreachable"):
            assert f"'{state}'" in js, f"the UI cannot express {state!r}"

    def test_chat_failing_is_derived_from_the_backend_fields(self):
        """Not guessed from a latency or an HTTP code — the backend says so."""
        js = self._js()
        assert "result.reachable" in js
        assert "result.chat_ok === false" in js

    def test_a_working_provider_reports_what_actually_answered(self):
        """'Working' should show the completion's latency and the model that
        replied, not the model list's latency. Different call, different
        number."""
        js = self._js()
        assert "chat_ms" in js and "chat_model" in js

    def test_the_status_dot_has_a_colour_for_chat_failing(self):
        js = self._js()
        assert "chat_failing:" in js


# ── one probe, every route that offers "Test" ───────────────────────────────


class TestThereIsOnlyOneProbe:
    """Two near-identical `test_provider` implementations existed, and the
    Settings page called the one that was NOT fixed — so improving the other
    changed nothing an operator would ever see. That is what two surfaces for
    one concept costs, and it cost it during this very refactor."""

    def test_both_routes_share_the_same_function_object(self):
        from kazma_core.provider_probe import probe_chat_completion
        from kazma_ui.providers import _probe_chat_completion

        assert _probe_chat_completion is probe_chat_completion, (
            "the UI route has its own copy of the probe again"
        )

    def test_the_settings_route_probes_chat_too(self):
        """This is the route providers.js actually calls."""
        import inspect

        from kazma_core import settings_providers

        src = inspect.getsource(settings_providers)
        assert "probe_chat_completion" in src
        assert "Reachable, but chat is failing" in src

    def test_the_probe_never_raises(self):
        """A health check that throws cannot report a health status."""
        import ast
        import inspect

        from kazma_core.provider_probe import probe_chat_completion

        # Parsed, not grepped: an earlier version of this test matched the word
        # "raise" inside the comment explaining that it must not raise.
        tree = ast.parse(inspect.getsource(probe_chat_completion).lstrip())
        raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        assert not raises, f"the probe can raise ({len(raises)} raise statements)"
        handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
        assert handlers, "the probe has no exception handling at all"


# ── the page has to actually show it ───────────────────────────────────────


class TestTheThreeStatesReachTheBrowser:
    """The probe returned `reachable`/`chat_ok` for a while and the page still
    showed two states, because FastAPI serialises the Test route through
    `ProviderTestResponse` and **drops every field the model does not
    declare**. The data was correct, the contract silently deleted it, and no
    test noticed because every test checked the function's return value rather
    than the response body."""

    def test_the_response_model_keeps_the_chat_fields(self):
        from kazma_ui.models import ProviderTestResponse

        dumped = ProviderTestResponse(
            success=False,
            latency_ms=12,
            reachable=True,
            chat_ok=False,
            error="Reachable, but chat is failing.",
        ).model_dump()

        assert dumped["reachable"] is True
        assert dumped["chat_ok"] is False

    def test_a_working_result_keeps_what_answered(self):
        from kazma_ui.models import ProviderTestResponse

        dumped = ProviderTestResponse(
            success=True, latency_ms=40, reachable=True, chat_ok=True,
            chat_ms=310, chat_model="glm-4.5",
        ).model_dump()

        assert dumped["chat_ms"] == 310
        assert dumped["chat_model"] == "glm-4.5"

    def test_chat_failure_gets_its_own_health_value(self):
        """`degraded` is also what a failing model list writes. Storing both
        under one label loses the distinction on the next page load, which is
        exactly when an operator needs it."""
        import inspect

        from kazma_core import settings_providers
        from kazma_ui import providers as ui_providers

        for module in (settings_providers, ui_providers):
            src = inspect.getsource(module)
            assert '"chat_failing"' in src, (
                f"{module.__name__} still collapses chat failure into 'degraded'"
            )


class TestTheProviderListCarriesCapabilities:
    """Badges cannot render from data the API never sends."""

    def test_the_list_route_attaches_declared_capabilities(self):
        import inspect

        from kazma_ui import providers as ui_providers

        src = inspect.getsource(ui_providers)
        assert 'entry["capabilities"]' in src, (
            "/api/providers does not send capabilities, so the UI has nothing to render"
        )

    def test_capabilities_keep_unknowns_as_null(self):
        """`None` must survive to JSON as `null`. A `False` here would be an
        assumption wearing a schema."""
        from kazma_core.providers import capabilities

        caps = capabilities("openrouter")
        assert caps["supports"]["streaming"] is None
        assert caps["supports"]["streaming"] is not False


class TestTheSettingsPageRendersTheStates:
    """The template is the surface the operator sees. Everything above this is
    plumbing that, until now, ended in a pipe with no tap on it."""

    @staticmethod
    def _html() -> str:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"
        )
        assert path.exists(), "settings.html is gone"
        return path.read_text(encoding="utf-8")

    def test_the_card_paints_a_state_pill(self):
        html = self._html()
        assert "providerState(p)" in html
        assert "state-pill" in html

    def test_the_card_renders_capability_badges(self):
        html = self._html()
        assert "providerCapabilities(p)" in html
        assert "cap-badge" in html

    def test_chat_failing_comes_with_something_to_do_about_it(self):
        """An amber pill that does not say what to change is a nicer way of
        saying nothing."""
        html = self._html()
        assert "note-fix" in html
        assert "providerState(p) === 'chat_failing'" in html

    def test_the_result_lands_on_the_card_that_was_tested(self):
        from pathlib import Path

        js = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js"
        ).read_text(encoding="utf-8")
        assert "card._test = outcome" in js, (
            "one shared result line cannot say which of six providers failed"
        )

    def test_the_styles_exist_for_every_state(self):
        from pathlib import Path

        css = (
            Path(__file__).resolve().parent.parent
            / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
        ).read_text(encoding="utf-8")
        for cls in (
            ".state-pill.state-working",
            ".state-pill.state-chat_failing",
            ".state-pill.state-unreachable",
            ".state-pill.state-untested",
            ".cap-badge.cap-yes",
            ".cap-badge.cap-no",
            ".cap-badge.cap-unknown",
        ):
            assert cls in css, f"{cls} has no styling, so it renders as the default"
