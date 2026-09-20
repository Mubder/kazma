"""Voice live pipeline — honesty, one-pump submission, sentence TTS.

Covers the 2026-09 voice rework:

PR A (honesty):
- VAD PCM is wrapped as a REAL WAV before STT (RIFF header, not a label).
- EnergyVAD does not drop partial frames between chunks.
- KAZMA_SILERO_VAD=1 without a real classifier returns EnergyVAD (no
  torch.hub placebo; WebRTC adapter only when webrtcvad is importable and
  the rate is supported).
- REST /api/voice/stt works with an in-process fake provider and never
  writes kazma-data/stt_error.log.
- WS TTS honors the ConfigStore voice.tts_voice override.

PR B (one mouth):
- resolve_voice_session refuses gw-* / unknown sessions (IDOR guard).
- run_voice_user_turn submits the transcript to the journal pump; the
  result text is the TERMINAL reply, not the concatenation of supervisor
  tokens; a HITL pause surfaces as interrupted (no TTS).
- A failed turn (⚠️ notice) is never spoken.
- app.py wires the voice WS to _graph_holder (source assertion).
- chat.js no longer exposes the live-voice paint hooks (no dual paint).

PR C (sentence TTS):
- split_sentences handles EN/Arabic/fullwidth terminators.
- synthesize_stream streams EdgeTTS chunks as produced; buffered providers
  yield one complete clip.
- _speak_reply emits one COMPLETE per-sentence tts_chunk (audio for
  sentence 1 arrives without waiting for sentence 2), honors barge-in.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
import uuid
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


# ── Helpers ─────────────────────────────────────────────────────────────


class FakeWebSocket:
    """Minimal WebSocket capture for the voice WS handler helpers."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        pass

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, **_kw) -> None:
        self.closed = True

    def of(self, type_: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == type_]


class FakeSessionStore:
    """Tenant-scoped-looking session store with one visible session."""

    def __init__(self, session) -> None:
        self.session = session
        self.puts = 0

    def get(self, session_id: str):
        return self.session if session_id == self.session.session_id else None

    def get_or_create(self, session_id: str, **_kw):
        return self.session

    def put(self, session) -> None:
        self.puts += 1


def _make_session(sid: str = "", thread_id: str = ""):
    from kazma_ui.session_manager import ChatSession

    sess = ChatSession(session_id=sid or f"vt-{uuid.uuid4().hex[:8]}")
    sess.thread_id = thread_id
    return sess


# ── PR A: WAV wrap ──────────────────────────────────────────────────────


def test_pcm16le_to_wav_builds_valid_riff_header() -> None:
    from kazma_core.voice.pcm import pcm16le_to_wav

    pcm = b"\x01\x02" * 160  # 160 samples
    wav = pcm16le_to_wav(pcm, sample_rate=16000, channels=1)

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + len(pcm)
    import struct

    riff_size, fmt_size, audio_fmt, channels, rate, _br, _ba, bits, data_size = (
        struct.unpack_from("<I", wav, 4)[0],
        struct.unpack_from("<I", wav, 16)[0],
        struct.unpack_from("<H", wav, 20)[0],
        struct.unpack_from("<H", wav, 22)[0],
        struct.unpack_from("<I", wav, 24)[0],
        struct.unpack_from("<I", wav, 28)[0],
        struct.unpack_from("<H", wav, 32)[0],
        struct.unpack_from("<H", wav, 34)[0],
        struct.unpack_from("<I", wav, 40)[0],
    )
    assert riff_size == 36 + len(pcm)
    assert fmt_size == 16 and audio_fmt == 1  # PCM
    assert channels == 1 and rate == 16000 and bits == 16
    assert data_size == len(pcm)
    assert wav[44:] == pcm


