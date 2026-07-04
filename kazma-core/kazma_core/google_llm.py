"""Google Vertex AI Gemini Client — reusable, project-agnostic wrapper.

Authentication is handled exclusively via Application Default
Credentials (ADC).  No API keys, no service-account JSON files.

This module provides two integration levels:

1. **GoogleGeminiClient** — standalone Vertex AI SDK wrapper for direct
   `generate_text()` calls using the ``vertexai`` SDK.

2. **GeminiProvider** — subclass of ``LLMProvider`` that plugs into
   Kazma's provider dispatch system.  Uses Vertex AI's OpenAI-compatible
   REST endpoint so all existing chat/tool/streaming code paths work
   without modification.

Typical usage (standalone)::

    from kazma_core.google_llm import GoogleGeminiClient

    client = GoogleGeminiClient(project_id="my-gcp-project")
    reply = client.generate_text("Explain monads in one paragraph.")

Typical usage (integrated with Kazma provider system)::

    from kazma_core.model_registry import get_model_registry

    registry = get_model_registry()
    registry.set_active_provider("google", model="gemini-2.5-flash")
    provider = registry.get_client()
    response = await provider.chat([{"role": "user", "content": "Hello"}])
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

from kazma_core.llm_provider import LLMConfig, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────
_DEFAULT_LOCATION: str = "us-central1"
_DEFAULT_MODEL: str = "gemini-2.5-flash"
_DEFAULT_TEMPERATURE: float = 0.2


class GeminiAPIError(Exception):
    """Recoverable failure from the Vertex AI Gemini API."""


class GoogleGeminiClient:
    """Production-grade, reusable Gemini model client.

    Authenticates via Application Default Credentials only — compliant
    with corporate policies that prohibit plain-text API keys.

    Args:
        project_id: GCP project ID (required).
        location: Vertex AI region.  Defaults to ``us-central1``.
        default_model: Model identifier used when no model override is
            passed to ``generate_text``.  Defaults to ``gemini-2.5-flash``.
    """

    def __init__(
        self,
        project_id: str,
        location: str = _DEFAULT_LOCATION,
        default_model: str = _DEFAULT_MODEL,
    ) -> None:
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required for Vertex AI ADC auth")

        self._project_id = project_id
        self._location = location
        self._default_model = default_model

        # ── Bootstrap Vertex AI with ADC ───────────────────────────
        try:
            vertexai.init(project=project_id, location=location)
        except Exception as exc:
            raise GeminiAPIError(
                f"Failed to initialise Vertex AI (check ADC / gcloud auth): {exc}"
            ) from exc

        logger.info(
            "GoogleGeminiClient ready | project=%s location=%s model=%s",
            project_id,
            location,
            default_model,
        )

    # ── Public API ───────────────────────────────────────────────────

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float = _DEFAULT_TEMPERATURE,
        model: str | None = None,
    ) -> str:
        """Send a prompt to Gemini and return the text response.

        Args:
            prompt: The user-facing content to send.
            system_instruction: Optional high-level behavioural directive
                passed as the model's system prompt.
            temperature: Sampling temperature (0.0 – 1.0).  Lower values
                produce more deterministic output.
            model: Override the default model for this call only.

        Returns:
            The model's text response, stripped of surrounding whitespace.

        Raises:
            GeminiAPIError: When the API call fails, credentials are
                invalid, or the response contains no text.
        """
        model_name = model or self._default_model
        gen_model = GenerativeModel(model_name)
        config = GenerationConfig(temperature=temperature)

        logger.debug("Calling %s (temp=%.2f, prompt_len=%d)", model_name, temperature, len(prompt))

        try:
            response = gen_model.generate_content(
                contents=prompt,
                generation_config=config,
                system_instruction=system_instruction,
            )
        except Exception as exc:
            logger.exception("Gemini API call failed | model=%s", model_name)
            raise GeminiAPIError(f"Gemini generation failed: {exc}") from exc

        return self._extract_text(response)

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Safely extract the text payload from a Gemini API response."""
        if response is None:
            raise GeminiAPIError("Gemini returned a null response")

        candidates: list[Any] = getattr(response, "candidates", None) or []
        if not candidates:
            raise GeminiAPIError("Gemini response contains no candidates")

        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        if content is None:
            raise GeminiAPIError("Candidate payload is empty")

        # Primary path: iterate over ``content.parts``.
        parts: list[Any] = getattr(content, "parts", None) or []
        texts: list[str] = []
        for part in parts:
            chunk = getattr(part, "text", None)
            if chunk:
                texts.append(chunk)

        # Fallback: some response shapes expose ``content.text`` directly.
        if not texts:
            fallback = getattr(content, "text", None)
            if fallback:
                return fallback.strip()
            raise GeminiAPIError("All candidate parts were empty")

        return "".join(texts).strip()


