"""LiveKit duplex config + access tokens.

The browser joins a LiveKit room (WebRTC AEC, mic, speaker). Kazma's
LangGraph supervisor stays the brain: STT → graph → TTS, with barge-in.
This module does **not** import LiveKit Agents as a second LLM loop.

Opt-in: ``LIVEKIT_URL`` + ``LIVEKIT_API_KEY`` + ``LIVEKIT_API_SECRET``.
Kill-switch: ``KAZMA_VOICE_DUPLEX=0``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

__all__ = [
    "LiveKitConfig",
    "get_livekit_config",
    "mint_livekit_token",
    "sanitize_room_name",
    "voice_duplex_enabled",
]

_ROOM_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class LiveKitConfig:
    enabled: bool
    url: str
    api_key: str
    api_secret: str


def _env_off(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "off", "no")


def _store_get(key: str, default: str = "") -> str:
    try:
        from kazma_core.config_store import get_config_store

        return str(get_config_store().get(key, default) or default).strip()
    except Exception:
        return default


def get_livekit_config() -> LiveKitConfig:
    """Live-read LiveKit credentials. Never raises. Empty URL = off."""
    if _env_off("KAZMA_VOICE_DUPLEX"):
        return LiveKitConfig(enabled=False, url="", api_key="", api_secret="")
    url = (
        os.environ.get("LIVEKIT_URL")
        or _store_get("voice.livekit.url")
    ).strip()
    key = (
        os.environ.get("LIVEKIT_API_KEY")
        or _store_get("voice.livekit.api_key")
    ).strip()
    secret = (
        os.environ.get("LIVEKIT_API_SECRET")
        or _store_get("voice.livekit.api_secret")
    ).strip()
    enabled = bool(url and key and secret)
    return LiveKitConfig(enabled=enabled, url=url, api_key=key, api_secret=secret)


def voice_duplex_enabled() -> bool:
    return get_livekit_config().enabled


def sanitize_room_name(raw: str) -> str:
    """LiveKit room names: letters, digits, underscore, hyphen."""
    cleaned = _ROOM_RE.sub("-", (raw or "").strip())[:48].strip("-")
    return cleaned or "default"


def mint_livekit_token(
    *,
    identity: str,
    room: str,
    name: str = "",
    ttl_seconds: int = 3600,
    config: LiveKitConfig | None = None,
) -> str:
    """HS256 LiveKit access token (PyJWT). Raises ValueError if duplex is off."""
    cfg = config or get_livekit_config()
    if not cfg.enabled:
        raise ValueError("LiveKit duplex is not configured")
    ident = sanitize_room_name(identity) or "user"
    room_name = sanitize_room_name(room)
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": cfg.api_key,
        "sub": ident,
        "jti": ident,
        "name": (name or ident)[:64],
        "nbf": now - 10,
        "exp": now + max(60, int(ttl_seconds)),
        "video": {
            "roomJoin": True,
            "room": room_name,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    try:
        import jwt
    except ImportError as exc:
        raise ValueError("PyJWT is required to mint LiveKit tokens") from exc
    return str(jwt.encode(claims, cfg.api_secret, algorithm="HS256"))


def livekit_status() -> dict[str, Any]:
    """Operator-safe status (no secrets)."""
    cfg = get_livekit_config()
    host = ""
    if cfg.url:
        try:
            parsed = urlparse(cfg.url)
            host = parsed.netloc or parsed.hostname or ""
        except Exception:
            host = "(set)"
    return {
        "enabled": cfg.enabled,
        "url": cfg.url if cfg.enabled else "",
        "host": host,
        "brain": "langgraph",
        "mode": "duplex" if cfg.enabled else "turn_based",
        # Browser publishes TTS into the room when duplex is on (AEC).
        "tts_in_room": bool(cfg.enabled),
    }
