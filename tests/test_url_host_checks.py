"""Two address checks name a host by its host name, never by a substring.

``url_utils.normalize_provider_url`` left /v1 off any host that ended in
"googleapis.com" ("evilgoogleapis.com" too), and the speech-to-text helper
took any address CONTAINING "integrate.api.nvidia.com" for NVIDIA's chat API
(CodeQL py/incomplete-url-substring-sanitization, 2026-11-14). Neither decided
anything security-related; both answered wrongly for such a name.
"""

from __future__ import annotations

from kazma_core.url_utils import normalize_provider_url
from kazma_core.voice.stt import _is_llm_only_nvidia_url


def test_only_google_s_own_hosts_keep_their_paths():
    assert normalize_provider_url("https://generativelanguage.googleapis.com/v1beta/openai") \
        == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert normalize_provider_url("https://us-central1-aiplatform.googleapis.com/endpoints/openapi") \
        == "https://us-central1-aiplatform.googleapis.com/endpoints/openapi"
    assert normalize_provider_url("https://evilgoogleapis.com/x") == "https://evilgoogleapis.com/x/v1"


def test_only_nvidia_s_chat_host_is_nvidia_s_chat_api():
    assert _is_llm_only_nvidia_url("https://integrate.api.nvidia.com/v1")
    assert not _is_llm_only_nvidia_url("https://integrate.api.nvidia.com/v1/asr")
    assert not _is_llm_only_nvidia_url("https://asr.example/integrate.api.nvidia.com/v1")
    assert not _is_llm_only_nvidia_url("https://integrate.api.nvidia.com.example/v1")


def test_control_the_old_substring_checks_answered_wrongly():
    assert "evilgoogleapis.com".endswith("googleapis.com")
    assert "integrate.api.nvidia.com" in "https://asr.example/integrate.api.nvidia.com/v1"