# ══════════════════════════════════════════════════════════════════════
#  GeminiProvider — Kazma LLMProvider integration
# ══════════════════════════════════════════════════════════════════════

# Vertex AI OpenAI-compatible endpoint template.
#   {location}-aiplatform.googleapis.com/v1beta1/projects/{project}/
#   locations/{location}/endpoints/openapi
_VERTEX_OPENAI_TEMPLATE: str = (
    "https://{location}-aiplatform.googleapis.com/v1beta1/"
    "projects/{project}/locations/{location}/endpoints/openapi"
)


class GeminiProvider(LLMProvider):
    """Kazma LLMProvider subclass for Vertex AI Gemini.

    Uses Application Default Credentials for authentication — no API
    keys.  Communicates via Vertex AI's OpenAI-compatible REST endpoint
    so that all existing chat, tool-calling, and streaming code paths
    work without modification.

    Args:
        config: Standard ``LLMConfig``.  ``api_key`` is ignored (ADC
            provides the bearer token).  ``base_url`` is overridden with
            the Vertex AI OpenAI-compatible endpoint.
        project_id: GCP project ID.  If empty, resolved from ADC.
        location: Vertex AI region.  Defaults to ``us-central1``.
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        project_id: str = "",
        location: str = _DEFAULT_LOCATION,
    ) -> None:
        self._gcp_project = project_id
        self._gcp_location = location

        # Derive the Vertex AI OpenAI-compatible base URL.
        resolved_project = self._resolve_project()
        config = config or LLMConfig()
        config.base_url = _VERTEX_OPENAI_TEMPLATE.format(
            location=location,
            project=resolved_project,
        )

        super().__init__(config)
        logger.info(
            "GeminiProvider ready | project=%s location=%s endpoint=%s",
            resolved_project, location, config.base_url,
        )

    # ── Overrides ─────────────────────────────────────────────────

    def _resolve_api_key(self) -> None:
        """ADC provides the auth token — skip the API key resolution."""
        # The bearer token is obtained on-demand via _get_client().
        # We set a placeholder so the parent doesn't error on missing key.
        self.config.api_key = "adc-placeholder"

    async def _get_client(self) -> httpx.AsyncClient:
        """Return an httpx client authenticated with an ADC bearer token.

        The token is refreshed on every call to ensure it never expires
        mid-session.
        """
        token: str
        try:
            import google.auth
            import google.auth.transport.requests

            credentials, _project = google.auth.default()
            auth_req = google.auth.transport.requests.Request()
            credentials.refresh(auth_req)
            token = credentials.token
        except Exception as exc:
            logger.exception("Failed to obtain ADC credentials")
            raise RuntimeError(
                "ADC authentication failed.  Run: gcloud auth application-default login"
            ) from exc

        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.config.base_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        else:
            # Update the token on the existing client.
            self._http.headers["Authorization"] = f"Bearer {token}"

        return self._http

    # ── Helpers ──────────────────────────────────────────────────

    def _resolve_project(self) -> str:
        """Resolve the GCP project ID.

        Priority: explicit kwarg > GOOGLE_CLOUD_PROJECT env > ADC project
        > ADC quota_project_id > gcloud config_default file.
        """
        import os
        import json
        from pathlib import Path

        if self._gcp_project:
            return self._gcp_project
        env_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if env_project:
            self._gcp_project = env_project
            return env_project
        try:
            import google.auth
            _, project = google.auth.default()
            if project:
                self._gcp_project = project
                return project
        except Exception as exc:
            logger.debug("google.auth.default() failed: %s", exc)
        # Fallback 1: read quota_project_id from ADC credentials file
        try:
            adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
            if not adc_path.exists():
                adc_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
            if adc_path.exists():
                data = json.loads(adc_path.read_text(encoding="utf-8"))
                quota_project = data.get("quota_project_id") or data.get("project_id") or ""
                if quota_project:
                    self._gcp_project = quota_project
                    logger.info("Resolved GCP project from ADC credentials: %s", quota_project)
                    return quota_project
        except Exception as exc:
            logger.debug("ADC credentials file read failed: %s", exc)
        # Fallback 2: read project from gcloud config_default file
        try:
            config_path = Path.home() / ".config" / "gcloud" / "configurations" / "config_default"
            if not config_path.exists():
                config_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "configurations" / "config_default"
            if config_path.exists():
                for line in config_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("project"):
                        _, _, project = line.partition("=")
                        project = project.strip()
                        if project:
                            self._gcp_project = project
                            logger.info("Resolved GCP project from gcloud config: %s", project)
                            return project
        except Exception as exc:
            logger.debug("gcloud config_default read failed: %s", exc)
        raise ValueError(
            "GCP project ID not set.  Pass project_id=, set GOOGLE_CLOUD_PROJECT, "
            "or run: gcloud auth application-default login --project=<your-project>"
        )