@pytest.mark.asyncio
async def test_utterance_sends_wav_shaped_bytes_to_stt(monkeypatch) -> None:
    """_process_utterance wraps the VAD PCM; STT receives RIFF bytes."""
    from kazma_ui import routes_voice_ws as rws

    captured: dict = {}

    async def fake_transcribe(audio_bytes, *, provider="openai", language="auto",
                              api_key=None, audio_format="ogg"):
        captured["bytes"] = audio_bytes
        captured["format"] = audio_format
        return None  # empty transcript ends the turn before the graph

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })

    ws = FakeWebSocket()
    pcm = b"\x00\x7f" * 16000  # 1 s of loud PCM (above MIN_SPEECH_SECONDS)
    await rws._process_utterance(
        ws, pcm,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=_make_session(), thread_id="vt-thread", session_id="vt-sess",
    )
    assert captured["format"] == "wav"
    assert captured["bytes"][:4] == b"RIFF"
    assert len(captured["bytes"]) == 44 + len(pcm)


@pytest.mark.asyncio
async def test_short_segment_is_silent_not_transcription_failed(monkeypatch) -> None:
    """Mic-open pops must not toast 'Transcription failed'."""
    from kazma_ui import routes_voice_ws as rws

    called = {"n": 0}

    async def fake_transcribe(*_a, **_k):
        called["n"] += 1
        return None

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    ws = FakeWebSocket()
    pcm = b"\x00\x7f" * 480  # 30 ms
    await rws._process_utterance(
        ws, pcm,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=_make_session(), thread_id="vt-thread", session_id="vt-sess",
        graph_getter=lambda: object(),
    )
    assert called["n"] == 0
    assert not ws.of("error")
    assert not ws.of("transcribed")


@pytest.mark.asyncio
async def test_empty_whisper_on_long_segment_is_not_transcription_failed(
    monkeypatch,
) -> None:
    """VAD clips always include ~1.5s of silence so duration >= 1s.
    Empty Whisper used to toast 'Transcription failed' on every pause."""
    from kazma_ui import routes_voice_ws as rws

    async def fake_transcribe(*_a, **_k):
        return ""  # successful no-speech, not a provider crash

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr("kazma_core.voice.stt.get_last_error", lambda: None)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    ws = FakeWebSocket()
    pcm = b"\x00\x00" * 32000  # 2 s of silence, well above 1.0s
    await rws._process_utterance(
        ws, pcm,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=_make_session(), thread_id="vt-thread", session_id="vt-sess",
        graph_getter=lambda: object(),
    )
    assert not ws.of("error"), ws.sent
    assert not ws.of("transcribed")


@pytest.mark.asyncio
async def test_real_stt_provider_failure_still_errors(monkeypatch) -> None:
    from kazma_ui import routes_voice_ws as rws

    async def fake_transcribe(*_a, **_k):
        return None

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr(
        "kazma_core.voice.stt.get_last_error",
        lambda: "OpenAI API key not configured",
    )
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    ws = FakeWebSocket()
    pcm = b"\x00\x7f" * 16000
    await rws._process_utterance(
        ws, pcm,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=_make_session(), thread_id="vt-thread", session_id="vt-sess",
        graph_getter=lambda: object(),
    )
    errs = ws.of("error")
    assert errs and "STT is not configured" in errs[0]["content"]


def test_vad_does_not_keep_full_silence_tail() -> None:
    from kazma_core.voice.vad import EnergyVAD

    vad = EnergyVAD(sample_rate=16000, silence_duration=1.5, min_speech_duration=0.3)
    loud = b"\x00\x7f" * 1600  # 100 ms
    silent = b"\x00\x00" * 1600
    segment = None
    for _ in range(8):
        vad.feed(loud)
    for _ in range(30):
        r = vad.feed(silent)
        if r is not None:
            segment = r
            break
    assert segment is not None
    # Untrimmed this would be speech + 1.5s of zeros (~48 KB of silence).
    # After the endpoint trim it must stay under ~1.4 s (44800 bytes).
    assert len(segment) < 45000, len(segment)


