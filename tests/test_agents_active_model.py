"""The Agents card shows the model that will answer, not the build-time one."""

from __future__ import annotations


def test_agents_card_follows_the_active_profile(monkeypatch) -> None:
    class _Agent:
        config = None
        is_running = False

        def get_tools_info(self) -> dict:
            return {"count": 0, "list": [], "servers": 0}

        def get_llm_config(self) -> dict:
            return {
                "model": "glm-5.3-flash",
                "base_url": "https://api.z.ai/api/coding/paas/v4",
                "max_tokens": 4096,
                "temperature": 0.7,
            }

    class _Registry:
        def get_active_profile(self) -> dict:
            return {
                "model": "deepseek-flash",
                "base_url": "https://api.deepseek.com",
                "provider": "deepseek",
            }

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        lambda: _Registry(),
    )
    from kazma_ui.agents import _get_agent_info

    info = _get_agent_info(_Agent())
    assert info["llm"]["model"] == "deepseek-flash"
    assert info["llm"]["base_url"] == "https://api.deepseek.com"
