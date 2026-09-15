"""Voice Streaming WebSocket — live voice as a first-class mouth.

Provides ``/ws/voice`` for live voice chat:

  Browser                 Server
  ──────                 ───────
  audio chunk  ──ws───►  VAD detects speech
  audio chunk  ──ws───►  VAD closes segment (silence)
                         PCM wrapped as WAV → STT transcribes
                         SAME journal pump as /api/chat/stream
                         (checkpointed graph, HITL-capable)
  ◄──ws───  transcribed  (status toast only — the journal paints the
                          turn in the chat UI; the client projects)
  ◄──ws───  tool_call / tool_result
  ◄──ws───  hitl_paused  danger tool paused → existing web approval
                          card is the resume surface (POST /api/approve)
  ◄──ws───  tts_chunk    one COMPLETE sentence clip (valid MP3 each)
  ◄──ws───  tts_done

Message protocol (JSON over WebSocket text frames):

  Client → Server:
    {"type": "start", "session_id": "...", "stt_provider": "openai",
     "tts_provider": "edgetts", "sample_rate": 16000}
    {"type": "audio", "data": "<base64 PCM 16-bit 16kHz mono>"}
    {"type": "config", "stt_provider": "nvidia", "tts_provider": "edgetts"}
    {"type": "interrupt"}
    {"type": "stop"}

  Server → Client:
    {"type": "ready"}
    {"type": "listening"}
    {"type": "transcribing"}
    {"type": "transcribed", "text": "..."}
    {"type": "tool_call"|"tool_result", "name": "..."}
    {"type": "hitl_paused"}
    {"type": "tts_chunk", "data": "<base64 audio>", "seq": 1}
    {"type": "tts_done"}
    {"type": "interrupted"}
    {"type": "done"}
    {"type": "error", "content": "..."}

There is no ``token`` stream on this socket: Turn Delivery V2 (§31) makes
the journal the source of truth and the chat UI the projection. TTS speaks
only the terminal user-facing reply, never supervisor planning tokens, and
never a failed turn (⚠️-prefixed notices are not spoken).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

__all__ = ["handle_voice_websocket"]

#: Client sample rates the VAD/PCM path accepts (kHz-family rates only).
_ALLOWED_SAMPLE_RATES = frozenset({8000, 16000, 24000, 48000})


async def handle_voice_websocket(
    websocket: WebSocket,
    graph_getter: Any | None = None,
    agent_getter: Any | None = None,
) -> None:
    """Handle a voice streaming WebSocket connection.

    Expects 16-bit PCM mono audio (default 16 kHz) from the browser.
    ``graph_getter`` must return the SAME checkpointed graph the SSE chat
    path uses (``_graph_holder``) — never the checkpointer-less streaming
    graph: a voice HITL pause must be resumable via ``/api/approve``.
    """
    await websocket.accept()

    async def _send(payload: dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    await _send({"type": "ready"})

    # Per-connection state
    vad = None
    stt_provider = "openai"
    tts_provider = "edgetts"
    sample_rate = 16000
    is_active = True
    processing = False
    session: Any | None = None
    session_id = ""
    thread_id = ""
    utterance_task: asyncio.Task[Any] | None = None
    tts_cancel = asyncio.Event()
    # HITL resume watcher spawned by an utterance that paused; kept here so
    # barge-in / stop / close can cancel it (a superseding turn must not be
    # spoken twice).
    resume_slot: dict[str, asyncio.Task[Any] | None] = {"task": None}

    async def _cancel_utterance() -> None:
        nonlocal processing, utterance_task
        tts_cancel.set()
        processing = False
        tasks = [utterance_task, resume_slot.get("task")]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        for task in tasks:
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        utterance_task = None
        resume_slot["task"] = None

    async def _send_error(content: str) -> None:
        await _send({"type": "error", "content": content})

    try:
        while is_active:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
            except TimeoutError:
                await _send_error("Connection timeout")
                break
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "start":
                # A fresh start (page reload / session switch) abandons any
                # in-flight utterance on the old thread.
                await _cancel_utterance()
                try:
                    requested_rate = int(msg.get("sample_rate", 16000) or 16000)
                except (TypeError, ValueError):
                    requested_rate = 16000
                if requested_rate not in _ALLOWED_SAMPLE_RATES:
                    # Keep the VAD unstarted: audio frames are ignored until
                    # a valid start arrives.
                    await _send_error(
                        f"Unsupported sample_rate {requested_rate}. "
                        f"Allowed: {sorted(_ALLOWED_SAMPLE_RATES)}."
                    )
                    continue
                sample_rate = requested_rate
                stt_provider = msg.get("stt_provider", "openai")
                tts_provider = msg.get("tts_provider", "edgetts")

                # Session ownership BEFORE any persistence: resolve the
                # client's web session through the tenant-scoped store and
                # use its thread_id as the graph key. A gw-* platform thread
                # or an unknown session is refused outright (IDOR guard).
                from kazma_ui.voice_turn import resolve_voice_session

                pair = resolve_voice_session(str(msg.get("session_id") or ""))
                if pair is None:
                    await _send_error(
                        "Unknown or unauthorized chat session — reload the page "
                        "and start live voice again."
                    )
                    await websocket.close(code=4003, reason="Unknown session")
                    break
                session, thread_id = pair
                session_id = str(getattr(session, "session_id", "") or "")

                from kazma_core.voice.mode import get_vad

                vad = get_vad(sample_rate=sample_rate)
                await _send({"type": "listening"})

            elif msg_type == "config":
                stt_provider = msg.get("stt_provider", stt_provider)
                tts_provider = msg.get("tts_provider", tts_provider)
                await _send({
                    "type": "config_updated",
                    "stt_provider": stt_provider,
                    "tts_provider": tts_provider,
                })

            elif msg_type == "audio":
                if vad is None:
                    continue
                audio_b64 = msg.get("data", "")
                try:
                    pcm_bytes = base64.b64decode(audio_b64)
                except Exception:
                    continue

                segment = vad.feed(pcm_bytes)
                if segment is not None:
                    if processing:
                        # Barge-in: a new utterance stops playback/TTS; the
                        # in-flight turn is superseded by the new one (same
                        # rule as a follow-up typed message).
                        from kazma_core.metrics import record_voice_barge_in

                        record_voice_barge_in()
                        await _cancel_utterance()
                        await _send({"type": "interrupted"})
                    processing = True
                    tts_cancel.clear()
                    await _send({"type": "transcribing"})

                    async def _run(
                        seg: bytes = segment,
                        stt: str = stt_provider,
                        tts: str = tts_provider,
                        rate: int = sample_rate,
                        sess: Any = session,
                        tid: str = thread_id,
                        sid: str = session_id,
                        rslot: dict[str, asyncio.Task[Any] | None] = resume_slot,
                    ) -> None:
                        nonlocal processing
                        try:
                            await _process_utterance(
                                websocket,
                                seg,
                                stt_provider=stt,
                                tts_provider=tts,
                                sample_rate=rate,
                                session=sess,
                                thread_id=tid,
                                session_id=sid,
                                graph_getter=graph_getter,
                                agent_getter=agent_getter,
                                cancel=tts_cancel,
                                resume_slot=rslot,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            from kazma_core.errors import safe_error

                            logger.exception("[ws-voice] Processing failed")
                            await _send_error(str(safe_error(exc)))
                        finally:
                            processing = False
                            await _send({"type": "listening"})

                    from kazma_core.background import spawn_background

                    utterance_task = spawn_background(
                        _run(), name=f"ws-voice:{thread_id[:12] or 'utterance'}"
                    )

            elif msg_type == "interrupt":
                from kazma_core.metrics import record_voice_barge_in

                record_voice_barge_in()
                await _cancel_utterance()
                await _send({"type": "interrupted"})
                await _send({"type": "listening"})

            elif msg_type == "stop":
                await _cancel_utterance()
                is_active = False
                break

    except Exception as exc:
        logger.debug("[ws-voice] Connection ended: %s", exc)
    finally:
        try:
            await _cancel_utterance()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def _voice_settings() -> dict[str, str]:
    """ConfigStore voice overrides — ConfigStore wins over client choices."""
    out: dict[str, str] = {"stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": ""}
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        for key in out:
            val = cs.get(f"voice.{key}")
            if val is not None and str(val).strip() and str(val).strip().lower() != "none":
                out[key] = str(val).strip()
    except Exception:
        pass
    return out


async def _process_utterance(
    websocket: WebSocket,
    audio_bytes: bytes,
    *,
    stt_provider: str,
    tts_provider: str,
    sample_rate: int,
    session: Any,
    thread_id: str,
    session_id: str,
    graph_getter: Any | None = None,
    agent_getter: Any | None = None,
    cancel: asyncio.Event | None = None,
    resume_slot: dict[str, asyncio.Task[Any] | None] | None = None,
) -> None:
    """Transcribe → submit to the journal pump → speak the final reply."""
    from kazma_core.metrics import (
        record_voice_stt,
        record_voice_utterance,
    )
    from kazma_core.voice.pcm import (
        MIN_SPEECH_SECONDS,
        pcm16le_duration_seconds,
        pcm16le_to_wav,
    )
    from kazma_core.voice.stt import get_last_error, sanitize_transcript, transcribe

    segment_at = time.monotonic()
    cfg = _voice_settings()
    if cfg["stt_provider"]:
        stt_provider = cfg["stt_provider"]
    language = cfg["stt_language"] or "auto"

    # Mic-open pops and button clicks are tens of ms. Sending them to
    # Whisper yields either empty (we used to toast "Transcription failed")
    # or a hallucinated "Thank you" / "you". Drop them before the API.
    duration = pcm16le_duration_seconds(audio_bytes, sample_rate)
    if duration < MIN_SPEECH_SECONDS:
        record_voice_utterance("ws", "too_short")
        logger.debug("[ws-voice] skipping %.0fms segment (min %.0fms)", duration * 1000, MIN_SPEECH_SECONDS * 1000)
        return

    # Step 1: Transcribe — VAD yields raw PCM; STT providers get a real WAV.
    wav = pcm16le_to_wav(audio_bytes, sample_rate=sample_rate)
    _t0 = time.monotonic()
    raw = await transcribe(
        wav,
        provider=stt_provider,
        language=language,
        audio_format="wav",
    )
    text = sanitize_transcript(raw)
    if not text:
        record_voice_stt(stt_provider, "empty", time.monotonic() - _t0)
        record_voice_utterance("ws", "stt_empty")
        # Empty Whisper / hallucination: keep listening. VAD segments always
        # include ~1.5s of trailing silence, so they look "long enough" even
        # when nobody spoke — that used to toast "Transcription failed" on
        # every pause. Only surface a real provider failure (no key, HTTP).
        err = get_last_error()
        if err:
            logger.warning("[ws-voice] STT provider failed: %s", err)
            await _ws_send(websocket, {"type": "error", "content": "Transcription failed"})
        else:
            logger.debug("[ws-voice] no speech in %.1fs segment", duration)
        return
    record_voice_stt(stt_provider, "ok", time.monotonic() - _t0)
    record_voice_utterance("ws", "stt_ok")
    await _ws_send(websocket, {"type": "transcribed", "text": text})
    logger.info("[ws-voice] Transcribed: %.100s", text)

    if cancel is not None and cancel.is_set():
        return

    # Step 2: run the turn through the SAME pump as /api/chat/stream.
    graph = None
    if graph_getter is not None:
        try:
            graph = graph_getter()
        except Exception as exc:
            logger.debug("[ws-voice] graph_getter failed: %s", exc)

    agent = None
    if agent_getter is not None:
        try:
            agent = agent_getter()
        except Exception:
            agent = None

    from kazma_ui.voice_turn import run_voice_user_turn, watch_voice_resume

    async def _forward_progress(ev: str, data: dict[str, Any]) -> None:
        await _ws_send(websocket, {"type": ev, "name": str(data.get("tool_name") or "")})

    result = await run_voice_user_turn(
        graph=graph,
        session=session,
        thread_id=thread_id,
        session_id=session_id,
        user_text=text,
        system_prompt=str(getattr(agent, "system_prompt", "") or ""),
        cost_breaker=getattr(agent, "cost_breaker", None),
        on_progress=_forward_progress,
    )

    if cancel is not None and cancel.is_set():
        return

    if result.error:
        record_voice_utterance("ws", "turn_error")
        await _ws_send(websocket, {"type": "error", "content": result.error})
        await _ws_send(websocket, {"type": "done"})
        return

    if result.interrupted:
        # HITL pause: the existing web approval card (gate registry) is the
        # resume surface. If this socket is still open when the approved
        # turn finishes, speak its final reply.
        record_voice_utterance("ws", "hitl_paused")
        await _ws_send(websocket, {"type": "hitl_paused"})
        if resume_slot is not None:
            from kazma_core.background import spawn_background

            async def _speak_resumed(reply_text: str) -> None:
                await _speak_reply(
                    websocket, reply_text, tts_provider, cancel=cancel,
                    segment_closed_at=time.monotonic(),
                )

            resume_slot["task"] = spawn_background(
                watch_voice_resume(
                    thread_id=thread_id,
                    session_id=session_id,
                    speak=_speak_resumed,
                ),
                name=f"ws-voice-resume:{thread_id[:12]}",
            )
        return

    await _speak_reply(
        websocket,
        result.text,
        tts_provider,
        cancel=cancel,
        segment_closed_at=segment_at,
    )
    await _ws_send(websocket, {"type": "done"})


async def _ws_send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:
        pass


async def _speak_reply(
    websocket: WebSocket,
    text: str,
    tts_provider: str,
    *,
    cancel: asyncio.Event | None = None,
    segment_closed_at: float | None = None,
) -> None:
    """Speak the final user-facing reply, one sentence clip at a time.

    Each ``tts_chunk`` is a COMPLETE per-sentence audio file (valid MP3),
    so the client can play clips as they arrive without decoding partial
    frames. First-audio latency = synthesis of sentence 1, not of the whole
    reply. A failed/notice reply (⚠️-prefixed) is never spoken — that is the
    "model stopped thinking" bug class (§ invariant 11).
    """
    from kazma_core.metrics import (
        record_voice_first_audio,
        record_voice_tts,
        record_voice_utterance,
    )
    from kazma_core.voice.tts import split_sentences, synthesize_stream

    # Never speak a failure notice or an empty turn.
    clean_text = (text or "").strip()
    if not clean_text or clean_text.lstrip().startswith("⚠️"):
        if clean_text:
            logger.info("[ws-voice] skipping TTS of failure notice (%d chars)", len(clean_text))
        return

    from kazma_gateway.adapters.voice_helpers import prepare_tts_text

    clean = prepare_tts_text(clean_text)
    if not clean or len(clean) < 5:
        return

    cfg = _voice_settings()
    if cfg["tts_provider"]:
        tts_provider = cfg["tts_provider"]
    voice = cfg["tts_voice"] or "default"

    sentences = split_sentences(clean)
    if not sentences:
        return

    t0 = time.monotonic()
    first_audio_done = False
    seq = 0
    status = "ok"
    try:
        for sentence in sentences:
            if cancel is not None and cancel.is_set():
                status = "cancelled"
                return
            clip = bytearray()
            try:
                async for chunk in synthesize_stream(
                    sentence, provider=tts_provider, voice=voice
                ):
                    if cancel is not None and cancel.is_set():
                        status = "cancelled"
                        return
                    clip.extend(chunk)
            except Exception as exc:
                logger.warning("[ws-voice] TTS failed mid-reply: %s", exc)
                status = "error"
                return
            if not clip:
                continue
            seq += 1
            await _ws_send(websocket, {
                "type": "tts_chunk",
                "data": base64.b64encode(bytes(clip)).decode("ascii"),
                "seq": seq,
            })
            if not first_audio_done:
                # First-audio honesty metric: segment close → first chunk.
                first_audio_done = True
                record_voice_first_audio(time.monotonic() - (segment_closed_at or t0))
    finally:
        record_voice_tts(tts_provider, status, time.monotonic() - t0)
        if status == "ok":
            record_voice_utterance("ws", "spoken")
    if status == "ok":
        await _ws_send(websocket, {"type": "tts_done"})
