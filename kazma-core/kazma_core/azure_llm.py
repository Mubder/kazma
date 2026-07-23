"""Azure OpenAI provider.

Azure's OpenAI service is OpenAI-compatible but differs in auth:
  * Header ``api-key: <key>`` (NOT ``Authorization: Bearer``)
  * URL ``https://<resource>.openai.azure.com/openai/deployments/<deployment>``
  * Required ``api-version`` query parameter (e.g. ``2024-10-21``)

Configuration keys (from the provider entry or env):
  * ``AZURE_OPENAI_ENDPOINT`` — full resource endpoint, e.g.
    ``https://myresource.openai.azure.com`` (or set ``base_url``).
  * ``AZURE_OPENAI_API_KEY`` / ``api_key`` — the Azure key.
  * ``AZURE_OPENAI_API_VERSION`` / ``api_version`` — e.g. ``2024-10-21``.
  * ``AZURE_OPENAI_DEPLOYMENT`` / ``model`` — the deployment name (also used
    as the model id in Azure's path-based routing).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from kazma_core.llm_provider import LLMConfig, LLMProvider

logger = logging.getLogger(__name__)

_DEFAULT_API_VERSION = "2024-10-21"


class AzureProvider(LLMProvider):
    """OpenAI-compatible Azure OpenAI client (api-key header + api-version)."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        super().__init__(config)
        endpoint = (
            os.getenv("AZURE_OPENAI_ENDPOINT", "")
            or self.config.base_url
        ).rstrip("/")
        self._endpoint = endpoint
        self._api_version = (
            os.getenv("AZURE_OPENAI_API_VERSION", "")
            or getattr(self.config, "api_version", "")
            or _DEFAULT_API_VERSION
        )
        if not self.config.api_key or self.config.api_key == "not-needed":
            self.config.api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        # The deployment doubles as the model id in Azure path routing.
        self._deployment = (
            os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
            or self.config.model
        )
        self._http: httpx.AsyncClient | None = None
        logger.info(
            "AzureProvider initialized: endpoint=%s deployment=%s api_version=%s",
            self._endpoint, self._deployment, self._api_version,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Return an httpx client whose base_url points at the deployment."""
        if self._http is None:
            # Build the deployment-scoped base URL so the LLMProvider.chat()
            # payload posts to .../chat/completions?api-version=...
            base = (
                f"{self._endpoint}/openai/deployments/{self._deployment}"
                if self._endpoint
                else "https://api.openai.com/v1"
            )
            self._http = httpx.AsyncClient(
                base_url=base,
                headers={
                    "api-key": self.config.api_key,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                params={"api-version": self._api_version},
            )
        return self._http

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ):  # type: ignore[override]
        """Chat via Azure — same payload as OpenAI, but deployment-scoped URL."""
        # Azure ignores the `model` field (routing is path-based on the
        # deployment), so we can reuse the parent's chat() with the Azure
        # client. The parent reads self.config.model for the payload model
        # field, which Azure accepts but ignores.
        return await super().chat(
            messages, tools, max_tokens, temperature,
            model=self._deployment,  # keep deployment as the model id
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
