"""Shared Pydantic models for the Kazma WebUI API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "AgentConfigUpdate",
    "AppearanceUpdate",
    "APITokenCreate",
    "ChatEvent",
    "ChatMessage",
    "ChatRequest",
    "ConnectorConfigUpdate",
    "ConnectorTestRequest",
    "ConnectorTestResponse",
    "ConnectorUpdateRequest",
    "ContextSettingsUpdate",
    "DashboardMetrics",
    "ImportConfigRequest",
    "MaskedSecretResponse",
    "MCPServerAddRequest",
    "MCPServerTestRequest",
    "MCPServerToggleRequest",
    "ModelCompareRequest",
    "ModelDefaultUpdate",
    "ModelProfileUpdateRequest",
    "ModelTestRequest",
    "PasswordChange",
    "ProviderAddRequest",
    "ProviderTestResponse",
    "ProviderToggleRequest",
    "ProviderUpdateRequest",
    "SafetySettingsUpdate",
    "SettingsUpdate",
    "ShortcutUpdate",
    "SkillConfigUpdate",
    "SkillInstallRequest",
    "SkillToggleRequest",
    "ToolTestRequest",
    "ToolToggleRequest",
    "VoiceSettingsUpdate",
]

# ── Chat ──────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str = Field(..., description="user | assistant | system | tool")
    content: str = ""
    timestamp: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

class ChatRequest(BaseModel):
    """Incoming chat message from the WebSocket."""

    type: str = "message"
    content: str = ""
    session_id: str = ""


class ChatEvent(BaseModel):
    """Outgoing event over WebSocket."""

    type: str  # thinking | token | tool_call | tool_result | done | error
    content: str = ""
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    message_id: str = ""
    cost: float = 0.0
    tokens: int = 0


# ── Settings (legacy) ─────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    """A single settings update."""

    key: str
    value: Any
    category: str = "general"


class ModelTestRequest(BaseModel):
    """Request to test a model connection."""

    base_url: str
    api_key: str = ""
    model: str


class MCPServerTestRequest(BaseModel):
    """Request to test an MCP server connection."""

    name: str
    transport: str = "stdio"
    command: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)


# ── Skills ────────────────────────────────────────────────────────────


class SkillInstallRequest(BaseModel):
    """Request to install a skill from the hub."""

    skill_id: str


class SkillToggleRequest(BaseModel):
    """Request to enable/disable a skill."""

    skill_id: str
    enabled: bool


# ── MCP ───────────────────────────────────────────────────────────────


class MCPServerAddRequest(BaseModel):
    """Request to add an MCP server."""

    name: str
    transport: str = "stdio"
    command: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None
    # Auth: {"type": "bearer", "token": "..."} or {"type": "header", "name": "...", "value": "..."}
    auth: dict[str, str] = Field(default_factory=dict)
    # Trust level: "trusted" | "approval_required" | "sandboxed"
    trust: str = "approval_required"


class MCPServerToggleRequest(BaseModel):
    """Toggle an MCP server enabled/disabled."""

    enabled: bool


# ── Dashboard ─────────────────────────────────────────────────────────


class DashboardMetrics(BaseModel):
    """Real-time dashboard metrics."""

    cost_current: float = 0.0
    cost_max: float = 0.50
    cost_headroom: float = 0.50
    breaker_status: str = "OK"
    breaker_color: str = "green"
    cost_color: str = "green"
    silence_info: str = ""
    tracing_backend: str = "console"
    active_sessions: int = 0
    mcp_servers_running: int = 0
    mcp_tools_available: int = 0


# ── Provider Models ───────────────────────────────────────────────────


class ProviderAddRequest(BaseModel):
    """Request to add a new LLM provider."""

    name: str
    display_name: str = ""
    base_url: str
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    enabled: bool = True


class ProviderToggleRequest(BaseModel):
    """Toggle a provider enabled/disabled."""

    enabled: bool


class ProviderUpdateRequest(BaseModel):
    """Request to add or update an LLM provider in the unified hub."""

    name: str
    display_name: str = ""
    base_url: str = ""
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    enabled: bool = True
    google_mode: str = ""
    project_id: str = ""
    location: str = "us-central1"


class ConnectorUpdateRequest(BaseModel):
    """Request to add or update a platform connector token."""

    name: str
    token: str = ""
    enabled: bool = True
    extras: dict[str, str] = Field(default_factory=dict)


class ProviderTestResponse(BaseModel):
    """Response from a non-destructive LLM provider health check."""

    success: bool
    latency_ms: int | None = None
    error: str | None = None


class ConnectorTestResponse(BaseModel):
    """Response from a platform connector health check."""

    success: bool
    bot_name: str | None = None
    error: str | None = None


class MaskedSecretResponse(BaseModel):
    """Generic masked secret representation returned by the secrets hub."""

    name: str
    type: str
    value: str
    masked: bool = True


class ModelProfileUpdateRequest(BaseModel):
    """Request to save a named model profile."""

    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = "custom"


# ── Agent Models ──────────────────────────────────────────────────────


class AgentConfigUpdate(BaseModel):
    """Update agent configuration."""

    name: str | None = None
    language: str | None = None
    system_prompt: str | None = None
    personality: str | None = None


class SafetySettingsUpdate(BaseModel):
    """Update HITL safety settings."""

    hitl_enabled: bool = True
    require_approval_for: list[str] = Field(default_factory=list)
    approval_timeout: int = 60
    auto_deny_on_timeout: bool = True


class ContextSettingsUpdate(BaseModel):
    """Update context window settings."""

    max_context_tokens: int = 128000
    context_strategy: str = "sliding_window"
    summarization_threshold: float = 0.8


# ── Connector Models ──────────────────────────────────────────────────


class ConnectorConfigUpdate(BaseModel):
    """Update a connector's configuration."""

    platform: str
    settings: dict[str, Any] = Field(default_factory=dict)


