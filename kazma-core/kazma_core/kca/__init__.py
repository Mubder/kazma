"""KCA — Kuwait Corporate Atlas Gemini integration layer.

Production-grade Vertex AI SDK wrapper for Google Gemini models,
using Application Default Credentials (ADC) for corporate security compliance.
"""

from kazma_core.kca.llm import GeminiClient
from kazma_core.kca.custom_agents import FounderAnalyst, KCABaseAgent, RiskGuardian

__all__ = ["GeminiClient", "KCABaseAgent", "FounderAnalyst", "RiskGuardian"]
