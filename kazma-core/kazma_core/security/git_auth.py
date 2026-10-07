"""GitHub Git authentication without credentials in command arguments."""
from __future__ import annotations

import base64


def clone_auth_env(token: str | None) -> dict[str, str]:
    """A URL-scoped Git config header; combine with tool_child_env."""
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: Basic {basic}",
    }
