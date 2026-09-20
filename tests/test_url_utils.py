"""Tests for URL normalization utilities.

Covers:
  - normalize_provider_url: scheme, trailing slashes, /v1 appending, Ollama exception
  - normalize_model_name: provider prefix detection
  - get_dummy_api_key: local vs remote key resolution
"""

from __future__ import annotations

from kazma_core.url_utils import get_dummy_api_key, normalize_model_name, normalize_provider_url

# ═══════════════════════════════════════════════════════════════════
# normalize_provider_url
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeProviderUrl:
    """Tests for URL normalization."""

    def test_adds_http_scheme(self):
        assert normalize_provider_url("localhost:1234") == "http://localhost:1234/v1"

    def test_strips_trailing_slash(self):
        assert normalize_provider_url("http://localhost:1234/v1/") == "http://localhost:1234/v1"

    def test_preserves_existing_v1(self):
        assert normalize_provider_url("http://localhost:1234/v1") == "http://localhost:1234/v1"

    def test_appends_v1_when_missing(self):
        assert normalize_provider_url("http://localhost:1234") == "http://localhost:1234/v1"

    def test_ollama_no_v1_append(self):
        # Ollama on port 11434 should NOT get /v1 appended
        result = normalize_provider_url("http://localhost:11434")
        assert result == "http://localhost:11434"

    def test_ollama_with_v1_preserved(self):
        # If user explicitly sets /v1 for Ollama, keep it
        result = normalize_provider_url("http://localhost:11434/v1")
        assert result == "http://localhost:11434/v1"

    def test_litellm_no_v1_append(self):
        result = normalize_provider_url("http://localhost:4000")
        assert result == "http://localhost:4000"

    def test_openai_cloud_unchanged(self):
        result = normalize_provider_url("https://api.openai.com/v1")
        assert result == "https://api.openai.com/v1"

    def test_empty_string(self):
        assert normalize_provider_url("") == ""

    def test_whitespace_only(self):
        assert normalize_provider_url("   ") == ""

    def test_ip_address(self):
        result = normalize_provider_url("192.168.1.100:8080")
        assert result == "http://192.168.1.100:8080/v1"

    def test_https_preserved(self):
        result = normalize_provider_url("https://my-server.com:8080/v1")
        assert result == "https://my-server.com:8080/v1"

    def test_no_port(self):
        result = normalize_provider_url("http://my-server.com")
        assert result == "http://my-server.com/v1"

    def test_ensure_v1_false(self):
        result = normalize_provider_url("http://localhost:1234", ensure_v1=False)
        assert result == "http://localhost:1234"

    def test_lm_studio_typical(self):
        result = normalize_provider_url("http://localhost:1234/v1")
        assert result == "http://localhost:1234/v1"

    def test_bare_localhost(self):
        result = normalize_provider_url("localhost")
        assert result == "http://localhost/v1"


# ═══════════════════════════════════════════════════════════════════
# normalize_model_name
# ═══════════════════════════════════════════════════════════════════


class TestNormalizeModelName:
    """Tests for model name normalization."""

    def test_already_prefixed(self):
        assert normalize_model_name("openai/gpt-4o-mini") == "openai/gpt-4o-mini"
        assert normalize_model_name("ollama/llama3.2") == "ollama/llama3.2"

    def test_lm_studio_model(self):
        result = normalize_model_name("local-model", "http://localhost:1234/v1")
        assert result == "openai/local-model"

    def test_ollama_model(self):
        result = normalize_model_name("llama3.2", "http://localhost:11434")
        assert result == "ollama/llama3.2"

    def test_openai_cloud_unchanged(self):
        result = normalize_model_name("gpt-4o-mini", "https://api.openai.com/v1")
        assert result == "gpt-4o-mini"

    def test_empty_model(self):
        assert normalize_model_name("", "http://localhost:1234/v1") == ""

    def test_empty_url(self):
        assert normalize_model_name("gpt-4o-mini", "") == "gpt-4o-mini"

    def test_localhost_generic_port(self):
        result = normalize_model_name("my-model", "http://localhost:8080/v1")
        assert result == "openai/my-model"


# ═══════════════════════════════════════════════════════════════════
# get_dummy_api_key
# ═══════════════════════════════════════════════════════════════════


class TestGetDummyApiKey:
    """Tests for API key resolution."""

    def test_configured_key_preserved(self):
        result = get_dummy_api_key("http://localhost:1234/v1", "sk-real-key")
        assert result == "sk-real-key"

    def test_lm_studio_gets_dummy(self):
        result = get_dummy_api_key("http://localhost:1234/v1", "")
        assert result == "sk-lm-studio-dummy-key"

    def test_ollama_gets_dummy(self):
        result = get_dummy_api_key("http://localhost:11434", "")
        assert result == "ollama"

    def test_litellm_gets_dummy(self):
        result = get_dummy_api_key("http://localhost:4000", "")
        assert result == "sk-litellm-dummy-key"

    def test_empty_url(self):
        result = get_dummy_api_key("", "")
        assert result == "not-needed"

    def test_remote_needs_real_key(self):
        result = get_dummy_api_key("https://api.openai.com/v1", "")
        assert result == ""

    def test_whitespace_key_treated_as_empty(self):
        result = get_dummy_api_key("http://localhost:1234/v1", "   ")
        assert result == "sk-lm-studio-dummy-key"


class TestAlreadyVersionedPathsAreLeftAlone:
    """`/v1` is not the only version a provider can serve.

    Z.AI's OpenAI-compatible API lives at `/api/paas/v4`. The normaliser
    appended `/v1` to anything not ending in `/v1`, producing
    `/api/paas/v4/v1/chat/completions` — a 404 on every single call. The
    provider was configured, enabled and paid for, and every request failed.

    The same bug had already been patched once for Google (`/v1beta/openai`),
    by adding a hostname exemption rather than fixing the rule. Two vendors is
    enough: any trailing `/v<N>` segment now counts as already versioned.
    """

    def test_a_v4_api_is_not_given_a_v1_suffix(self):
        from kazma_core.url_utils import normalize_provider_url

        assert (
            normalize_provider_url("https://api.z.ai/api/paas/v4/")
            == "https://api.z.ai/api/paas/v4"
        )

    def test_other_versions_too(self):
        from kazma_core.url_utils import normalize_provider_url

        for path in ("v2", "v3", "v4", "v10"):
            url = f"https://api.example.com/{path}"
            assert normalize_provider_url(url) == url, path

    def test_v1beta_style_versions_survive(self):
        """Google's is `/v1beta/openai`; the hostname exemption covered that
        one, but the rule should not depend on recognising the vendor."""
        from kazma_core.url_utils import normalize_provider_url

        assert (
            normalize_provider_url("https://api.example.com/v1beta")
            == "https://api.example.com/v1beta"
        )

    def test_an_unversioned_gateway_still_gets_v1(self):
        """The fix must not stop the normaliser doing its job."""
        from kazma_core.url_utils import normalize_provider_url

        assert (
            normalize_provider_url("https://gw.example.com/api")
            == "https://gw.example.com/api/v1"
        )
        assert normalize_provider_url("localhost:1234") == "http://localhost:1234/v1"

    def test_the_v1_dedupe_still_works(self):
        from kazma_core.url_utils import normalize_provider_url

        assert (
            normalize_provider_url("https://api.openai.com/v1/v1")
            == "https://api.openai.com/v1"
        )
