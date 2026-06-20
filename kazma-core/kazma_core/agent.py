"""Kazma Agent — ReAct loop with LangGraph state machine.

This is the main entry point for the Kazma agent.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_FILE = "kazma.yaml"


@dataclass
class AgentConfig:
    """Configuration loaded from kazma.yaml."""

    name: str = "kazma"
    version: str = "0.1.0"
    language: str = "ar"
    rtl: bool = True
    default_model: str = "gpt-4o-mini"
    storage_path: str = "data/kazma.db"
    vector_dim: int = 1536
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML file."""
    path = Path(config_path) if config_path else Path(CONFIG_FILE)
    if not path.exists():
        logger.warning("Config file %s not found, using defaults", path)
        return AgentConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    agent_cfg = raw.get("agent", {})
    models_cfg = raw.get("models", {})
    storage_cfg = raw.get("storage", {})

    return AgentConfig(
        name=agent_cfg.get("name", "kazma"),
        version=agent_cfg.get("version", "0.1.0"),
        language=agent_cfg.get("language", "ar"),
        rtl=agent_cfg.get("rtl", True),
        default_model=models_cfg.get("default", "gpt-4o-mini"),
        storage_path=storage_cfg.get("path", "data/kazma.db"),
        vector_dim=storage_cfg.get("vector_dim", 1536),
        raw=raw,
    )


class KazmaAgent:
    """Main agent class — ReAct loop placeholder.

    Full implementation will use LangGraph state machine.
    """

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or load_config()
        self._running = False
        logger.info(
            "Kazma agent initialized: %s v%s (lang=%s)",
            self.config.name,
            self.config.version,
            self.config.language,
        )

    async def run(self, user_input: str) -> str:
        """Process user input and return response.

        Placeholder — full ReAct loop will be implemented with LangGraph.
        """
        logger.info("Received input: %s", user_input[:100])
        # TODO: Implement ReAct loop with LangGraph state machine
        # TODO: Connect to tool registry, memory, and providers
        return f"[Kazma] Echo: {user_input}"

    async def shutdown(self) -> None:
        """Clean shutdown of the agent."""
        self._running = False
        logger.info("Kazma agent shut down.")


async def main() -> None:
    """Entry point for running Kazma as a standalone agent."""
    config = load_config()
    agent = KazmaAgent(config)
    agent._running = True

    logger.info("Kazma agent started. Type 'quit' to exit.")
    try:
        while agent._running:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("kazma> ")
            )
            if user_input.strip().lower() in ("quit", "exit"):
                break
            response = await agent.run(user_input)
            print(response)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
