"""Tests for Telegram voice message transcription (gw-050).

Covers:
    - detect_voice_message: positive/negative detection
    - download_voice_file: mocked getFile → download flow
    - transcribe_voice: STT available and unavailable
    - Full pipeline: voice update → download → transcribe → IncomingMessage
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_gateway.adapters.telegram import MAX_VOICE_BYTES, TelegramAdapter
from kazma_gateway.gateway import IncomingMessage

# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


def _make_voice_update(
    file_id: str = "AwACAgIAAxkBAAI",
    duration: int = 5,
    chat_id: int = 12345,
    user_id: int = 999,
) -> dict:
    """Build a minimal Telegram update with a voice message."""
    return {
        "update_id": 100,
        "message": {
            "message_id": 42,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "testuser", "first_name": "Test"},
            "voice": {
                "file_id": file_id,
                "file_unique_id": "AgADBQ",
                "duration": duration,
                "mime_type": "audio/ogg",
            },
        },
    }


def _make_text_update(chat_id: int = 12345, user_id: int = 999) -> dict:
    """Build a minimal Telegram update with a text message."""
    return {
        "update_id": 101,
        "message": {
            "message_id": 43,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "testuser", "first_name": "Test"},
            "text": "Hello world",
        },
    }


def _make_audio_update(
    file_id: str = "BQACAgIAAxkBAAI",
    chat_id: int = 12345,
    user_id: int = 999,
) -> dict:
    """Build a minimal Telegram update with an audio file."""
    return {
        "update_id": 102,
        "message": {
            "message_id": 44,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "testuser", "first_name": "Test"},
            "audio": {
                "file_id": file_id,
                "file_unique_id": "AgADBA",
                "duration": 120,
                "mime_type": "audio/mpeg",
            },
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. detect_voice_message — positive
# ══════════════════════════════════════════════════════════════════════════


class TestDetectVoiceMessage:
    """Tests for the static detect_voice_message method."""

    def test_detect_voice_message_positive(self) -> None:
        """Voice message is detected correctly."""
        adapter = TelegramAdapter(token="fake:token")
        msg = _make_voice_update()["message"]
        assert adapter.detect_voice_message(msg) is True

    def test_detect_voice_message_audio(self) -> None:
        """Audio file is also detected as voice."""
        adapter = TelegramAdapter(token="fake:token")
        msg = _make_audio_update()["message"]
        assert adapter.detect_voice_message(msg) is True

    def test_detect_voice_message_negative(self) -> None:
        """Text-only message is not detected as voice."""
        adapter = TelegramAdapter(token="fake:token")
        msg = _make_text_update()["message"]
        assert adapter.detect_voice_message(msg) is False

    def test_detect_voice_message_sticker(self) -> None:
        """Sticker message is not detected as voice."""
        adapter = TelegramAdapter(token="fake:token")
        msg = {"sticker": {"file_id": "abc"}}
        assert adapter.detect_voice_message(msg) is False

    def test_detect_voice_message_empty(self) -> None:
        """Empty message is not detected as voice."""
        adapter = TelegramAdapter(token="fake:token")
        assert adapter.detect_voice_message({}) is False


# ══════════════════════════════════════════════════════════════════════════
# 2. download_voice_file
# ══════════════════════════════════════════════════════════════════════════


class TestDownloadVoiceFile:
    """Tests for the download_voice_file method."""

    @pytest.mark.asyncio
    async def test_download_voice_file_success(self) -> None:
        """getFile + file download succeeds and returns bytes."""
        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        # Mock getFile response
        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg"},
        }

        # Mock file download response
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"\x4f\x67\x67\x53"  # fake OGG bytes
        download_resp.headers = {"content-length": str(len(b"\x4f\x67\x67\x53"))}

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        result = await adapter.download_voice_file("AgACAgIAAxkBAAI")
        assert result == b"\x4f\x67\x67\x53"
        assert adapter._http.get.call_count == 2
        assert adapter._http.get.await_args_list[0].args[0] == "/getFile"

    @pytest.mark.asyncio
    async def test_download_voice_file_getfile_fails(self) -> None:
        """Returns None when getFile API returns ok=false."""
        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        fail_resp = MagicMock()
        fail_resp.status_code = 200
        fail_resp.raise_for_status = MagicMock()
        fail_resp.json.return_value = {"ok": False, "description": "Not Found"}

        adapter._http.get = AsyncMock(return_value=fail_resp)

        result = await adapter.download_voice_file("bad_file_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_voice_file_http_error(self) -> None:
        """Returns None on HTTP status error."""
        import httpx

        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        error_resp = MagicMock()
        error_resp.status_code = 404
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=error_resp
        )

        adapter._http.get = AsyncMock(return_value=error_resp)

        result = await adapter.download_voice_file("file_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_voice_file_no_http_client(self) -> None:
        """Raises AssertionError when HTTP client not initialized."""
        adapter = TelegramAdapter(token="fake:token")
        # _http is None by default
        with pytest.raises(AssertionError, match="HTTP client not initialized"):
            await adapter.download_voice_file("file_id")


# ══════════════════════════════════════════════════════════════════════════
# 3. transcribe_voice — STT available / unavailable
# ══════════════════════════════════════════════════════════════════════════


class TestTranscribeVoice:
    """Tests for the transcribe_voice method."""

    @pytest.mark.asyncio
    async def test_transcribe_voice_stt_available(self) -> None:
        """Returns transcription text when STT provider succeeds."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="openai",
            stt_api_key="sk-test-key",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"text": "Hello, this is a test transcription"}

        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client

            result = await adapter.transcribe_voice(b"fake audio bytes")

        assert result == "Hello, this is a test transcription"

    @pytest.mark.asyncio
    async def test_transcribe_voice_stt_unavailable_no_key(self) -> None:
        """Returns None when no API key is configured."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="openai",
            stt_api_key=None,
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            result = await adapter.transcribe_voice(b"fake audio bytes")
        assert result is None

    @pytest.mark.asyncio
    async def test_transcribe_voice_stt_unknown_provider(self) -> None:
        """Returns None for unknown STT provider."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="unknown_provider",
        )
        result = await adapter.transcribe_voice(b"fake audio bytes")
        assert result is None

    @pytest.mark.asyncio
    async def test_transcribe_voice_groq_available(self) -> None:
        """Returns transcription via Groq provider."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="groq",
            stt_api_key="gsk-test-key",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"text": "Groq transcription result"}

        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client

            result = await adapter.transcribe_voice(b"fake audio bytes")

        assert result == "Groq transcription result"


# ══════════════════════════════════════════════════════════════════════════
# 4. Full voice pipeline — _handle_voice_message
# ══════════════════════════════════════════════════════════════════════════


class TestVoicePipeline:
    """Integration tests for the full voice → text pipeline."""

    @pytest.mark.asyncio
    async def test_handle_voice_message_success(self) -> None:
        """Full pipeline: download + transcribe returns text."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="openai",
            stt_api_key="sk-test-key",
        )
        adapter._http = AsyncMock()

        # Mock getFile + download
        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"fake ogg bytes"
        download_resp.headers = {"content-length": str(len(b"fake ogg bytes"))}

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        # Mock STT
        mock_stt_resp = MagicMock()
        mock_stt_resp.status_code = 200
        mock_stt_resp.raise_for_status = MagicMock()
        mock_stt_resp.json.return_value = {"text": "Salam ya habibi"}

        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_stt_resp)
            MockClient.return_value = mock_client

            msg = _make_voice_update()["message"]
            result = await adapter._handle_voice_message(msg)

        assert result == "Salam ya habibi"

    @pytest.mark.asyncio
    async def test_handle_voice_message_stt_unavailable_sends_fallback(self) -> None:
        """When STT fails, sends fallback reply to user and returns None."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="openai",
            stt_api_key="sk-test-key",
        )
        adapter._http = AsyncMock()

        # Mock getFile + download
        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"fake ogg bytes"
        download_resp.headers = {"content-length": str(len(b"fake ogg bytes"))}

        # sendMessage response (for fallback message)
        send_resp = MagicMock()
        send_resp.status_code = 200

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])
        adapter._http.post = AsyncMock(return_value=send_resp)

        # Mock STT to return None (failure)
        with patch.object(adapter, "transcribe_voice", new_callable=AsyncMock, return_value=None):
            msg = _make_voice_update()["message"]
            result = await adapter._handle_voice_message(msg)

        assert result is None
        # Verify fallback message was sent
        adapter._http.post.assert_called_once()
        assert adapter._http.post.await_args.args[0] == "/sendMessage"
        payload = adapter._http.post.await_args.kwargs["json"]
        assert payload["chat_id"] == 12345
        assert "Voice received but transcription is unavailable" in payload["text"]

    @pytest.mark.asyncio
    async def test_voice_to_text_pipeline_full(self) -> None:
        """Full end-to-end: voice update → parse → IncomingMessage with transcription."""
        adapter = TelegramAdapter(
            token="fake:token",
            voice_enabled=True,
            voice_provider="openai",
            stt_api_key="sk-test-key",
        )
        adapter._http = AsyncMock()

        # Mock getFile + download
        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"fake ogg bytes"
        download_resp.headers = {"content-length": str(len(b"fake ogg bytes"))}

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        # Mock STT
        mock_stt_resp = MagicMock()
        mock_stt_resp.status_code = 200
        mock_stt_resp.raise_for_status = MagicMock()
        mock_stt_resp.json.return_value = {"text": "Voice transcription works!"}

        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_stt_resp)
            MockClient.return_value = mock_client

            # Simulate what listen() does for a voice update
            update = _make_voice_update()
            message = update["message"]

            # 1. _parse_update should return None (no text)
            msg = adapter._parse_update(update)
            assert msg is None

            # 2. detect_voice_message should be True
            assert adapter.detect_voice_message(message) is True

            # 3. Full pipeline returns transcription
            transcription = await adapter._handle_voice_message(message)
            assert transcription == "Voice transcription works!"

            # 4. Build IncomingMessage from transcription (as listen() would)
            from_user = message.get("from", {})
            user_id = from_user.get("id", 0)
            chat_id = message.get("chat", {}).get("id", 0)
            sender_id = f"telegram:{user_id}"

            incoming = IncomingMessage(
                platform="telegram",
                sender_id=sender_id,
                text=transcription,
                context_metadata={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "voice_transcribed": True,
                },
            )

            assert incoming.platform == "telegram"
            assert incoming.text == "Voice transcription works!"
            assert incoming.sender_id == "telegram:999"
            assert incoming.context_metadata["voice_transcribed"] is True
            assert incoming.context_metadata["chat_id"] == 12345

# ══════════════════════════════════════════════════════════════════════════
# 5. Voice file size cap (gw-064 BUG 1)
# ══════════════════════════════════════════════════════════════════════════


class TestVoiceSizeCap:
    """Tests for MAX_VOICE_BYTES enforcement in download_voice_file."""

    @pytest.mark.asyncio
    async def test_download_voice_file_content_length_too_large(self) -> None:
        """Returns None when Content-Length exceeds MAX_VOICE_BYTES."""
        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/huge.ogg"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"x" * 100  # small actual content
        download_resp.headers = {"content-length": str(MAX_VOICE_BYTES + 1)}

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        result = await adapter.download_voice_file("huge_file_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_voice_file_actual_content_too_large(self) -> None:
        """Returns None when downloaded bytes exceed limit (no Content-Length header)."""
        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/stealth.ogg"},
        }

        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = b"x" * 100
        download_resp.headers = {}  # no Content-Length header

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        # Patch MAX_VOICE_BYTES to be tiny so 100 bytes triggers the cap
        with patch("kazma_gateway.adapters.telegram.MAX_VOICE_BYTES", 50):
            result = await adapter.download_voice_file("stealth_file_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_download_voice_file_at_limit_succeeds(self) -> None:
        """Returns content when file is exactly at (not over) the limit."""
        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        get_file_resp = MagicMock()
        get_file_resp.status_code = 200
        get_file_resp.raise_for_status = MagicMock()
        get_file_resp.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/exact.ogg"},
        }

        content = b"OggS" * 4  # 16 bytes
        download_resp = MagicMock()
        download_resp.status_code = 200
        download_resp.raise_for_status = MagicMock()
        download_resp.content = content
        download_resp.headers = {"content-length": str(len(content))}

        adapter._http.get = AsyncMock(side_effect=[get_file_resp, download_resp])

        with patch("kazma_gateway.adapters.telegram.MAX_VOICE_BYTES", 16):
            result = await adapter.download_voice_file("exact_file_id")

        assert result == content
