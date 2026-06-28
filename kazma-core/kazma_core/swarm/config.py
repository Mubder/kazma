"""Configuration schema for the SwarmManager.

Loads from the ``swarm`` section of ``kazma.yaml``. The section is optional —
if absent, ``SwarmConfig.from_yaml`` returns ``None`` so the caller can
gracefully degrade.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from kazma_core.swarm.task import WorkerCapabilities

logger = logging.getLogger(__name__)


def _resolve_chat_id(yaml_value: int) -> int:
    """Resolve the swarm group chat ID.

    The ``SWARM_CHAT_ID`` environment variable takes precedence so the chat ID
    is never hardcoded in a checked-in config file. If the env var is absent,
    the YAML value (default ``0``) is used.
    """
    env_value = os.environ.get("SWARM_CHAT_ID")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            logger.warning(
                "SWARM_CHAT_ID env var '%s' is not a valid integer — ignoring.", env_value
            )
    return yaml_value


@dataclass(frozen=True)
class WorkerConfig:
    """Configuration for a single swarm worker."""

    name: str
    type: str  # "in_process" | "telegram_bot"
    model: str = ""
    provider: str = ""
    profile: str = ""
    bot_token_env: str = ""  # env var name holding the Telegram bot token
    role: str = ""  # e.g. "backend_core", "frontend_ux"
    capabilities: WorkerCapabilities = field(default_factory=WorkerCapabilities)

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.name:
            errors.append("Worker name is required.")
        if self.type not in ("in_process", "telegram_bot"):
            errors.append(f"Worker type must be 'in_process' or 'telegram_bot', got '{self.type}'.")
        if self.type == "telegram_bot" and not self.profile:
            errors.append(f"Telegram worker '{self.name}' requires a 'profile'.")
        if self.type == "telegram_bot" and not self.bot_token_env:
            errors.append(f"Telegram worker '{self.name}' requires 'bot_token_env'.")
        return errors


@dataclass(frozen=True)
class OrchestratorConfig:
    """Configuration for the orchestrator identity."""

    name: str = "Kazma Orchestrator"
    profile: str = "default"


@dataclass
class SwarmConfig:
    """Top-level swarm configuration."""

    enabled: bool = False
    group_chat_id: int = 0
    max_concurrent: int = 5
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    workers: list[WorkerConfig] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> SwarmConfig | None:
        """Load swarm config from a YAML file.

        Returns ``None`` if the file doesn't exist or has no ``swarm`` section.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("Config file not found: %s", path)
            return None

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        swarm_data = data.get("swarm")
        if not swarm_data:
            logger.info("No 'swarm' section in %s — swarm disabled.", path)
            return None

        return cls.from_dict(swarm_data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmConfig:
        """Build config from a dict (the ``swarm`` YAML subtree)."""
        orch_raw = data.get("orchestrator", {})
        orchestrator = OrchestratorConfig(
            name=orch_raw.get("name", "Kazma Orchestrator"),
            profile=orch_raw.get("profile", "default"),
        )

        workers: list[WorkerConfig] = []
        for w in data.get("workers", []):
            capabilities = WorkerCapabilities.from_dict(
                w.get("capabilities", {"role": w.get("role", "")})
            )
            workers.append(
                WorkerConfig(
                    name=w["name"],
                    type=w["type"],
                    model=w.get("model", ""),
                    provider=w.get("provider", ""),
                    profile=w.get("profile", ""),
                    bot_token_env=w.get("bot_token_env", ""),
                    role=w.get("role", ""),
                    capabilities=capabilities,
                )
            )

        return cls(
            enabled=data.get("enabled", False),
            group_chat_id=_resolve_chat_id(int(data.get("group_chat_id", 0))),
            max_concurrent=max(1, int(data.get("max_concurrent", 5))),
            orchestrator=orchestrator,
            workers=workers,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return all validation errors (empty = valid)."""
        errors: list[str] = []
        if self.max_concurrent < 1:
            errors.append("Swarm max_concurrent must be at least 1.")
        names_seen: set[str] = set()
        for wc in self.workers:
            errors.extend(wc.validate())
            if wc.name in names_seen:
                errors.append(f"Duplicate worker name: '{wc.name}'.")
            names_seen.add(wc.name)
        return errors

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_worker(self, name: str) -> WorkerConfig | None:
        """Look up a worker by name."""
        for wc in self.workers:
            if wc.name == name:
                return wc
        return None
