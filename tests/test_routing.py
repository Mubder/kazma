"""Tests for multi-model routing (gw-028).

8 tests:
    1. Code keywords → CODING profile
    2. "explain architecture" → REASONING
    3. "hello" → FAST
    4. Generic message → DEFAULT
    5. Coding profile → coding model
    6. Reasoning profile → reasoning model
    7. Models list loaded from config
    8. Fallback on failure
"""

from __future__ import annotations

from kazma_core.models.router import ModelRouter, ModelSpec, TaskProfile


class TestClassify:
    """Test message classification."""

    def test_classify_coding(self) -> None:
        """Test 1: Code keywords → CODING profile."""
        assert ModelRouter.classify("write a Python function") == TaskProfile.CODING
        assert ModelRouter.classify("fix this bug in the code") == TaskProfile.CODING
        assert ModelRouter.classify("debug the traceback error") == TaskProfile.CODING
        assert ModelRouter.classify("refactor this class") == TaskProfile.CODING
        assert ModelRouter.classify("git commit the changes") == TaskProfile.CODING

    def test_classify_reasoning(self) -> None:
        """Test 2: Reasoning keywords → REASONING."""
        assert ModelRouter.classify("explain the architecture") == TaskProfile.REASONING
        assert ModelRouter.classify("why does this work?") == TaskProfile.REASONING
        assert ModelRouter.classify("compare these two approaches") == TaskProfile.REASONING
        assert ModelRouter.classify("analyze the trade-offs") == TaskProfile.REASONING
        assert ModelRouter.classify("plan the strategy") == TaskProfile.REASONING

    def test_classify_fast(self) -> None:
        """Test 3: Short greetings → FAST."""
        assert ModelRouter.classify("hello") == TaskProfile.FAST
        assert ModelRouter.classify("hi") == TaskProfile.FAST
        assert ModelRouter.classify("status") == TaskProfile.FAST
        assert ModelRouter.classify("thanks") == TaskProfile.FAST

    def test_classify_default(self) -> None:
        """Test 4: Generic messages → DEFAULT."""
        assert ModelRouter.classify("tell me about Kuwait") == TaskProfile.DEFAULT
        assert ModelRouter.classify("what's the weather like today?") == TaskProfile.DEFAULT


class TestRoute:
    """Test model routing."""

    def test_route_coding(self) -> None:
        """Test 5: Coding profile → coding model."""
        models = [
            ModelSpec(provider="deepseek", model="deepseek-v4-pro", profiles=[TaskProfile.CODING]),
            ModelSpec(provider="openrouter", model="claude-sonnet-4", profiles=[TaskProfile.REASONING]),
            ModelSpec(provider="deepseek", model="deepseek-chat", profiles=[TaskProfile.FAST, TaskProfile.DEFAULT]),
        ]
        router = ModelRouter(models=models)
        spec = router.route(TaskProfile.CODING)
        assert spec.model == "deepseek-v4-pro"
        assert spec.provider == "deepseek"

    def test_route_reasoning(self) -> None:
        """Test 6: Reasoning profile → reasoning model."""
        models = [
            ModelSpec(provider="deepseek", model="deepseek-v4-pro", profiles=[TaskProfile.CODING]),
            ModelSpec(provider="openrouter", model="claude-sonnet-4", profiles=[TaskProfile.REASONING]),
            ModelSpec(provider="deepseek", model="deepseek-chat", profiles=[TaskProfile.FAST, TaskProfile.DEFAULT]),
        ]
        router = ModelRouter(models=models)
        spec = router.route(TaskProfile.REASONING)
        assert spec.model == "claude-sonnet-4"

    def test_route_fallback(self) -> None:
        """Unknown profile falls back to default."""
        models = [
            ModelSpec(provider="deepseek", model="deepseek-chat", profiles=[TaskProfile.DEFAULT]),
        ]
        router = ModelRouter(models=models)
        spec = router.route(TaskProfile.REASONING)
        assert spec.model == "deepseek-chat"


class TestFromConfig:
    """Test 7: Models list loaded from config."""

    def test_from_config(self) -> None:
        config = {
            "default": "default",
            "providers": {
                "deepseek": {
                    "models": [
                        {"model": "deepseek-v4-pro", "profiles": ["reasoning", "coding"], "max_tokens": 16384},
                        {"model": "deepseek-chat", "profiles": ["fast"], "max_tokens": 4096},
                    ]
                },
                "openrouter": {
                    "models": [
                        {"model": "claude-sonnet-4", "profiles": ["reasoning", "coding"]},
                    ]
                },
            },
        }
        router = ModelRouter.from_config(config)

        # Coding → deepseek-v4-pro (first in list for coding)
        spec = router.route(TaskProfile.CODING)
        assert spec.provider == "deepseek"
        assert spec.model == "deepseek-v4-pro"

        # Fast → deepseek-chat
        spec = router.route(TaskProfile.FAST)
        assert spec.model == "deepseek-chat"


class TestFallback:
    """Test 8: Fallback on failure."""

    def test_fallback_to_default(self) -> None:
        """When no model matches profile, fall back to default."""
        models = [
            ModelSpec(provider="local", model="local-model", profiles=[TaskProfile.DEFAULT]),
        ]
        router = ModelRouter(models=models, default="default")

        # All profiles should fall back to the default model
        for profile in TaskProfile:
            spec = router.route(profile)
            assert spec.model == "local-model"
