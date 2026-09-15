---
id: voice-and-media
title: Voice & Media
sidebar_label: Voice & Media
description: Send voice notes, photos, and documents to Kazma on any platform — STT/TTS and multimodal attachments.
---

Kazma can **hear** (speech-to-text), **speak** (text-to-speech), and **see**
(images/PDFs/docs) across every chat platform — Telegram, Discord, Slack, and
the Web UI. This page covers how to enable and use voice and media.

---

## Voice (STT + TTS)

Kazma voice is **turn-based by default** (STT → LangGraph → TTS) on every
platform. On the **Web UI**, the Live button opens `/ws/voice`: the same
checkpointed supervisor graph and the **same chat thread/journal** as typed
messages — live voice is a mouth, not a second brain. Speak; you can
interrupt while it speaks.

Energy VAD is the implementation. `KAZMA_SILERO_VAD=1` selects a better
frame classifier when one is actually available: WebRTC VAD if
`pip install webrtcvad` (8/16/32/48 kHz). Silero requires a vendored model
Kazma does not ship yet — until it does, the flag does nothing beyond that
and Energy VAD runs (logged once). No runtime model downloads.

### Live voice on the web (`/ws/voice`)

- **One brain, one thread.** Your utterance is transcribed, submitted to
  the same graph `/api/chat/stream` uses, and journaled in the same
  conversation. The voice socket authors the user line (like Send does for
  typed text); the journal authors the assistant — the chat UI paints the
  turn from it.
- **TTS speaks the final reply only**, sentence by sentence (each clip is a
  complete MP3, so playback starts with the first sentence). Supervisor
  planning, tool chatter, and failed turns (⚠️ notices) are never spoken.
- **Danger tools pause for approval** like typed chat: you get the normal
  approval card in the chat UI (`POST /api/approve/{thread_id}` resumes);
  voice replies "Approval needed" and keeps listening. If the socket is
  still open when the approved turn finishes, the reply is spoken.
- **Barge-in**: speaking again stops playback and starts a new turn (a new
  utterance is a new user message — it supersedes the in-flight one, same
  rule as typed chat).
- **Hold the mic** (the round button) to dictate into the composer; a
  click is ignored. Live mode stays listening through silence — a
  mic-open pop is not "Transcription failed".
- The socket authenticates like every other WS and resolves your chat
  session server-side; an unknown session or a platform (`gw-*`) thread is
  refused.

### LiveKit duplex (web only, optional transport upgrade)

