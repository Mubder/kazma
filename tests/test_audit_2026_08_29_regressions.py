"""Behavioural regression tests for the 2026-08-29 security audit.

One test per finding that had an exploitable behaviour, written so it fails
against the pre-fix code. The static (grep-shaped) findings are covered by
``tests/test_static_gates.py`` instead.
"""

from __future__ import annotations

import json

import pytest

# ── F-01: loopback trust behind a reverse proxy ──────────────────────────

@pytest.fixture
def clean_auth_env(monkeypatch):
    for var in (
        "KAZMA_TRUSTED_PROXIES",
        "KAZMA_TRUST_LAN",
        "KAZMA_DEMO_MODE",
        "KAZMA_LOOPBACK_AUTOLOGIN",
        "KAZMA_PRODUCTION",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_SECRET", "regression-test-secret")
    yield


class _FakeRequest:
    """Minimal Request stand-in for the auth helpers (they only touch these)."""

    def __init__(self, peer: str, headers: dict[str, str] | None = None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}
        self.cookies: dict[str, str] = {}


def test_loopback_peer_not_trusted_when_proxy_declared(clean_auth_env, monkeypatch):
    """F-01: a proxied loopback peer must not be treated as the local operator."""
    from kazma_ui.auth import _peer_trust_allowed, _should_auto_issue_cookie

    req = _FakeRequest("127.0.0.1")

    # No proxy declared: direct localhost use keeps working.
    assert _peer_trust_allowed(req) is True
    assert _should_auto_issue_cookie(req, "regression-test-secret") is True

    # Proxy declared: the peer is the proxy, so it proves nothing.
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    assert _peer_trust_allowed(req) is False
    assert _should_auto_issue_cookie(req, "regression-test-secret") is False


def test_forwarded_for_only_honoured_from_trusted_proxy(clean_auth_env, monkeypatch):
    """F-01: a spoofed X-Forwarded-For from a direct client is ignored."""
    from kazma_ui.auth import _client_host

    spoofed = _FakeRequest("203.0.113.7", {"x-forwarded-for": "127.0.0.1"})
    assert _client_host(spoofed) == "203.0.113.7"  # not the spoofed value

    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "10.0.0.1")
    via_proxy = _FakeRequest("10.0.0.1", {"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    assert _client_host(via_proxy) == "203.0.113.9"


def test_websocket_peer_trust_disabled_behind_proxy(clean_auth_env, monkeypatch):
    """F-01: the WS handshake must not auto-trust a proxied loopback peer."""
    from kazma_ui.auth import websocket_is_authenticated

    ws = _FakeRequest("127.0.0.1")           # no Origin header (curl-style)
    ws.query_params = {}
    assert websocket_is_authenticated(ws) is True   # direct bind: allowed

    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    assert websocket_is_authenticated(ws) is False  # proxied: denied


# ── F-02: recursive secret masking ───────────────────────────────────────

def test_mask_deep_recurses_into_lists():
    """F-02: a list of provider dicts must have its nested api_key masked."""
    from kazma_ui.settings import MASK, mask_deep

    payload = {
        "providers.list": [
            {"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
             "api_key": "sk-live-value-must-not-leak", "enabled": True},
            {"name": "openai", "api_key": "", "enabled": False},
        ],
        "connectors.x.api_key": "xoxb-also-secret",
        "workspace.selected_path": "/home/me/project",
    }
    out = mask_deep(payload)

    assert out["providers.list"][0]["api_key"] == MASK
    assert out["providers.list"][0]["name"] == "deepseek"       # non-secret kept
    assert out["providers.list"][0]["enabled"] is True          # bools untouched
    assert out["providers.list"][1]["api_key"] == ""            # empty stays empty
    assert out["connectors.x.api_key"] == MASK
    assert out["workspace.selected_path"] == "/home/me/project"

    assert "sk-live-value-must-not-leak" not in json.dumps(out)


def test_mask_deep_handles_json_encoded_values():
    """F-02: secrets inside JSON-encoded config strings are masked too."""
    from kazma_ui.settings import mask_deep

    out = mask_deep({"models.profiles": json.dumps([{"api_key": "sk-nested"}])})
    assert "sk-nested" not in json.dumps(out)


# ── F-03: shell_exec argument policy ─────────────────────────────────────

@pytest.mark.parametrize(
    "command,blocked_flag",
    [
        ("find . -maxdepth 0 -exec whoami +", "-exec"),
        ("find . -name '*.py' -delete", "-delete"),
        ("git clone --upload-pack=echo file:///tmp/x", "--upload-pack"),
        ("git -c core.gitProxy=whoami status", "-c"),
        ("tar --use-compress-program=whoami -cf x.tar .", "--use-compress-program"),
    ],
)
async def test_shell_exec_blocks_exec_capable_arguments(command, blocked_flag):
    """F-03: an allowlisted binary must not be able to run another program."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    result = await registry._tools["shell_exec"].func(command, timeout=10)

    assert result.startswith("Error:"), f"{command!r} was not blocked: {result[:200]}"
    assert blocked_flag in result


async def test_shell_exec_still_allows_ordinary_commands():
    """F-03: the argument policy must not break normal allowlisted use."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    result = await registry._tools["shell_exec"].func("git status --short", timeout=15)
    assert not result.startswith("Error:")


# ── F-04: HITL default-deny ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "tool",
    ["file_append", "git_push", "git_merge", "send_message", "send_file",
     "vault_store", "memory_delete_entity", "github_create_issue"],
)
def test_side_effecting_tools_require_approval(tool):
    """F-04: tools with destructive or outbound effects must be gated."""
    from kazma_core.safety.hitl import get_hitl_config, requires_approval

    assert requires_approval(tool, get_hitl_config({})) is True


def test_unknown_tool_defaults_to_requiring_approval():
    """F-04: approval is the default; exemption must be explicit."""
    from kazma_core.safety.hitl import get_hitl_config, requires_approval

    assert requires_approval("some_tool_added_next_week", get_hitl_config({})) is True


@pytest.mark.parametrize("tool", ["file_read", "web_search", "memory_search", "git_status"])
def test_read_only_tools_still_run_unapproved(tool):
    """F-04: default-deny must not start prompting on ordinary reads."""
    from kazma_core.safety.hitl import get_hitl_config, requires_approval

    assert requires_approval(tool, get_hitl_config({})) is False


# ── F-05: tenant isolation fails closed ──────────────────────────────────

def test_tenant_resolution_failure_scopes_to_nothing(monkeypatch):
    """F-05: a failed tenant lookup must match no rows, not every row."""
    import kazma_ui.routes_direct as rd

    def _boom():
        raise RuntimeError("config store down")

    monkeypatch.setattr("kazma_ui.memory_api._memory_tenant_id", _boom)
    tid = rd._mem_tid()
    assert tid == "__unscoped__"

    sql, params = rd._tenant_clause(tid)
    assert sql.strip() and params == ["__unscoped__"], (
        "unscoped tenant produced an empty predicate — that is a cross-tenant read"
    )


# ── F-11: expired sessions are swept ─────────────────────────────────────

def test_expired_sessions_are_purged(monkeypatch, tmp_path):
    """F-11: expired session records must not accumulate forever."""
    from kazma_core.security import web_sessions

    store: dict[str, dict] = {
        "web_session.aaa": {"expires_at": 1.0, "actor": "old"},        # expired
        "web_session.bbb": {"expires_at": 4e9, "actor": "live"},       # live
        "other.key": {"keep": True},
    }

    class _Store:
        def get_category(self, category):
            assert category == "auth"
            return dict(store)

        def delete(self, key):
            store.pop(key, None)
            return True

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    assert web_sessions.purge_expired_sessions() == 1
    assert "web_session.aaa" not in store
    assert "web_session.bbb" in store
    assert "other.key" in store


# ── F-13: rate limiter eviction ──────────────────────────────────────────

def test_rate_limiter_evicts_lru_not_everything():
    """F-13: hitting the key cap must not reset other principals' windows."""
    from kazma_ui import rate_limit

    rate_limit._windows.clear()
    monkey_cap = 8
    original = rate_limit._MAX_TRACKED_KEYS
    rate_limit._MAX_TRACKED_KEYS = monkey_cap
    try:
        victim = ("bucket", "victim")
        for _ in range(3):
            rate_limit._allow(victim, limit=100)
        assert len(rate_limit._windows[victim]) == 3

        # Flood with distinct keys, as an attacker varying its cookie would.
        for i in range(monkey_cap * 3):
            rate_limit._allow(("bucket", f"flood-{i}"), limit=100)

        assert len(rate_limit._windows) <= monkey_cap
        # The victim was evicted rather than *reset*, and no other live window
        # was silently cleared — the map never dropped to zero entries.
        assert rate_limit._windows, "eviction wiped the entire map (the F-13 bug)"
    finally:
        rate_limit._MAX_TRACKED_KEYS = original
        rate_limit._windows.clear()


# ── F-15: always-open prefixes are normalised ────────────────────────────

def test_always_open_prefix_matching_is_slash_insensitive():
    """F-15: a trailing slash must not silently disable a prefix entry."""
    from kazma_ui import auth

    assert auth.is_always_open("/api/github/oauth/callback")
    assert auth.is_always_open("/api/github/oauth/callback/extra")
    # /api/auth/* is NOT blanket-open: each pre-login route is listed explicitly.
    assert auth.is_always_open("/api/auth/login")
    assert not auth.is_always_open("/api/auth/tokens/create")


# ── Audit O2: exception details do not reach clients ─────────────────────

def test_safe_error_hides_internals(monkeypatch):
    """O2: client-facing errors carry a code + ref, never paths or SQL."""
    from kazma_core.errors import safe_error

    monkeypatch.delenv("KAZMA_VERBOSE_ERRORS", raising=False)
    exc = FileNotFoundError("no such file: C:\\Users\\me\\secret\\config.db")
    info = safe_error(exc, log=False)

    assert info.code == "not_found"
    assert "C:\\Users" not in str(info)
    assert "config.db" not in str(info)
    assert info.ref in str(info)


def test_safe_error_redacts_even_in_verbose_mode(monkeypatch):
    """O2: verbose mode still never echoes a credential."""
    from kazma_core.errors import safe_error

    monkeypatch.setenv("KAZMA_VERBOSE_ERRORS", "1")
    info = safe_error(ValueError("bad token sk-abcdefghijklmnop"), log=False)
    assert "sk-abcdefghijklmnop" not in str(info)
