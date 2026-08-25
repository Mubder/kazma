"""Langfuse auto-on when keys exist."""

from __future__ import annotations

from kazma_core.tracing.langfuse_enable import resolve_langfuse_enabled


def test_auto_with_keys() -> None:
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk",
        "LANGFUSE_SECRET_KEY": "sk",
    }
    assert resolve_langfuse_enabled(
        {"langfuse": {"enabled": "auto"}}, environ=env
    ) is True


def test_auto_without_keys() -> None:
    env: dict[str, str] = {}
    assert resolve_langfuse_enabled(
        {"langfuse": {"enabled": "auto"}}, environ=env
    ) is False


def test_explicit_false_even_with_keys() -> None:
    env = {
        "LANGFUSE_PUBLIC_KEY": "pk",
        "LANGFUSE_SECRET_KEY": "sk",
    }
    assert resolve_langfuse_enabled(
        {"langfuse": {"enabled": False}}, environ=env
    ) is False


def test_kill_switch() -> None:
    env = {
        "KAZMA_LANGFUSE": "0",
        "LANGFUSE_PUBLIC_KEY": "pk",
        "LANGFUSE_SECRET_KEY": "sk",
    }
    assert resolve_langfuse_enabled(
        {"langfuse": {"enabled": True}}, environ=env
    ) is False


def test_explicit_true() -> None:
    assert resolve_langfuse_enabled(
        {"langfuse": {"enabled": True}}, environ={}
    ) is True
