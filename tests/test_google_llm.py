"""Tests for GeminiProvider in kazma_core.google_llm."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from kazma_core.google_llm import GeminiProvider, LLMConfig


class TestGeminiProviderProjectResolution:
    """Verify that GeminiProvider.resolve_project follows the correct priorities."""

    @pytest.fixture(autouse=True)
    def clean_env(self):
        """Clean GCP environment variables before and after each test."""
        vars_to_clean = ["GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", "GOOGLE_API_KEY", "GEMINI_API_KEY"]
        saved = {v: os.environ.get(v) for v in vars_to_clean}
        for v in vars_to_clean:
            if v in os.environ:
                del os.environ[v]
        yield
        for v, val in saved.items():
            if val is not None:
                os.environ[v] = val
            elif v in os.environ:
                del os.environ[v]

    def test_resolve_from_explicit_kwarg(self):
        """kwarg project_id has highest priority."""
        provider = GeminiProvider(LLMConfig(), project_id="explicit-kwarg-project")
        assert provider._resolve_project() == "explicit-kwarg-project"

    def test_resolve_from_google_cloud_project_env(self):
        """GOOGLE_CLOUD_PROJECT is used if kwarg is missing."""
        os.environ["GOOGLE_CLOUD_PROJECT"] = "env-project-1"
        provider = GeminiProvider(LLMConfig())
        assert provider._resolve_project() == "env-project-1"

    def test_resolve_from_cloudsdk_core_project_env(self):
        """CLOUDSDK_CORE_PROJECT is used if GOOGLE_CLOUD_PROJECT and kwarg are missing."""
        os.environ["CLOUDSDK_CORE_PROJECT"] = "env-project-2"
        provider = GeminiProvider(LLMConfig())
        assert provider._resolve_project() == "env-project-2"

    def test_resolve_from_google_auth_default(self):
        """Fallback to google.auth.default() if env variables are empty."""
        mock_auth = MagicMock()
        mock_auth.default.return_value = (None, "auth-default-project")
        with patch("google.auth.default", mock_auth.default):
            provider = GeminiProvider(LLMConfig())
            assert provider._resolve_project() == "auth-default-project"

    def test_resolve_from_gcloud_cli_fallback(self):
        """Fallback to running `gcloud config get-value project` subprocess."""
        mock_auth = MagicMock(side_effect=Exception("Auth error"))
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "gcloud-cli-project\n"

        with patch("google.auth.default", mock_auth), \
             patch("subprocess.run", mock_run), \
             patch("pathlib.Path.exists", return_value=False):
            provider = GeminiProvider(LLMConfig())
            assert provider._resolve_project() == "gcloud-cli-project"
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "gcloud" in args
            assert "project" in args

    def test_resolve_raises_value_error_on_failure(self):
        """ValueError is raised if all project resolution methods fail."""
        mock_auth = MagicMock(side_effect=Exception("Auth error"))
        mock_run = MagicMock()
        mock_run.return_value.returncode = 1  # subprocess failure

        with patch("google.auth.default", mock_auth), \
             patch("subprocess.run", mock_run), \
             patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(ValueError, match="GCP project ID not set"):
                provider = GeminiProvider(LLMConfig())
                provider._resolve_project()