@pytest.mark.asyncio
async def test_whisper_hallucination_is_not_a_user_turn(monkeypatch) -> None:
    from kazma_ui import routes_voice_ws as rws
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(
        _streaming, "_drive_graph_to_journal",
        _drive_script([("done", {"content": "should not run", "interrupted": False})]),
    )

    async def fake_transcribe(*_a, **_k):
        return "Thank you."

    spoken: list[str] = []

    async def fake_stream(text, **_kw):
        spoken.append(text)
        yield b"audio"

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    ws = FakeWebSocket()
    pcm = b"\x00\x7f" * 16000
    await rws._process_utterance(
        ws, pcm,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=_make_session(), thread_id="vt-thread", session_id="vt-sess",
        graph_getter=lambda: object(),
    )
    assert not ws.of("transcribed")
    assert not ws.of("error")
    assert spoken == []


def test_public_stt_hint_does_not_call_nvidia_asr_a_missing_key() -> None:
    from kazma_ui.routes_voice_ws import _public_stt_hint

    nvidia = _public_stt_hint("NVIDIA ASR endpoint not configured")
    assert "Whisper" in nvidia
    assert "add a key" not in nvidia.lower()
    key = _public_stt_hint("OpenAI API key not configured")
    assert "add a key" in key.lower()


@pytest.mark.asyncio
async def test_transcribe_preferring_skips_nvidia_without_asr(monkeypatch) -> None:
    from kazma_core.voice import stt as stt_mod

    calls: list[str] = []

    async def fake_transcribe(audio_bytes, *, provider="openai", **_k):
        calls.append(provider)
        if provider == "openai":
            return "hello from openai"
        return None

    monkeypatch.setattr(stt_mod, "transcribe", fake_transcribe)
    monkeypatch.setattr(stt_mod, "_nvidia_asr_base_url", lambda: None)
    text = await stt_mod.transcribe_preferring(b"RIFF", provider="nvidia", audio_format="wav")
    assert text == "hello from openai"
    assert "nvidia" not in calls
    assert calls == ["openai"]


def test_sanitize_transcript_drops_no_speech_keeps_real_replies() -> None:
    from kazma_core.voice.stt import sanitize_transcript

    assert sanitize_transcript("Thank you.") is None
    assert sanitize_transcript("you") is None
    assert sanitize_transcript("...") is None
    assert sanitize_transcript("شكرا") is None
    assert sanitize_transcript("yes") == "yes"
    assert sanitize_transcript("schedule the meeting") == "schedule the meeting"
    assert sanitize_transcript(None) is None


def test_pcm16le_duration_seconds() -> None:
    from kazma_core.voice.pcm import pcm16le_duration_seconds

    assert pcm16le_duration_seconds(b"\x00\x00" * 16000, 16000) == 1.0
    assert pcm16le_duration_seconds(b"", 16000) == 0.0


def test_hold_to_record_and_live_capture_guards() -> None:
    voice = (_REPO / "kazma-ui/kazma_ui/static/js/voice.js").read_text(encoding="utf-8")
    assert "_MIN_HOLD_MS" in voice
    assert "_downsampleTo16k" in voice
    assert "muteGain.gain.value = 0" in voice
    assert "Hold the mic to record" in voice
    assert "is-recording" in voice
    assert "micIcon.style.display" not in voice
    v5 = (_REPO / "kazma-ui/kazma_ui/static/css/kazma.v5.css").read_text(encoding="utf-8")
    # Idle: recording circle hidden. Recording: circle shown, mic glyph hidden.
    assert ".composer-voice-btn .composer-mic-recording { display: none; }" in v5
    assert ".composer-voice-btn.is-recording .composer-mic-recording { display: block; }" in v5


def test_stt_language_is_a_form_select_like_the_other_voice_fields() -> None:
    html = (_REPO / "kazma-ui/kazma_ui/templates/settings.html").read_text(encoding="utf-8")
    js = (_REPO / "kazma-ui/kazma_ui/static/js/settings_integrations.js").read_text(
        encoding="utf-8"
    )
    assert 'x-model="sttLanguageType"' in html
    assert 'class="form-select" x-model="sttLanguageType"' in html
    assert 'placeholder="auto or en, ar, fr..."' not in html
    assert "onSttLanguageTypeChange" in js
    assert "_syncSttLanguageType" in js