class ConnectorTestRequest(BaseModel):
    """Test a connector connection."""

    platform: str


# ── Skill Models ──────────────────────────────────────────────────────


class SkillConfigUpdate(BaseModel):
    """Update skill-specific settings."""

    settings: dict[str, Any] = Field(default_factory=dict)


# ── Appearance Models ─────────────────────────────────────────────────


class AppearanceUpdate(BaseModel):
    """Update appearance settings."""

    theme: str | None = None
    accent_color: str | None = None
    font_size: int | None = None
    sidebar_position: str | None = None
    custom_css: str | None = None


# ── Shortcut Models ───────────────────────────────────────────────────


class ShortcutUpdate(BaseModel):
    """Update a keyboard shortcut."""

    action: str
    keys: str


# ── Account Models ────────────────────────────────────────────────────


class PasswordChange(BaseModel):
    """Change account password."""

    old_password: str
    new_password: str


class APITokenCreate(BaseModel):
    """Create a new API token."""

    name: str
    expires_days: int = 90


# ── Tool Models ───────────────────────────────────────────────────────


class ToolToggleRequest(BaseModel):
    """Toggle a tool enabled/disabled."""

    enabled: bool


class ToolTestRequest(BaseModel):
    """Test a tool with arguments."""

    arguments: dict[str, Any] = Field(default_factory=dict)


# ── Import/Export Models ──────────────────────────────────────────────


class ImportConfigRequest(BaseModel):
    """Import configuration from YAML/JSON."""

    data: str
    format: str = "yaml"
    selective: bool = False
    sections: list[str] = Field(default_factory=list)


# ── Model Comparison ──────────────────────────────────────────────────


class ModelCompareRequest(BaseModel):
    """Compare multiple models with the same prompt."""

    prompt: str
    models: list[str]
    temperature: float = 0.7
    max_tokens: int = 256


class ModelDefaultUpdate(BaseModel):
    """Set default model for a task type."""

    task_type: str  # 'chat', 'code', 'summarize', 'translate'
    model_name: str


class VoiceSettingsUpdate(BaseModel):
    """Update Voice Subsystem settings."""

    enabled: bool = False
    stt_provider: str = "openai"
    tts_provider: str = "edgetts"
    tts_voice: str = "default"
    stt_language: str = "auto"
    tts_output_format: str = "mp3"
