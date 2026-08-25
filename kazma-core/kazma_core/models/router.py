"""Multi-model routing — selects the best model per task.

Classifies user messages into task profiles (reasoning, coding, fast, default)
and routes to the optimal model. Config-driven via kazma.yaml.

Usage:
    router = ModelRouter.from_config(kazma_yaml["models"])
    profile = ModelRouter.classify("write a Python function to sort a list")
    spec = router.route(profile)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["ModelRouter", "ModelSpec", "TaskProfile", "classify_prompt"]

logger = logging.getLogger(__name__)

_TOKEN_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _has_signal(text: str, keywords: list[str]) -> bool:
    """True when any keyword hits as a word (or a multi-word phrase).

    Single tokens use word boundaries so ``code`` does not match
    ``barcode`` and ``hi`` does not match ``this``.
    """
    for raw in keywords:
        kw = (raw or "").lower()
        if not kw.strip():
            continue
        token = kw.strip()
        if " " in token:
            if token in text:
                return True
            continue
        pat = _TOKEN_RE_CACHE.get(token)
        if pat is None:
            pat = re.compile(rf"\b{re.escape(token)}\b")
            _TOKEN_RE_CACHE[token] = pat
        if pat.search(text):
            return True
    return False


class TaskProfile(StrEnum):
    """Task classification profiles."""

    REASONING = "reasoning"  # Complex analysis, research, planning
    CODING = "coding"  # Code generation, debugging, refactoring
    VISION = "vision"  # Image analysis, diagrams, screenshots
    FAST = "fast"  # Simple Q&A, greetings, status checks
    GENERAL = "general"  # Capable general-purpose work
    DEFAULT = "default"  # Fallback (alias of general for routing)


@dataclass
class ModelSpec:
    """Specification for a model provider."""

    provider: str  # openai, deepseek, anthropic, openrouter, etc.
    model: str  # deepseek-v4-pro, claude-sonnet-4, etc.
    profiles: list[TaskProfile]  # What this model specializes in
    max_tokens: int = 8192
    cost_per_1k_tokens: float = 0.0


class ModelRouter:
    """Routes tasks to the optimal model based on classification.

    Args:
        models:  List of ModelSpec entries.
        default: Default profile to fall back to (default: "default").
    """

    def __init__(self, models: list[ModelSpec], default: str = "default") -> None:
        self._models = models
        self._default = default
        # Build profile → model mapping (first match wins)
        self._profile_map: dict[str, ModelSpec] = {}
        for m in models:
            for profile in m.profiles:
                if profile.value not in self._profile_map:
                    self._profile_map[profile.value] = m
        # Ensure default fallback
        if self._default not in self._profile_map and models:
            self._profile_map[self._default] = models[0]

    def route(self, task_profile: TaskProfile) -> ModelSpec:
        """Return the best model for a given task profile.

        Args:
            task_profile: The classified task profile.

        Returns:
            ModelSpec for the chosen model.
        """
        return self._profile_map.get(
            task_profile.value,
            self._profile_map.get(self._default, self._models[0]),
        )

    @staticmethod
    def classify(message: str) -> TaskProfile:
        """Classify a user message into a task profile using heuristics.

        Args:
            message: The user's message text.

        Returns:
            TaskProfile enum value.
        """
        msg_lower = message.lower().strip()

        # Vision signals — image/photo/diagram analysis. Checked early because
        # vision prompts often also contain analysis words ("analyze this image").
        vision_keywords = [
            "image", "photo", "picture", "screenshot", "diagram",
            "what does this show", "ocr", "read the image", "look at this",
        ]
        if _has_signal(msg_lower, vision_keywords):
            return TaskProfile.VISION

        # Coding signals
        coding_keywords = [
            "code", "function", "bug", "fix", "refactor",
            "python", "class", "import", "def", "test",
            "error", "traceback", "debug", "commit", "git",
            "implement", "write a", "create a", "build a",
        ]
        if _has_signal(msg_lower, coding_keywords):
            return TaskProfile.CODING

        # Reasoning signals
        reasoning_keywords = [
            "why", "explain", "compare", "analyze",
            "architecture", "design", "plan", "strategy",
            "research", "evaluate", "assess", "think",
            "reason", "consider", "trade-off", "pros and cons",
        ]
        if _has_signal(msg_lower, reasoning_keywords):
            return TaskProfile.REASONING

        # Fast signals — short messages, greetings, status checks
        fast_keywords = [
            "hi", "hello", "status", "ok", "thanks", "bye",
            "yes", "no", "ping", "test",
        ]
        if len(message.split()) <= 5 and _has_signal(msg_lower, fast_keywords):
            return TaskProfile.FAST

        # Anything else with substance is general-purpose work. Threshold is
        # >3 words so short-but-substantive requests ("draft a welcome email")
        # classify as GENERAL rather than DEFAULT; the FAST branch above still
        # catches ≤3-word greetings.
        if len(message.split()) > 3:
            return TaskProfile.GENERAL

        return TaskProfile.DEFAULT

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ModelRouter:
        """Build a ModelRouter from kazma.yaml models config.

        Args:
            config: The "models" section of kazma.yaml.

        Returns:
            Configured ModelRouter instance.
        """
        models: list[ModelSpec] = []
        providers = config.get("providers", {})

        for provider_name, provider_cfg in providers.items():
            for model_cfg in provider_cfg.get("models", []):
                profiles = []
                for p in model_cfg.get("profiles", ["default"]):
                    try:
                        profiles.append(TaskProfile(p))
                    except ValueError:
                        profiles.append(TaskProfile.DEFAULT)

                models.append(
                    ModelSpec(
                        provider=provider_name,
                        model=model_cfg["model"],
                        profiles=profiles,
                        max_tokens=model_cfg.get("max_tokens", 8192),
                        cost_per_1k_tokens=model_cfg.get("cost_per_1k_tokens", 0.0),
                    )
                )

        default_model = config.get("default", "default")
        logger.info(
            "[ModelRouter] Loaded %d models across %d providers",
            len(models),
            len(providers),
        )
        return cls(models=models, default=default_model)


def classify_prompt(text: str) -> TaskProfile:
    """Classify a task prompt into a TaskProfile (standalone, no router needed).

    Thin wrapper around :meth:`ModelRouter.classify` so swarm callers (autoscaler,
    worker model selection) can classify a prompt without constructing a full
    ModelRouter. Returns GENERAL for empty/None input instead of DEFAULT, so an
    auto-spawned worker always gets a usable capability hint.
    """
    if not text or not str(text).strip():
        return TaskProfile.GENERAL
    return ModelRouter.classify(str(text))

