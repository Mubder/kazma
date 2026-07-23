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

Voice is a **single config block that controls all platforms**. When enabled,
inbound audio is transcribed to text before reaching the agent, and the
agent's reply is synthesized back to audio when the inbound turn was voice.

### Enable voice

In `kazma.yaml` under `gateway`, or at runtime via the **Web UI → Settings →
Voice** tab (writes to ConfigStore, takes effect immediately):

```yaml
gateway:
  voice:
    enabled: true
    stt_provider: openai      # speech-to-text provider
    stt_language: auto        # auto-detect; or "ar", "en", ...
    tts_provider: edgetts     # text-to-speech provider
    tts_voice: default
    tts_output_format: mp3
```

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
| Telegram | Voice/audio note transcribed → text | TTS voice reply when your turn was voice |
| Discord | Audio attachment transcribed → text | TTS voice reply (audio file upload) |
| Slack | Audio file transcribed → text | TTS voice reply (file upload) |
| Web UI | `POST /api/voice/stt` | `POST /api/voice/tts`, plus the real-time `/ws/voice` WebSocket for bidirectional streaming |

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
| **Image** (PNG/JPEG/WEBP/GIF, ≤ 8 MB) | Inlined as a base64 `image_url` vision block — the LLM sees it directly. |
| **Document** (PDF/DOCX/large image/audio/...) | Saved to `kazma-data/attachments/`; the prompt gets a `[Attached: foo.pdf — use file_read to open: <path>]` stub so the agent can open it via the file tools. This keeps prompt size bounded. |

The multimodal content follows the OpenAI vision format
(`content: [{type:image_url,...}, {type:text,...}]`) and passes through
`llm_provider.py` verbatim — any vision-capable model works.

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
