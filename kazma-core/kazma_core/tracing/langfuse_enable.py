"""When Langfuse tracing is actually on.

YAML used to ship ``logging.langfuse.enabled: false``, so even with keys
the tracer stayed on the console backend. Default is now ``auto``: keys
present → Langfuse; no keys → console. ``KAZMA_LANGFUSE=0`` is the kill-switch.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

__all__ = ["langfuse_keys", "resolve_langfuse_enabled"]


def langfuse_keys(
    langfuse_cfg: dict[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str, str]:
    """Return ``(public, secret, host)`` from YAML then env."""
    env = environ if environ is not None else os.environ
    lf = langfuse_cfg if isinstance(langfuse_cfg, dict) else {}
    public = str(lf.get("public_key") or env.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret = str(lf.get("secret_key") or env.get("LANGFUSE_SECRET_KEY") or "").strip()
    host = str(
        lf.get("host")
        or env.get("LANGFUSE_HOST")
        or "http://localhost:3000"
    ).strip()
    return public, secret, host


def resolve_langfuse_enabled(
    logging_cfg: dict[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when the Langfuse backend should be selected.

    * ``KAZMA_LANGFUSE=0`` → off
    * YAML/Config ``enabled: false`` → off (operator kill)
    * ``enabled: true`` → on
    * ``enabled: auto`` (default) → on only when public+secret keys exist
    """
    env = environ if environ is not None else os.environ
    if str(env.get("KAZMA_LANGFUSE", "")).strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        return False
    block = (logging_cfg or {}).get("langfuse") if isinstance(logging_cfg, dict) else {}
    if not isinstance(block, dict):
        block = {}
    flag = block.get("enabled", "auto")
    explicit: bool | None
    if isinstance(flag, bool):
        explicit = flag
    else:
        s = str(flag).strip().lower()
        if s in ("0", "false", "off", "no"):
            explicit = False
        elif s in ("1", "true", "on", "yes"):
            explicit = True
        else:
            explicit = None
    if explicit is False:
        return False
    if explicit is True:
        return True
    public, secret, _host = langfuse_keys(block, environ=env)
    return bool(public and secret)
