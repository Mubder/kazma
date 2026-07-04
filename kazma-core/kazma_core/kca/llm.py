"""KCA LLM — Vertex AI Gemini client via Application Default Credentials.

Corporate security policy mandates ADC-based authentication.
No API keys. No service-account JSON files on disk.
Machine identity or `gcloud auth application-default login` only.
"""

from __future__ import annotations

import logging
from typing import Any

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────
_DEFAULT_LOCATION: str = "us-central1"
_DEFAULT_MODEL: str = "gemini-2.5-flash"
_DEFAULT_TEMPERATURE: float = 0.3


class GeminiClientError(Exception):
    """Raised when the GeminiClient encounters a recoverable failure."""


class GeminiClient:
    """Production-grade Gemini model wrapper backed by Vertex AI.

    Authenticates exclusively via Application Default Credentials.
    No API keys. No service-account key files.

    Args:
        project_id: GCP project ID (required).
        location: Vertex AI region. Defaults to ``us-central1``.
        model_name: Gemini model identifier. Defaults to ``gemini-2.5-flash``.
    """

    def __init__(
        self,
        project_id: str,
        location: str = _DEFAULT_LOCATION,
        model_name: str = _DEFAULT_MODEL,
    ) -> None:
        if not project_id:
            raise ValueError("project_id is required for Vertex AI ADC auth")

        self._project_id: str = project_id
        self._location: str = location
        self._model_name: str = model_name

        # ── Initialise Vertex AI with ADC ──────────────────────────
        vertexai.init(project=project_id, location=location)
        self._model = GenerativeModel(model_name)
        logger.info(
            "GeminiClient initialised: project=%s location=%s model=%s",
            project_id,
            location,
            model_name,
        )

    # ── Public API ───────────────────────────────────────────────────

    def generate(
        self,
        system_instruction: str,
        prompt: str,
        temperature: float = _DEFAULT_TEMPERATURE,
    ) -> str:
        """Send a prompt to Gemini and return the cleaned text response.

        Args:
            system_instruction: High-level behavioural directive for the model.
            prompt: The user-facing content or analysis request.
            temperature: Sampling temperature (0.0–1.0).

        Returns:
            The model's text response with leading/trailing whitespace stripped.

        Raises:
            GeminiClientError: When the API call fails or returns no content.
        """
        config = GenerationConfig(temperature=temperature)
        try:
            response = self._model.generate_content(
                contents=prompt,
                generation_config=config,
                system_instruction=system_instruction,
            )
        except Exception as exc:
            logger.exception("Gemini API call failed")
            raise GeminiClientError(
                f"Gemini generation failed: {exc}"
            ) from exc

        return self._extract_text(response)

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Safely pull the text payload from a Gemini response object."""
        if not response:
            raise GeminiClientError("Gemini returned an empty response")

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise GeminiClientError("Gemini response contains no candidates")

        candidate = candidates[0]
        content = getattr(candidate, "content", None)
        if content is None:
            raise GeminiClientError("Candidate has no content")

        parts = getattr(content, "parts", None) or []
        if not parts:
            # Fallback: `content.text` might be available directly.
            fallback = getattr(content, "text", None)
            if fallback:
                return fallback.strip()
            raise GeminiClientError("Gemini response has no text parts")

        # Concatenate all part texts.
        texts: list[str] = []
        for part in parts:
            chunk = getattr(part, "text", None)
            if chunk:
                texts.append(chunk)

        if not texts:
            raise GeminiClientError("All response parts were empty")

        return "".join(texts).strip()
