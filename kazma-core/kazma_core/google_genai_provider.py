"""Unified Google Provider module for Kazma.

Utilises the official, modern ``google-genai`` Python SDK to support both
Google AI Studio (Gemini API) and Vertex AI (Google Cloud Platform) seamlessly,
without rigid proxy routing prefixes.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── Configuration Defaults ────────────────────────────────────────────
_DEFAULT_LOCATION: str = "us-central1"
_DEFAULT_TEXT_MODEL: str = "gemini-2.5-flash"
_DEFAULT_CODE_MODEL: str = "gemini-2.5-pro"


class GoogleProviderError(Exception):
    """Base exception for all Google Provider operations."""


# ── Pydantic Configuration Model ──────────────────────────────────────

class GoogleProviderConfig(BaseModel):
    """Configuration model for the unified Google Provider."""

    google_mode: Literal["ai_studio", "vertex_ai"] = Field(
        default="ai_studio",
        description="The active Google product mode: 'ai_studio' or 'vertex_ai'.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Developer API key used exclusively for Google AI Studio.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="GCP Project ID required for Vertex AI authentication.",
    )
    location: str = Field(
        default=_DEFAULT_LOCATION,
        description="Vertex AI region / location (e.g. 'us-central1').",
    )
    default_model: str = Field(
        default=_DEFAULT_TEXT_MODEL,
        description="The fallback model for standard text generation tasks.",
    )
    default_code_model: str = Field(
        default=_DEFAULT_CODE_MODEL,
        description="The model utilized for advanced code assist / reasoning tasks.",
    )


# ── Initialization Logic ──────────────────────────────────────────────

def initialize_google_provider(config_dict: dict[str, Any]) -> genai.Client:
    """Initialize the unified `genai.Client` based on user-supplied database configuration.

    Args:
        config_dict: Saved configuration dictionary retrieved from the database.

    Returns:
        A fully initialized, thread-safe genai.Client.

    Raises:
        GoogleProviderError: If required credential attributes are missing.
    """
    try:
        config = GoogleProviderConfig(**config_dict)
    except Exception as exc:
        raise GoogleProviderError(f"Invalid Google Provider configuration: {exc}") from exc

    try:
        if config.google_mode == "ai_studio":
            api_key = config.api_key or ""
            if not api_key:
                import os
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""

            if not api_key:
                raise GoogleProviderError(
                    "API Key is required for Google AI Studio mode. "
                    "Please provide an 'api_key' in your configuration or set the "
                    "GEMINI_API_KEY environment variable."
                )

            logger.info("Initializing Google AI Studio Client (api_key present)")
            return genai.Client(api_key=api_key)

        elif config.google_mode == "vertex_ai":
            project_id = config.project_id or ""
            if not project_id:
                # Attempt to resolve from active environment variables or ADC context
                import os
                project_id = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""

            logger.info(
                "Initializing Vertex AI Client | project=%s location=%s",
                project_id or "Auto-Resolved",
                config.location,
            )
            # The SDK uses ADC (Application Default Credentials) under the hood
            return genai.Client(
                vertexai=True,
                project=project_id or None,
                location=config.location,
            )
        else:
            raise GoogleProviderError(f"Unsupported google_mode: {config.google_mode}")

    except Exception as exc:
        if isinstance(exc, GoogleProviderError):
            raise
        raise GoogleProviderError(f"Failed to bootstrap unified Google Client: {exc}") from exc


# ── Execution Wrappers ────────────────────────────────────────────────

def generate_text(
    client: genai.Client,
    prompt: str,
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    temperature: float = 0.2,
) -> str:
    """Send a prompt to the Google LLM and return the text response.

    Args:
        client: The active initialized genai.Client.
        prompt: The user-facing prompt / content payload.
        model: Override default text model (e.g. 'gemini-2.5-flash').
        system_instruction: High-level behavioural directive or system prompt.
        temperature: Sampling temperature (0.0 to 1.0).

    Returns:
        The model's text response, stripped of surrounding whitespace.
    """
    selected_model = model or _DEFAULT_TEXT_MODEL
    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_instruction,
    )

    logger.debug(
        "Calling generate_text | model=%s temp=%.2f prompt_len=%d",
        selected_model,
        temperature,
        len(prompt),
    )

    try:
        response = client.models.generate_content(
            model=selected_model,
            contents=prompt,
            config=config,
        )
        if response.text is None:
            raise GoogleProviderError("Model returned an empty text payload.")
        return response.text.strip()
    except Exception as exc:
        logger.exception("Unified generate_text call failed")
        raise GoogleProviderError(f"Google generation failed: {exc}") from exc


def generate_code(
    client: genai.Client,
    prompt: str,
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    temperature: float = 0.0,
) -> str:
    """A specialized Code Assist generation call with Google's code_execution tool enabled.

    Enables the model to write, compile, run, and iteratively self-correct Python code
    internally before returning the final solution.

    Args:
        client: The active initialized genai.Client.
        prompt: The programming / reasoning prompt to execute.
        model: Override default code/reasoning model (recommends 'gemini-2.5-pro').
        system_instruction: High-level system prompt directives.
        temperature: Sampling temperature. Defaults to 0.0 for deterministic coding.

    Returns:
        The final markdown / code response returned by the model.
    """
    selected_model = model or _DEFAULT_CODE_MODEL
    
    # Instantiate the official Google code_execution tool
    code_execution_tool = types.Tool(code_execution=types.ToolCodeExecution())
    
    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_instruction,
        tools=[code_execution_tool],
    )

    logger.debug(
        "Calling generate_code (Code Assist) | model=%s temp=%.2f code_exec=enabled",
        selected_model,
        temperature,
    )

    try:
        response = client.models.generate_content(
            model=selected_model,
            contents=prompt,
            config=config,
        )
        if response.text is None:
            raise GoogleProviderError("Model returned an empty code payload.")
        return response.text.strip()
    except Exception as exc:
        logger.exception("Unified generate_code (Code Assist) call failed")
        raise GoogleProviderError(f"Google code generation failed: {exc}") from exc
