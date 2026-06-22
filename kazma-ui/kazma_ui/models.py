"""Shared Pydantic models for the Kazma WebUI API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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


# ── Settings ──────────────────────────────────────────────────────────


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