You need a LiveKit server (self-host or [LiveKit Cloud](https://livekit.io)).
Kazma does not start one.

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxx
LIVEKIT_API_SECRET=...
# Kill-switch: KAZMA_VOICE_DUPLEX=0
```

Or ConfigStore `voice.livekit.url` / `api_key` / `api_secret`. Then the Live
button on `/` joins a room (`POST /api/voice/livekit/token`) and barge-in
cancels TTS when you speak. When duplex is on, the browser also **publishes
TTS into the room** (`publishTrack`) so the media loop is honest (`tts_in_room`
on `/api/voice/livekit/status`). LiveKit is WebRTC transport + browser echo
cancellation — **not** a server media plane and not a second brain; the
LangGraph `/ws/voice` loop still owns the conversation. Telegram / Discord /
Slack are still voice notes.

OpenAI Realtime and Gemini Live are **not** the conversation brain. Optional
REST STT/TTS codec: `KAZMA_REALTIME_CODEC=1` (`kazma_core.voice.realtime_codec`).
Those two providers are skipped because they cannot stay codec-only without
owning the tool loop.

Voice is a **single config block that controls all platforms**. When enabled,
inbound audio is transcribed to text before reaching the agent. Optional
**auto voice-note replies** (`tts_reply`) synthesize the agent's reply back to
audio **only when that inbound turn was voice** (not for plain text chats).

### Enable voice

In `kazma.yaml` under `gateway`, or at runtime via the **Web UI → Settings →
Voice** tab (writes to ConfigStore, takes effect immediately):

```yaml
gateway:
  voice:
    enabled: true
    tts_reply: true           # platform auto voice-note replies (toggle in UI)
    stt_provider: openai      # speech-to-text provider
    stt_language: auto        # auto-detect; or "ar", "en", ...
    tts_provider: edgetts     # text-to-speech provider
    tts_voice: default
    tts_output_format: mp3
```

| Setting | Effect |
|---|---|
| **Voice subsystem** (`enabled`) | Master on/off for STT + TTS everywhere |
| **Auto voice-note replies** (`tts_reply`) | Telegram/Discord/Slack: speak the reply after a voice inbound. Off = text-only replies; STT still works |

The same keys are read live by all adapters (`voice_helpers.py`), so changing
a setting in the UI affects Telegram, Discord, Slack, and Web at once.

### STT (speech-to-text) providers

| Provider | Key | Needs | Notes |
|---|---|---|---|
| OpenAI Whisper | `openai` | `OPENAI_API_KEY` | Default; robust across languages. |
| Groq Whisper | `groq` | `GROQ_API_KEY` | Fastest; great for real-time. |
| Cohere | `cohere` | `COHERE_API_KEY` | |
| NVIDIA NIM / Riva | `nvidia` | `NVIDIA_API_KEY` | |
| faster-whisper (local) | `faster-whisper` | `pip install faster-whisper` | Runs on-device; no API key. |

### TTS (text-to-speech) providers

| Provider | Key | Needs | Notes |
|---|---|---|---|
| Edge TTS | `edgetts` | nothing | **Free, no key** — the default. |
| OpenAI | `openai` | `OPENAI_API_KEY` | High-quality neural voices. |
| NVIDIA NIM | `nvidia` | `NVIDIA_API_KEY` | |
| Kokoro (local) | `kokoro` | local install | On-device. |
| Coqui (local) | `coqui` | local install | On-device. |

### Per-platform behavior

| Platform | Inbound (you → agent) | Outbound (agent → you) |
|---|---|---|
| Telegram | Voice/audio note transcribed → text | TTS voice reply **only if** `tts_reply` and this turn was voice |
| Discord | Audio attachment transcribed → text | Same gate; audio file upload |
| Slack | Audio file transcribed → text | Same gate; file upload |
| Web UI | `POST /api/voice/stt` | Explicit `POST /api/voice/tts` / live `/ws/voice` (not gated by platform `tts_reply`) |

> If STT is not configured (no key / disabled), an inbound voice note returns
> a friendly fallback message instead of failing silently.

---

## Media & attachments

Kazma's message contract carries an `attachments` list (`Attachment`
dataclass in `gateway.py`) alongside text. Each attachment has a `kind`
(`image` / `file` / `audio` / `video`), `mime`, `filename`, and either
in-memory `data` bytes or a fetchable `url`.

### How the agent receives media

When you send an image or document, the attachment builder
(`agent_handler/attachments.py`) decides how to present it to the LLM:

| Attachment type | Behavior |
|---|---|
| **Image** (PNG/JPEG/WEBP/GIF, ≤ 8 MB, vision-capable model) | Inlined as a base64 `image_url` vision block — the LLM sees it directly. Text-only models get a persist-and-stub path instead of `image_url` (avoids provider 400s). |
| **Document** (PDF/DOCX/XLSX/PPTX/CSV/… when a document parser capability is available) | Saved under `kazma-data/attachments/`. The platform **auto-parses a bounded excerpt** via `DocumentService.read_transient_sync`, wraps it in an untrusted fence (`source="document_attachment"`), and adds a pointer for full content (prefer durable `document_read` / platform tools when the file is also ingested). |
| **Other / unparsable / over-cap media** | Persisted and represented as a text stub (`file_read` or path hint) so prompt size stays bounded. |

**Limits (chat gateway attachment path):** 20 MiB per file, 10 files, 50 MiB
aggregate. The durable [Document Intelligence](./document-intelligence.md)
platform uses its own intake limits (50 MiB default per file).

The multimodal content follows the OpenAI vision format
(`content: [{type:image_url,...}, {type:text,...}]`) and passes through
`llm_provider.py` verbatim — any vision-capable model works for inlined images.

### Per-platform support

| Platform | Inbound media | Outbound media |
|---|---|---|
| Telegram | Photo / document / video / animation captured (was silently dropped) | `sendPhoto` / `sendDocument` / `sendVideo` / `sendAudio` |
| Discord | Attachments + image embeds | Multipart file upload |
| Slack | `files` (Socket Mode primary path) | `getUploadURLExternal` → upload → `completeUploadExternal` |
| Web UI | `POST /api/chat/upload` (multipart, 20 MB cap) | Download links |

### Sending media on the Web UI

1. Click the attachment (📎) button in the chat box and pick a file.
2. **Small text files** (≤ 1 MB, `.txt`/`.md`/`.py`/...) are inlined into the
   message client-side — no upload round-trip.
3. **Images, PDFs, and binary files** are uploaded via
   `POST /api/chat/upload` and attached to your next message.

The agent can also **produce** media to send back — e.g. `generate_image`
(multi-backend) writes to `kazma-data/images/` and the path flows out as an
attachment on supported platforms.

---

## Disabling

Voice and media are opt-in. Voice defaults to `enabled: false`. Media capture
is always on where the adapter supports it (it's just data on the message
contract); if you want to suppress outbound media, that's controlled per-tool
(the agent only sends attachments it explicitly creates).
