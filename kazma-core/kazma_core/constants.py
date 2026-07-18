"""Centralized constants for Kazma core.

Single source of truth for magic numbers, timeouts, limits, and thresholds.
All modules should import from here instead of hardcoding values.
"""

__all__ = [
    "CIRCUIT_BREAKER_DEFAULT_FAILURE_THRESHOLD",
    "CIRCUIT_BREAKER_DEFAULT_RECOVERY_TIMEOUT_SECONDS",
    "CIRCUIT_BREAKER_HALF_OPEN_PROBE_LIMIT",
    "CONFIG_STORE_DB_BUSY_TIMEOUT_MS",
    "CONFIG_STORE_DEFAULT_TTL_SECONDS",
    "CONFIG_STORE_MAX_ENTRIES",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_RETRY_BASE_DELAY_SECONDS",
    "DEFAULT_RETRY_MAX_DELAY_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GRAPH_HITL_DANGER_TOOLS",
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "MAX_PAGE_SIZE",
    "MCP_DANGER_PATTERNS",
    "MCP_SAFE_PATTERNS",
    "ROUTER_DEFAULT_TOP_K",
    "ROUTER_MIN_CONFIDENCE",
    "SESSION_STORE_DEFAULT_TTL_SECONDS",
    "SESSION_STORE_MAX_ENTRIES",
    "SWARM_BUS_DANGER_TOOLS",
    "SWARM_DISPATCH_TIMEOUT_SECONDS",
    "SWARM_ENGINE_MAX_HANDOFF_DEPTH",
    "SWARM_ENGINE_MAX_HISTORY",
    "SWARM_ENGINE_MAX_VISITS_PER_WORKER",
    "SWARM_OUTPUT_TRUNCATE_CHARS",
    "SWARM_TASK_PREVIEW_MAX_CHARS",
    "TASK_STORE_DB_BUSY_TIMEOUT_MS",
    "TASK_STORE_MAX_HISTORY",
    "TELEGRAM_MAX_CHAT_ID",
    "TELEGRAM_MIN_CHAT_ID",
    "VALID_OUTPUT_PLATFORMS",
    "VALID_TASK_PATTERNS",
]

# ─── ConfigStore ───────────────────────────────────────────────────────
CONFIG_STORE_MAX_ENTRIES = 10_000
CONFIG_STORE_DEFAULT_TTL_SECONDS = 3600
CONFIG_STORE_DB_BUSY_TIMEOUT_MS = 5000

# ─── SessionStore (agent_handler) ──────────────────────────────────────
SESSION_STORE_MAX_ENTRIES = 10_000
SESSION_STORE_DEFAULT_TTL_SECONDS = 3600

# ─── Swarm Dispatch ────────────────────────────────────────────────────
SWARM_DISPATCH_TIMEOUT_SECONDS = 300
SWARM_TASK_PREVIEW_MAX_CHARS = 200
SWARM_OUTPUT_TRUNCATE_CHARS = 4000

# ─── Telegram ──────────────────────────────────────────────────────────
# Telegram chat IDs: private chats > 0, groups/channels < 0
# Group IDs typically range from -10^9 to -10^13
TELEGRAM_MIN_CHAT_ID = -10**13
TELEGRAM_MAX_CHAT_ID = -10**9

# ─── Routing ───────────────────────────────────────────────────────────
ROUTER_DEFAULT_TOP_K = 3
ROUTER_MIN_CONFIDENCE = 0.3

# ─── Safety / HITL ─────────────────────────────────────────────────────
# Danger tools requiring graph interrupt() approval (Mechanism A)
GRAPH_HITL_DANGER_TOOLS = frozenset({
    "file_write",
    "file_delete",
    "shell_exec",
    "code_exec",
    "python_exec",
})

# Extended danger tools for Swarm Message Bus (Mechanism B)
# Includes spawn/schedule tools in addition to graph danger tools
SWARM_BUS_DANGER_TOOLS = frozenset({
    "file_write",
    "file_delete",
    "shell_exec",
    "code_exec",
    "python_exec",
    "spawn_agent",
    "spawn_agents",
    "schedule_task",
    "cancel_scheduled",
})

# MCP tool classification patterns
MCP_DANGER_PATTERNS = frozenset({
    "write",
    "exec",
    "delete",
    "create",
    "update",
    "remove",
    "drop",
})
MCP_SAFE_PATTERNS = frozenset({
    "read",
    "list",
    "get",
    "find",
    "search",
    "query",
    "fetch",
})

# ─── Circuit Breaker ───────────────────────────────────────────────────
CIRCUIT_BREAKER_DEFAULT_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_DEFAULT_RECOVERY_TIMEOUT_SECONDS = 30
CIRCUIT_BREAKER_HALF_OPEN_PROBE_LIMIT = 1

# ─── TaskStore ─────────────────────────────────────────────────────────
TASK_STORE_MAX_HISTORY = 500
TASK_STORE_DB_BUSY_TIMEOUT_MS = 5000

# ─── Swarm Engine ──────────────────────────────────────────────────────
SWARM_ENGINE_MAX_HISTORY = 500
SWARM_ENGINE_MAX_HANDOFF_DEPTH = 5
SWARM_ENGINE_MAX_VISITS_PER_WORKER = 2

# ─── Retry / Timeout ───────────────────────────────────────────────────
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 1.0
DEFAULT_RETRY_MAX_DELAY_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 60

# ─── Valid Platforms ───────────────────────────────────────────────────
VALID_OUTPUT_PLATFORMS = frozenset({"telegram", "discord", "slack", "web"})
VALID_TASK_PATTERNS = frozenset({
    "dispatch",
    "pipeline",
    "consult",
    "fan_out",
    "broadcast",
})

# ─── Logging ───────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ─── Pagination ────────────────────────────────────────────────────────
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200