# ── PR A: VAD carry (no dropped partial frames) ─────────────────────────


def test_energy_vad_does_not_drop_partial_frames_between_chunks() -> None:
    from kazma_core.voice.vad import EnergyVAD

    vad = EnergyVAD(sample_rate=16000)  # 480-sample frames
    loud = b"\x00\x7f" * 4096  # ScriptProcessor-sized chunk, loud
    silent = b"\x00\x00" * 4096

    segment = None
    for _ in range(10):
        r = vad.feed(loud)
        assert r is None
    for _ in range(60):
        r = vad.feed(silent)
        if r is not None:
            segment = r
            break
    assert segment is not None, "silence should close the segment"

    # No-drop invariant: EVERY loud sample reached the segment. The old
    # per-chunk loop dropped the trailing partial frame of each 8192-byte
    # chunk (512 B × 10 = 5120 B lost) — the segment came up short.
    total_loud_bytes = 10 * len(loud)
    assert len(segment) % 960 == 0  # whole 30 ms frames only
    assert len(segment) >= total_loud_bytes


# ── PR A: Silero honesty ────────────────────────────────────────────────


def test_silero_flag_without_classifier_returns_plain_energy_vad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.voice import mode
    from kazma_core.voice.vad import EnergyVAD

    monkeypatch.setenv("KAZMA_SILERO_VAD", "1")
    monkeypatch.setitem(sys.modules, "webrtcvad", None)  # force ImportError

    vad = mode.get_vad(sample_rate=16000)
    assert type(vad) is EnergyVAD  # not a wrapper hiding an unused model


def test_mode_module_has_no_torch_hub_placebo() -> None:
    src = (_REPO / "kazma-core/kazma_core/voice/mode.py").read_text(encoding="utf-8")
    assert "torch.hub" not in src
    assert "_SileroWrapper" not in src


