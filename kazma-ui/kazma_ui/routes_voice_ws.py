"""Voice Streaming WebSocket — real-time bidirectional audio.

Provides ``/ws/voice`` for live voice chat:

  Browser                 Server
  ──────                 ───────
  audio chunk  ──ws───►  VAD detects speech
  audio chunk  ──ws───►  VAD detects silence
                         STT transcribes segment
                         LLM streams tokens
  ◄──ws───  token        (tokens sent as they arrive)
  ◄──ws───  tts_chunk    TTS synthesized in chunks
  ◄──ws───  tts_done     TTS complete

Message protocol (JSON over WebSocket text frames):

  Client → Server:
    {"type": "start", "stt_provider": "openai", "tts_provider": "edgetts"}
    {"type": "audio", "data": "<base64 PCM 16-bit 16kHz mono>"}
    {"type": "stop"}
    {"type": "config", "stt_provider": "nvidia", "tts_provider": "edgetts"}

  Server → Client:
    {"type": "ready"}
    {"type": "listening"}
    {"type": "transcribing", "partial": "..."}
    {"type": "transcribed", "text": "..."}
    {"type": "token", "content": "..."}        — LLM token stream
    {"type": "tool_call", "name": "..."}
    {"type": "tool_result", "name": "..."}
    {"type": "tts_chunk", "data": "<base64 audio>"}  — TTS audio stream
    {"type": "tts_done"}
    {"type": "done"}
    {"type": "error", "content": "..."}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

__all__ = ["handle_voice_websocket"]


async def handle_voice_websocket(
    websocket: WebSocket,
    graph_getter: Any | None = None,
) -> None:
    """Handle a voice streaming WebSocket connection.

    Expects 16-bit PCM mono audio at 16kHz from the browser.

    Args:
        websocket: The connected WebSocket.
        graph_getter: Optional zero-arg callable returning the live
            checkpointed streaming graph (wired by the app builder). Without
            it, voice falls back to the plain error path.
    """
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "ready"}))

    # Per-connection state
    vad = None
    stt_provider = "openai"
    tts_provider = "edgetts"
    sample_rate = 16000
    is_active = True
    processing = False
    session_id = None
    utterance_task: asyncio.Task[Any] | None = None
    tts_cancel = asyncio.Event()

    async def _cancel_utterance() -> None:
        nonlocal processing, utterance_task
        tts_cancel.set()
        task = utterance_task
        utterance_task = None
        processing = False
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # Audio format conversion buffer
    audio_buffer = bytearray()

    try:
        while is_active:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
            except TimeoutError:
                await websocket.send_text(json.dumps({
                    "type": "error", "content": "Connection timeout"
                }))
                break
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "start":
                stt_provider = msg.get("stt_provider", "openai")
                tts_provider = msg.get("tts_provider", "edgetts")
                sample_rate = msg.get("sample_rate", 16000)
                session_id = msg.get("session_id", None)
                from kazma_core.voice.mode import get_vad

                vad = get_vad(sample_rate=sample_rate)
                await websocket.send_text(json.dumps({"type": "listening"}))

            elif msg_type == "config":
                stt_provider = msg.get("stt_provider", stt_provider)
                tts_provider = msg.get("tts_provider", tts_provider)
                await websocket.send_text(json.dumps({
                    "type": "config_updated",
                    "stt_provider": stt_provider,
                    "tts_provider": tts_provider,
                }))

            elif msg_type == "audio":
                if vad is None:
                    continue
                # Decode base64 PCM data
                audio_b64 = msg.get("data", "")
                try:
                    pcm_bytes = base64.b64decode(audio_b64)
                except Exception:
                    continue

                # Feed to VAD
                segment = vad.feed(pcm_bytes)
                if segment is not None:
                    # Barge-in: a new utterance cancels TTS / in-flight turn.
                    if processing:
                        await _cancel_utterance()
                        await websocket.send_text(json.dumps({"type": "interrupted"}))
                    processing = True
                    tts_cancel.clear()
                    await websocket.send_text(json.dumps({"type": "transcribing"}))

                    async def _run(seg: bytes = segment) -> None:
                        nonlocal processing
                        try:
                            await _process_utterance(
                                websocket, seg, stt_provider, tts_provider,
                                session_id=session_id, graph_getter=graph_getter,
                                cancel=tts_cancel,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            logger.exception("[ws-voice] Processing failed")
                            await websocket.send_text(json.dumps({
                                "type": "error", "content": str(exc)
                            }))
                        finally:
                            processing = False
                            try:
                                await websocket.send_text(
                                    json.dumps({"type": "listening"})
                                )
                            except Exception:
                                pass

                    utterance_task = asyncio.create_task(_run())

            elif msg_type == "interrupt":
                await _cancel_utterance()
                await websocket.send_text(json.dumps({"type": "interrupted"}))
                await websocket.send_text(json.dumps({"type": "listening"}))

            elif msg_type == "stop":
                await _cancel_utterance()
                is_active = False
                break

    except Exception as exc:
        logger.debug("[ws-voice] Connection ended: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def _process_utterance(
    websocket: WebSocket,
    audio_bytes: bytes,
    stt_provider: str,
    tts_provider: str,
    session_id: str | None = None,
    graph_getter: Any | None = None,
    cancel: asyncio.Event | None = None,
) -> None:
    """Transcribe audio, get LLM response, stream tokens + TTS back."""
    from kazma_core.voice.stt import transcribe

    # Step 1: Transcribe
    text = await transcribe(
        audio_bytes,
        provider=stt_provider,
        language="auto",
        audio_format="wav",  # VAD produces PCM, we'll wrap as WAV
    )
    if not text:
        await websocket.send_text(json.dumps({
            "type": "error", "content": "Transcription failed"
        }))
        return

    await websocket.send_text(json.dumps({"type": "transcribed", "text": text}))
    logger.info("[ws-voice] Transcribed: %.100s", text)

    # Step 2: Get LLM response (reuse the SSE chat path)
    # We collect tokens, stream them to the client, and then synthesize TTS
    token_buffer: list[str] = []
    full_response = ""

    try:
        async for event in _stream_llm_response(
            text, session_id=session_id, graph_getter=graph_getter,
        ):
            if cancel is not None and cancel.is_set():
                await websocket.send_text(json.dumps({"type": "interrupted"}))
                return
            event_type = event.get("type")
            if event_type == "token":
                content = event.get("content", "")
                token_buffer.append(content)
                full_response += content
                await websocket.send_text(json.dumps({
                    "type": "token", "content": content
                }))
            elif event_type == "tool_call":
                await websocket.send_text(json.dumps({
                    "type": "tool_call", "name": event.get("name", "")
                }))
            elif event_type == "tool_result":
                await websocket.send_text(json.dumps({
                    "type": "tool_result", "name": event.get("name", "")
                }))
            elif event_type == "done":
                break
            elif event_type == "error":
                await websocket.send_text(json.dumps({
                    "type": "error", "content": event.get("content", "")
                }))
                return
    except Exception as exc:
        await websocket.send_text(json.dumps({
            "type": "error", "content": f"LLM stream failed: {exc}"
        }))
        return

    if not full_response.strip():
        await websocket.send_text(json.dumps({"type": "done"}))
        return

    # Step 3: Synthesize TTS and stream chunks back
    try:
        await _stream_tts(websocket, full_response, tts_provider, cancel=cancel)
    except Exception as exc:
        logger.warning("[ws-voice] TTS failed: %s", exc)
        # TTS is optional — still send done
    await websocket.send_text(json.dumps({"type": "done"}))


async def _stream_llm_response(
    text: str,
    session_id: str | None = None,
    graph_getter: Any | None = None,
) -> Any:
    """Yield LLM response events from the agent runner.

    Uses the live checkpointed streaming graph supplied by the app builder
    (the same one the SSE chat path uses). Falls back to a clear error if no
    graph is available.
    """
    graph = None
    if graph_getter is not None:
        try:
            graph = graph_getter()
        except Exception as exc:
            logger.debug("[ws-voice] graph_getter failed: %s", exc)

    if graph is None:
        logger.warning("[ws-voice] No agent graph available — voice stream cannot run")
        yield {"type": "error", "content": "Agent graph unavailable"}
        return

    if not session_id:
        session_id = f"ws-voice-{id(text)}"

    config = {"configurable": {"thread_id": session_id}}
    inputs = {"messages": [{"role": "user", "content": text}]}

    try:
        async for event in graph.astream_events(inputs, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content"):
                    content = chunk.content if isinstance(chunk.content, str) else ""
                    if content:
                        yield {"type": "token", "content": content}

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                yield {"type": "tool_call", "name": tool_name}

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                yield {"type": "tool_result", "name": tool_name}

    except Exception as exc:
        logger.exception("[ws-voice] LLM stream failed")
        yield {"type": "error", "content": str(exc)}
        return

    yield {"type": "done"}


async def _stream_tts(
    websocket: WebSocket,
    text: str,
    tts_provider: str,
    cancel: asyncio.Event | None = None,
) -> None:
    """Synthesize TTS audio and stream it to the client in chunks.

    For simplicity, we synthesize the full response first, then chunk it.
    True streaming TTS would require provider-specific streaming support.
    """
    import re

    # Clean text for TTS
    clean = re.sub(r"`[^`]*`", "", text)
    clean = re.sub(r"```.*?```", "", clean, flags=re.DOTALL)
    clean = re.sub(r"[*_~]+", "", clean)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
    clean = clean.strip()

    if not clean or len(clean) < 5:
        return

    # Truncate very long text
    if len(clean) > 2000:
        clean = clean[:2000]

    from kazma_core.voice.tts import synthesize

    audio = await synthesize(clean, provider=tts_provider, voice="default", output_format="mp3")
    if not audio:
        return

    # Stream in ~16KB chunks (stop immediately on barge-in).
    chunk_size = 16384
    for i in range(0, len(audio), chunk_size):
        if cancel is not None and cancel.is_set():
            return
        chunk = audio[i:i + chunk_size]
        chunk_b64 = base64.b64encode(chunk).decode("ascii")
        await websocket.send_text(json.dumps({
            "type": "tts_chunk", "data": chunk_b64
        }))
        await asyncio.sleep(0.01)

    await websocket.send_text(json.dumps({"type": "tts_done"}))
