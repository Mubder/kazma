"""Slack voice STT/TTS helpers — Telegram-depth path via voice_helpers."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_gateway.gateway import IncomingMessage

logger = logging.getLogger(__name__)

__all__ = ["send_voice_reply", "transcribe_message"]

_SLACK_API = "https://slack.com/api"


async def transcribe_message(
    msg: IncomingMessage,
    *,
    http: httpx.AsyncClient | None = None,
    bot_token: str = "",
) -> IncomingMessage:
    """Run the shared deep STT pipeline (Slack private URLs need bearer auth)."""
    from kazma_gateway.adapters.voice_helpers import transcribe_inbound_message

    headers = None
    if bot_token:
        headers = {"Authorization": f"Bearer {bot_token}"}
    return await transcribe_inbound_message(
        msg, http=http, auth_headers=headers
    )


async def send_voice_reply(
    *,
    http: httpx.AsyncClient,
    bot_token: str,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
    headers_fn: Any = None,
) -> bool:
    """Synthesize *text* and upload as an audio file to Slack."""
    from kazma_gateway.adapters.voice_helpers import (
        live_voice_settings,
        synthesize_speech,
    )

    cfg = live_voice_settings()
    if not cfg.get("enabled") or not cfg.get("tts_reply", True) or not text:
        return False

    def _headers() -> dict[str, str]:
        if callable(headers_fn):
            return headers_fn()
        return {"Authorization": f"Bearer {bot_token}"}

    try:
        audio = await synthesize_speech(text, require_tts_reply=True)
        if not audio:
            return False
        fmt = str(live_voice_settings().get("tts_output_format") or "mp3")
        ext = "mp3" if fmt in ("mp3", "mpeg") else fmt
        mime = "audio/mpeg" if ext == "mp3" else f"audio/{ext}"
        safe_name = f"reply.{ext}"

        resp = await http.post(
            f"{_SLACK_API}/files.getUploadURLExternal",
            params={"filename": safe_name, "length": str(len(audio))},
            headers=_headers(),
        )
        resp.raise_for_status()
        up = resp.json()
        if not up.get("ok"):
            logger.warning("[Slack] getUploadURLExternal: %s", up.get("error"))
            return False
        ul_resp = await http.post(
            up["upload_url"],
            files={"file": (safe_name, audio, mime)},
        )
        ul_resp.raise_for_status()
        complete_body: dict[str, Any] = {
            "files": [{"id": up["file_id"], "title": safe_name}],
            "channel_id": channel_id,
        }
        if thread_ts:
            complete_body["thread_ts"] = thread_ts
        cp = await http.post(
            f"{_SLACK_API}/files.completeUploadExternal",
            json=complete_body,
            headers=_headers(),
        )
        cp.raise_for_status()
        ok = bool(cp.json().get("ok", False))
        if ok:
            logger.info(
                "[Slack] voice reply sent to %s (%d bytes)", channel_id, len(audio)
            )
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Slack] voice reply failed: %s", type(exc).__name__)
        return False