def test_webrtc_adapter_is_real_and_rate_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """webrtcvad present → real adapter; unsupported rate → EnergyVAD."""
    from kazma_core.voice import mode
    from kazma_core.voice.vad import EnergyVAD

    calls: list[bytes] = []

    class _FakeVad:
        def __init__(self, level: int) -> None:
            pass

        def is_speech(self, frame: bytes, sample_rate: int) -> bool:
            calls.append(frame)
            return frame != b"\x00\x00" * (len(frame) // 2)

    fake_mod = types.SimpleNamespace(Vad=_FakeVad)
    monkeypatch.setenv("KAZMA_SILERO_VAD", "1")
    monkeypatch.setitem(sys.modules, "webrtcvad", fake_mod)

    vad = mode.get_vad(sample_rate=16000)
    assert isinstance(vad, mode._WebRtcVAD)
    # Loud frame → speech state; is_speech was actually consulted.
    vad.feed(b"\x00\x7f" * 480)
    assert vad.is_speaking
    assert calls, "the webrtcvad model must be scored, not just loaded"

    # 24 kHz is legal for Kazma but not for webrtcvad → plain EnergyVAD.
    vad24 = mode.get_vad(sample_rate=24000)
    assert type(vad24) is EnergyVAD


# ── PR A: REST STT endpoint (fake provider, no debug log file) ──────────


@pytest.mark.asyncio
async def test_rest_stt_with_fake_provider(monkeypatch, tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.voice import stt as stt_mod
    from kazma_ui import routes_voice

    async def fake_provider(audio_bytes, *, language="auto", api_key=None,
                            audio_format="ogg"):
        return "hello from fake"

    stt_mod.register_stt_provider("fake-stt", fake_provider)

    class _NoConfigStore:
        def get(self, key):
            return None

    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store", lambda: _NoConfigStore()
    )
    monkeypatch.chdir(tmp_path)

    app = FastAPI()
    app.include_router(routes_voice.router)
    client = TestClient(app)

    resp = client.post(
        "/api/voice/stt",
        files={"file": ("voice.wav", b"RIFF-fake-bytes", "audio/wav")},
        data={"provider": "fake-stt", "language": "auto"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "hello from fake"
    # The removed debug log must not come back.
    assert not (tmp_path / "kazma-data" / "stt_error.log").exists()


@pytest.mark.asyncio
async def test_rest_stt_empty_body_is_400(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import routes_voice

    class _NoConfigStore:
        def get(self, key):
            return None

    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store", lambda: _NoConfigStore()
    )
    app = FastAPI()
    app.include_router(routes_voice.router)
    client = TestClient(app)

    resp = client.post(
        "/api/voice/stt",
        files={"file": ("voice.wav", b"", "audio/wav")},
        data={"provider": "openai", "language": "auto"},
    )
    assert resp.status_code == 400


# ── PR B: session ownership ─────────────────────────────────────────────


def test_resolve_voice_session_accepts_owned_web_session(monkeypatch) -> None:
    from kazma_ui import voice_turn

    sess = _make_session(thread_id="")
    store = FakeSessionStore(sess)
    monkeypatch.setattr("kazma_ui.session_manager.get_session_manager", lambda: store)

    pair = voice_turn.resolve_voice_session(sess.session_id)
    assert pair is not None
    resolved, thread_id = pair
    assert thread_id == sess.session_id  # web session_id == thread_id
    assert store.puts == 1  # thread binding persisted


def test_resolve_voice_session_rejects_foreign_and_gateway_ids(monkeypatch) -> None:
    from kazma_ui import voice_turn

    mine = _make_session()
    store = FakeSessionStore(mine)
    monkeypatch.setattr("kazma_ui.session_manager.get_session_manager", lambda: store)

    # Unknown session (another tenant's) is invisible → rejected.
    assert voice_turn.resolve_voice_session("someone-elses-session") is None
    # Gateway threads must not be drivable from the browser.
    assert voice_turn.resolve_voice_session("gw-telegram-123") is None
    assert voice_turn.resolve_voice_session("") is None


# ── PR B: one pump, terminal reply only ─────────────────────────────────


def _drive_script(frames: list[tuple[str, dict]]):
    """Build a fake _drive_graph_to_journal that journals the given frames."""

    async def _drive(graph, input_state, config, *, thread_id="", session_id="",
                     reply_turn_id=""):
        from kazma_ui.delivery import get_turn_broker

        broker = get_turn_broker()
        for ev, data in frames:
            await broker.emit(thread_id, {"type": ev, "data": data})

    return _drive


@pytest.mark.asyncio
async def test_voice_turn_text_is_terminal_reply_not_token_concat(
    monkeypatch,
) -> None:
    """Supervisor plan + tool tokens are journaled but the spoken text is
    only the terminal `done` content."""
    from kazma_ui import voice_turn
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(
        _streaming, "_drive_graph_to_journal",
        _drive_script([
            ("token", {"content": "Batch 1/4: planning"}),
            ("tool_call", {"tool_name": "demo_tool"}),
            ("token", {"content": "hop narration"}),
            ("done", {"content": "FINAL ANSWER", "interrupted": False}),
        ]),
    )
    monkeypatch.setattr(
        "kazma_ui.session_manager.get_session_manager",
        lambda: FakeSessionStore(_make_session()),
    )

    sess = _make_session(thread_id=f"vt-{uuid.uuid4().hex[:8]}")
    result = await voice_turn.run_voice_user_turn(
        graph=object(),
        session=sess,
        thread_id=sess.thread_id,
        session_id=sess.session_id,
        user_text="what is the answer?",
    )
    assert result.error == ""
    assert result.interrupted is False
    assert result.text == "FINAL ANSWER"


@pytest.mark.asyncio
async def test_voice_turn_hitl_pause_is_interrupted_not_synthesized(
    monkeypatch,
) -> None:
    from kazma_ui import routes_voice_ws as rws
    from kazma_ui import voice_turn
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(
        _streaming, "_drive_graph_to_journal",
        _drive_script([
            ("token", {"content": "I need to run that tool"}),
            ("approval_required", {"tool": "shell_exec", "thread_id": "t"}),
        ]),
    )

    async def fake_transcribe(audio_bytes, **_kw):
        return "run the danger tool"

    spoken: list[str] = []

    async def fake_stream(text, **_kw):
        spoken.append(text)
        yield b"SHOULD-NOT-SPEAK"

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    # The resume watcher needs a store; none will be touched (turn paused).
    monkeypatch.setattr(
        "kazma_ui.session_manager.get_session_manager",
        lambda: FakeSessionStore(_make_session()),
    )

    sess = _make_session(thread_id=f"vt-{uuid.uuid4().hex[:8]}")
    ws = FakeWebSocket()
    await rws._process_utterance(
        ws, b"\x00\x7f" * 16000,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=sess, thread_id=sess.thread_id, session_id=sess.session_id,
        graph_getter=lambda: object(),
        resume_slot={"task": None},
    )
    assert ws.of("hitl_paused")
    assert spoken == []  # TTS must not run on a HITL-paused turn
    assert not ws.of("tts_chunk")
    assert not ws.of("tts_done")
    await asyncio.sleep(0.05)  # let the resume watcher drain and exit


@pytest.mark.asyncio
async def test_failed_turn_notice_is_never_spoken(monkeypatch) -> None:
    from kazma_ui import routes_voice_ws as rws
    from kazma_ui import voice_turn
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(
        _streaming, "_drive_graph_to_journal",
        _drive_script([
            ("done", {"content": "⚠️ The model stopped responding. Try again.", "interrupted": False}),
        ]),
    )

    async def fake_transcribe(audio_bytes, **_kw):
        return "hello"

    spoken: list[str] = []

    async def fake_stream(text, **_kw):
        spoken.append(text)
        yield b"audio"
        return

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "", "tts_provider": "", "tts_voice": "",
    })
    monkeypatch.setattr(
        "kazma_ui.session_manager.get_session_manager",
        lambda: FakeSessionStore(_make_session()),
    )

    sess = _make_session(thread_id=f"vt-{uuid.uuid4().hex[:8]}")
    ws = FakeWebSocket()
    await rws._process_utterance(
        ws, b"\x00\x7f" * 16000,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=sess, thread_id=sess.thread_id, session_id=sess.session_id,
        graph_getter=lambda: object(),
    )
    assert spoken == []  # turn_failed class: never speak over a broken turn
    assert not ws.of("tts_chunk")
    assert ws.of("done")


@pytest.mark.asyncio
async def test_ws_tts_uses_configured_voice(monkeypatch) -> None:
    from kazma_ui import routes_voice_ws as rws
    from kazma_ui import voice_turn
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(
        _streaming, "_drive_graph_to_journal",
        _drive_script([("done", {"content": "All done here.", "interrupted": False})]),
    )

    async def fake_transcribe(audio_bytes, **_kw):
        return "say something"

    seen: dict = {}

    async def fake_stream(text, *, provider="edgetts", voice="default", **_kw):
        seen["voice"] = voice
        seen["text"] = text
        yield b"clip-bytes"

    monkeypatch.setattr("kazma_core.voice.stt.transcribe", fake_transcribe)
    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)
    monkeypatch.setattr(rws, "_voice_settings", lambda: {
        "stt_provider": "", "stt_language": "",
        "tts_provider": "", "tts_voice": "ar-EG-SalmaNeural",
    })
    monkeypatch.setattr(
        "kazma_ui.session_manager.get_session_manager",
        lambda: FakeSessionStore(_make_session()),
    )

    sess = _make_session(thread_id=f"vt-{uuid.uuid4().hex[:8]}")
    ws = FakeWebSocket()
    await rws._process_utterance(
        ws, b"\x00\x7f" * 16000,
        stt_provider="openai", tts_provider="edgetts", sample_rate=16000,
        session=sess, thread_id=sess.thread_id, session_id=sess.session_id,
        graph_getter=lambda: object(),
    )
    assert seen.get("voice") == "ar-EG-SalmaNeural"  # ConfigStore wins
    chunks = ws.of("tts_chunk")
    assert chunks and chunks[0]["seq"] == 1
    assert ws.of("tts_done")


