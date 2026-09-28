"""Speech is labelled by what the bytes are, not by what Settings asked for.

On the live install (2026-09-28) ``voice.tts_output_format`` was ``opus`` and
the voice was edge-tts, which always returns MP3. The read-aloud route
answered MP3 as ``audio/opus`` and Telegram uploaded it as ``reply.ogg`` /
``audio/ogg``; Discord and Slack named it ``reply.opus``. Chrome sniffed and
played it; a stricter player is entitled not to. Every sender now labels
through ``kazma_core.voice.audio_format.audio_label``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kazma_core.voice.audio_format import audio_label, _sniff_audio

REPO = Path(__file__).resolve().parents[1]
# The first bytes edge-tts returned on the live install: an MPEG-2 layer III frame.
MP3_FRAME = bytes([0xFF, 0xF3, 0x64, 0xC4]) + b"\x00" * 60


@pytest.mark.parametrize(
    ("head", "want"),
    [
        (MP3_FRAME, ("mp3", "audio/mpeg")),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 8, ("mp3", "audio/mpeg")),
        (b"OggS\x00\x02" + b"\x00" * 10, ("ogg", "audio/ogg")),
        (b"RIFF\x24\x08\x00\x00WAVEfmt ", ("wav", "audio/wav")),
        (b"fLaC\x00\x00\x00\x22" + b"\x00" * 8, ("flac", "audio/flac")),
        (bytes([0xFF, 0xF1, 0x50, 0x80]) + b"\x00" * 8, ("aac", "audio/aac")),
        (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4, ("m4a", "audio/mp4")),
        (b"\x1a\x45\xdf\xa3" + b"\x00" * 8, ("webm", "audio/webm")),
    ],
)
def test_the_container_is_read_from_the_bytes(head: bytes, want: tuple[str, str]) -> None:
    assert _sniff_audio(head) == want


def test_the_label_follows_the_bytes_not_the_setting() -> None:
    assert audio_label(MP3_FRAME, "opus") == ("mp3", "audio/mpeg")
    # Unrecognised bytes: what was asked for, with "opus" meaning an Ogg file.
    assert audio_label(b"\x00\x01\x02\x03", "opus") == ("ogg", "audio/ogg")
    assert audio_label(b"", "wav") == ("wav", "audio/wav")
    assert audio_label(None, "") == ("mp3", "audio/mpeg")


def test_every_voice_sender_labels_through_the_helper() -> None:
    """No sender builds a MIME type or file name from the format setting."""
    senders = [
        REPO / "kazma-ui" / "kazma_ui" / "routes_voice.py",
        REPO / "kazma-gateway" / "kazma_gateway" / "adapters" / "telegram.py",
        REPO / "kazma-gateway" / "kazma_gateway" / "adapters" / "discord_stt.py",
        REPO / "kazma-gateway" / "kazma_gateway" / "adapters" / "slack_stt.py",
    ]
    for path in senders:
        src = path.read_text(encoding="utf-8")
        assert "audio_label(" in src, path.name
        assert not re.search(r'f"audio/\{|"reply\.ogg"|"audio/opus"', src), path.name


def test_negative_control_the_old_labels_lied() -> None:
    """The old mapping, fed the live setting and edge-tts's bytes."""
    old_web = {"mp3": "audio/mpeg", "opus": "audio/opus"}.get("opus", "audio/mpeg")
    assert old_web != audio_label(MP3_FRAME, "opus")[1]


@pytest.mark.asyncio
async def test_the_read_aloud_route_answers_what_it_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import kazma_core.voice.tts as tts
    from kazma_ui import routes_voice

    class _Store:
        def get(self, key: str, default=None):
            return {"voice.tts_output_format": "opus"}.get(key, default)

    async def fake_synthesize(text, **kwargs):
        assert kwargs.get("output_format") == "opus", "the setting still reaches the provider"
        return MP3_FRAME

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    app = FastAPI()
    app.include_router(routes_voice.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/voice/tts", data={"text": "hello", "provider": "edgetts", "voice": "auto"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.content == MP3_FRAME
