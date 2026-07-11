"""Pydantic configuration schema for Kazma.

Provides validation, type safety, and consistency checks for all
configuration values. Integrates with ConfigStore for round-trip
serialization.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator, model_validator


class TelegramOutputTarget(BaseModel):
    """Telegram output target for swarm results."""
    
    bot_token: str = Field(..., min_length=10, description="Bot token from @BotFather")
    chat_id: int = Field(..., description="Target chat/group/channel ID")
    platform: str = Field(default="telegram", description="Platform identifier")
    enabled: bool = Field(default=True, description="Whether this target is active")
    
    @field_validator("chat_id")
    @classmethod
    def validate_chat_id(cls, v: int) -> int:
        if not (-10**13 <= v <= -10**9 or v > 0):
            raise ValueError("Telegram chat_id must be negative (group/channel) or positive (private)")
        return v


class TelegramConnectorConfig(BaseModel):
    """Telegram connector configuration for gateway."""
    
    token: str = Field(..., min_length=10)
    allowed_users: list[int] = Field(default_factory=list)
    webhook_url: str | None = Field(default=None)


class DiscordConnectorConfig(BaseModel):
    """Discord connector configuration for gateway."""
    
    token: str = Field(..., min_length=10)
    application_id: str | None = Field(default=None)
    public_key: str | None = Field(default=None)


class SlackConnectorConfig(BaseModel):
    """Slack connector configuration for gateway."""
    
    bot_token: str = Field(..., min_length=10)
    signing_secret: str = Field(..., min_length=10)
    app_token: str | None = Field(default=None)


class ConnectorsConfig(BaseModel):
    """All platform connectors configuration."""
    
    telegram: TelegramConnectorConfig | None = Field(default=None)
    discord: DiscordConnectorConfig | None = Field(default=None)
    slack: SlackConnectorConfig | None = Field(default=None)


class SafetyConfig(BaseModel):
    """Safety/HITL configuration."""
    
    enabled: bool = Field(default=True)
    hitl: dict[str, Any] = Field(default_factory=lambda: {
        "enabled": True,
        "require_approval_for": [
            "file_write",
            "file_delete",
            "shell_exec",
            "code_exec",
            "python_exec",
        ],
        "timeout_seconds": 300,
    })
    allow_headless_danger: bool = Field(
        default=False,
        description="Allow danger tools without approval bus (TEST ONLY)"
    )


class ModelRegistryConfig(BaseModel):
    """Model registry configuration."""
    
    active_model: str | None = Field(default=None)
    active_provider: str | None = Field(default=None)
    fallback_models: list[str] = Field(default_factory=list)


class TracingConfig(BaseModel):
    """Tracing configuration."""

    enabled: bool = Field(default=False)
    backend: str = Field(default="console", description="Tracing backend: langfuse, console")
    service_name: str = Field(default="kazma-agent", description="Service name for traces")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Trace sampling rate")
    # Langfuse specific (if backend=langfuse)
    langfuse_public_key: str | None = Field(default=None)
    langfuse_secret_key: str | None = Field(default=None)
    langfuse_host: str = Field(default="http://localhost:3000")

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        valid = {"langfuse", "console"}
        if v not in valid:
            raise ValueError(f"backend must be one of {valid}")
        return v


class SwarmConfig(BaseModel):
    """Swarm orchestration configuration."""
    
    enabled: bool = Field(default=True)
    default_pattern: str = Field(
        default="dispatch",
        description="Default dispatch pattern"
    )
    output_target: TelegramOutputTarget | None = Field(default=None)
    auto_route: bool = Field(default=True)
    max_concurrent_tasks: int = Field(default=10, ge=1, le=100)
    workers: list[dict[str, Any]] = Field(default_factory=list)
    
    @field_validator("default_pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        valid = {"dispatch", "pipeline", "consult", "fan_out", "broadcast"}
        if v not in valid:
            raise ValueError(f"pattern must be one of {valid}")
        return v


class KazmaConfig(BaseModel):
    """Root configuration model for Kazma.
    
    This is the single source of truth for all configuration.
    Validates cross-section consistency (e.g., swarm.output_target
    must match connectors.telegram if both are set).
    """
    
    swarm: SwarmConfig = Field(default_factory=SwarmConfig)
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    model_registry: ModelRegistryConfig = Field(default_factory=ModelRegistryConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    
    # Pydantic config
    model_config = {
        "extra": "allow",  # Allow extra fields for extensibility
        "validate_assignment": True,
        "use_enum_values": True,
    }
    
    # Track which fields came from flat dict for round-trip
    _FLAT_FIELD_PREFIXES: ClassVar[list[str]] = [
        "swarm",
        "connectors",
        "safety",
        "model_registry",
        "tracing",
    ]
    
    @model_validator(mode="after")
    def check_telegram_consistency(self) -> KazmaConfig:
        """Ensure swarm.output_target and connectors.telegram are consistent."""
        swarm_target = self.swarm.output_target
        conn_telegram = self.connectors.telegram
        
        if swarm_target and conn_telegram:
            # Both set - they should agree on token
            if swarm_target.bot_token != conn_telegram.token:
                raise ValueError(
                    "swarm.output_target.bot_token must match connectors.telegram.token"
                )
        
        return self
    
    @classmethod
    def from_flat_dict(cls, flat: dict[str, Any]) -> KazmaConfig:
        """Create config from flat dotted-key dictionary (as stored in ConfigStore).
        
        Args:
            flat: Dict like {"swarm.enabled": True, "connectors.telegram.token": "..."}
            
        Returns:
            Validated KazmaConfig instance.
        """
        # Convert flat dict to nested
        nested: dict[str, Any] = {}
        
        for key, value in flat.items():
            parts = key.split(".")
            target = nested
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
        
        return cls.model_validate(nested)
    
    def to_flat_dict(self) -> dict[str, Any]:
        """Convert to flat dotted-key dictionary for ConfigStore storage."""
        flat: dict[str, Any] = {}
        
        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    flat[full_key] = v
        
        _flatten(self.model_dump())
        return flat