# ── PR B: wiring source assertions ──────────────────────────────────────


def test_app_wires_voice_ws_to_graph_holder() -> None:
    src = (_REPO / "kazma-ui/kazma_ui/app.py").read_text(encoding="utf-8")
    start = src.find("Voice Streaming WebSocket")
    end = src.find("Telemetry SSE Route")
    region = src[start:end]
    assert region, "voice WS mount block not found"
    assert "_graph_holder" in region
    assert "get_streaming_graph" not in region


def test_chat_js_has_no_live_voice_paint_hooks() -> None:
    chat = (_REPO / "kazma-ui/kazma_ui/static/js/chat.js").read_text(encoding="utf-8")
    voice = (_REPO / "kazma-ui/kazma_ui/static/js/voice.js").read_text(encoding="utf-8")
    for hook in ("onUserTranscription", "onStreamToken", "onStreamDone"):
        assert f"{hook}: function" not in chat, f"chat.js still authors voice turns via {hook}"
        assert f"KazmaChat.{hook}" not in voice, f"voice.js still dual-paints via {hook}"


def test_live_voice_mints_the_user_row_not_the_assistant() -> None:
    """The voice socket authors the USER line (like Send); the journal stays
    the only author of the assistant. Without the user row the next turn's
    tokens latch onto the previous assistant bubble."""
    chat = (_REPO / "kazma-ui/kazma_ui/static/js/chat.js").read_text(encoding="utf-8")
    voice = (_REPO / "kazma-ui/kazma_ui/static/js/voice.js").read_text(encoding="utf-8")

    # chat.js exports beginVoiceTurn, defined with the user-row contract:
    # user bubble → fresh assistant latch → a NEW turn, not a resume.
    assert "function beginVoiceTurn(text)" in chat
    assert "beginVoiceTurn: beginVoiceTurn," in chat
    fn = chat[chat.find("function beginVoiceTurn(text)"):chat.find("window.KazmaChat = {")]
    assert "appendMessage('user'" in fn
    # The latch reset. `tokenAccum = ''` used to be half of it;
    # UNIFIED_TURN_BLOCK.md Phase 2 deleted that accumulator and made the
    # turn document the single answer authority (invariant U06), so the
    # text is reset by minting a new turn rather than by blanking a
    # string. What survives is the DOM half plus the paint latch.
    chat_code = "\n".join(
        line for line in chat.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    assert "tokenAccum" not in chat_code, (
        "the token accumulator is back; the document is supposed to be "
        "the only answer authority (plan §5, invariant U06). Comments are "
        "excluded — the note explaining the removal names it."
    )
    assert "currentMsgEl = null" in fn, (
        "beginVoiceTurn no longer drops the previous assistant bubble; "
        "voice tokens latch onto the last reply"
    )
    assert "_turnPainted = false" in fn, (
        "beginVoiceTurn no longer clears the paint latch, so the new "
        "turn is treated as already painted"
    )
    # A new utterance is a new turn, never a resume. disableInput() is
    # beginTurn() (chat.js ~1977) — asserted by name so a refactor that
    # inlines it still has to say what it does.
    assert "beginTurn" in fn or "disableInput()" in fn
    # It must not submit a second graph turn — the server already did.
    assert "sendMessage" not in fn
    assert "/api/chat/stream" not in fn

    # voice.js invokes it from the transcribed branch (and still never
    # paints assistant tokens itself).
    transcribed = voice[voice.find("type === 'transcribed'"):voice.find("type === 'tool_call'")]
    assert "beginVoiceTurn" in transcribed
    assert "onStreamToken" not in voice
    assert "tokenAccum" not in voice


# ── PR C: sentence-splitting + streaming TTS ────────────────────────────


def test_split_sentences_en_arabic_newlines() -> None:
    from kazma_core.voice.tts import split_sentences

    parts = split_sentences("First one. Second two? Third ثلاثة؟\n- bullet item")
    assert parts == ["First one.", "Second two?", "Third ثلاثة؟", "- bullet item"]


@pytest.mark.asyncio
async def test_synthesize_stream_yields_edgetts_chunks_as_produced(monkeypatch) -> None:
    from kazma_core.voice.tts import synthesize_stream

    class _FakeCommunicate:
        def __init__(self, text: str, voice: str) -> None:
            pass

        async def stream(self):
            yield {"type": "audio", "data": b"chunk0"}
            yield {"type": "not-audio", "data": b"ignore"}
            yield {"type": "audio", "data": b"chunk1"}

    monkeypatch.setitem(
        sys.modules, "edge_tts", types.SimpleNamespace(Communicate=_FakeCommunicate)
    )
    chunks = [c async for c in synthesize_stream("hi", provider="edgetts")]
    assert chunks == [b"chunk0", b"chunk1"]


@pytest.mark.asyncio
async def test_synthesize_stream_buffers_non_streaming_provider(monkeypatch) -> None:
    from kazma_core.voice import tts as tts_mod

    async def fake_synthesize(text, *, provider="edgetts", voice="default",
                              api_key=None, output_format="mp3"):
        return b"full-clip"

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    chunks = [
        c
        async for c in tts_mod.synthesize_stream("hi", provider="openai")
    ]
    assert chunks == [b"full-clip"]


@pytest.mark.asyncio
async def test_speak_reply_sentences_are_independent_complete_clips(monkeypatch) -> None:
    """First tts_chunk carries ONLY sentence 1 audio — no waiting for the
    full reply, no fake chunking of a completed buffer."""
    from kazma_ui import routes_voice_ws as rws

    order: list[str] = []

    async def fake_stream(text, *, provider="edgetts", voice="default", **_kw):
        order.append(text)
        yield b"audio:" + text.encode()

    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)

    ws = FakeWebSocket()
    await rws._speak_reply(
        ws, "One sentence. Two sentence.", "edgetts",
        cancel=asyncio.Event(), segment_closed_at=time.monotonic(),
    )
    chunks = ws.of("tts_chunk")
    assert [c["seq"] for c in chunks] == [1, 2]
    import base64

    assert base64.b64decode(chunks[0]["data"]) == b"audio:One sentence."
    assert base64.b64decode(chunks[1]["data"]) == b"audio:Two sentence."
    assert order == ["One sentence.", "Two sentence."]
    assert ws.of("tts_done")
    # No fake chunker: one clip per sentence, not sliced bytes with sleeps.
    assert "asyncio.sleep" not in (
        _REPO / "kazma-ui/kazma_ui/routes_voice_ws.py"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_speak_reply_barge_in_stops_mid_stream(monkeypatch) -> None:
    from kazma_ui import routes_voice_ws as rws

    cancel = asyncio.Event()

    async def fake_stream(text, *, provider="edgetts", voice="default", **_kw):
        if text.startswith("Second"):
            cancel.set()  # user barged in between sentences
        yield b"audio:" + text.encode()

    monkeypatch.setattr("kazma_core.voice.tts.synthesize_stream", fake_stream)

    ws = FakeWebSocket()
    await rws._speak_reply(
        ws, "First one. Second one.", "edgetts",
        cancel=cancel, segment_closed_at=time.monotonic(),
    )
    chunks = ws.of("tts_chunk")
    assert len(chunks) == 1  # first sentence only
    assert not ws.of("tts_done")  # cancelled: no terminal tts_done


# ── PR A: WS sample-rate allowlist (source + behavior via handler state) ─


def test_ws_module_guards_sample_rate() -> None:
    from kazma_ui import routes_voice_ws as rws

    assert rws._ALLOWED_SAMPLE_RATES == frozenset({8000, 16000, 24000, 48000})
