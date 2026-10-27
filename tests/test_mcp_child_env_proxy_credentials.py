"""An MCP server does not inherit a proxy password (audit 2026-10-26 AUD-097).

``mcp_child_env`` (audit H-4) passes an MCP server only an allowlist of
Kazma's environment, and ``HTTP_PROXY``/``HTTPS_PROXY`` are on it so a server
can reach the network. A proxy URL can carry the proxy account's password,
and the allowlist handed it to every MCP server -- packages the operator may
only be trying out through Settings' Test. A value carrying credentials now
stays behind; a proxy without them still passes, and a server's own ``env``
(the operator's choice for that server) still wins.
"""

from __future__ import annotations

import pytest

from kazma_core.mcp.child_env import MCP_CHILD_ENV_ALLOWLIST, mcp_child_env

SECRET = "pr0xy-pass-AUD097"


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.delenv("KAZMA_MCP_INHERIT_ENV", raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://kazma:{SECRET}@proxy.example:3128")
    monkeypatch.setenv("HTTPS_PROXY", f"kazma:{SECRET}@proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")


def _leaks(env: dict[str, str]) -> list[str]:
    return [k for k, v in env.items() if SECRET in v]


def test_a_proxy_with_credentials_is_not_inherited(proxied) -> None:
    env = mcp_child_env("demo", {})
    assert _leaks(env) == []
    assert "HTTP_PROXY" not in env and "HTTPS_PROXY" not in env
    assert env.get("NO_PROXY") == "localhost,127.0.0.1"


def test_a_proxy_without_credentials_still_passes(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_MCP_INHERIT_ENV", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    assert mcp_child_env("demo", {})["HTTPS_PROXY"] == "http://proxy.example:3128"


def test_the_servers_own_env_still_wins(proxied) -> None:
    own = f"http://svc:{SECRET}@proxy.example:3128"
    env = mcp_child_env("demo", {"env": {"HTTPS_PROXY": own}})
    assert env["HTTPS_PROXY"] == own


def test_the_old_allowlist_filter_leaked_the_password(proxied) -> None:
    """Negative control: the allowlist alone, as the function filtered."""
    import os

    old = {k: v for k, v in os.environ.items() if k.upper() in MCP_CHILD_ENV_ALLOWLIST}
    assert sorted(_leaks(old)) == ["HTTPS_PROXY", "HTTP_PROXY"]
