"""Local voice providers load and run their models off the event loop, once.

Audit 2026-10-26 AUD-036: the faster-whisper, Kokoro and Coqui providers
imported, loaded and ran their models synchronously inside ``async def`` --
a model load froze the whole server (every chat, every page) for its
duration, and faster-whisper loaded its model again on every message. Coqui
also imported ``TTS.api.TTS`` as a module, which it is not (a class in
``TTS.api``), so the provider always answered "not installed".

The packages are not installed here (nor in CI), so they are stubbed in
``sys.modules``; the stubs record the thread each call ran on. The calls the
providers make on the models (``WhisperModel(...)``, ``kokoro.create``,
``TTS().tts``) are the providers' own and are not verified here.
Negative control: the old Coqui import fails on the very package layout it
targets, and a call made inline runs on the loop's thread.
"""

from __future__ import annotations

import asyncio
import threading
import types

import pytest
from kazma_core.voice import stt as stt_mod
from kazma_core.voice import tts as tts_mod

from tests._module_stubs import stub_modules


def _soundfile() -> types.ModuleType:
    module = types.ModuleType("soundfile")

    def write(buf, data, sr, format=None):  # noqa: A002 - soundfile's own name
        buf.write(b"RIFF" + bytes(data))

    module.write = write
    return module


async def _on_loop_thread(coro):
    loop_thread = threading.get_ident()
    return loop_thread, await coro


def test_faster_whisper_loads_once_and_runs_in_a_thread(monkeypatch):
    threads: dict[str, list[int]] = {"load": [], "transcribe": []}

    class WhisperModel:
        def __init__(self, size, **kw):
            threads["load"].append(threading.get_ident())

        def transcribe(self, path, language=None):
            threads["transcribe"].append(threading.get_ident())
            info = types.SimpleNamespace(language="en", duration=1.0)
            return iter([types.SimpleNamespace(text="hello there")]), info

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setattr(stt_mod, "_whisper_models", {})
    transcribe = stt_mod._faster_whisper_stt()

    async def twice():
        first = await transcribe(b"ogg-bytes")
        second = await transcribe(b"ogg-bytes")
        return first, second

    with stub_modules({"faster_whisper": module}):
        loop_thread, (first, second) = asyncio.run(_on_loop_thread(twice()))

    assert first == second == "hello there"
    assert len(threads["load"]) == 1, "the model was loaded again for a second message"
    assert loop_thread not in threads["load"] + threads["transcribe"]


def test_faster_whisper_not_installed_says_so(monkeypatch):
    transcribe = stt_mod._faster_whisper_stt()

    async def run():
        return await transcribe(b"ogg"), stt_mod._last_error.get()

    with stub_modules({"faster_whisper": None}):
        text, error = asyncio.run(run())
    assert text is None
    assert "not installed" in (error or "")


def _coqui_package(record: list[int], loads: list[int]):
    package = types.ModuleType("TTS")
    package.__path__ = []  # a package, as Coqui's is
    api = types.ModuleType("TTS.api")

    class TTS:
        def __init__(self, *a, **kw):
            loads.append(threading.get_ident())

        def tts(self, text):
            record.append(threading.get_ident())
            return [1, 2, 3]

    api.TTS = TTS
    package.api = api
    return {"TTS": package, "TTS.api": api, "soundfile": _soundfile()}


def test_coqui_imports_its_class_and_runs_in_a_thread(monkeypatch):
    record: list[int] = []
    loads: list[int] = []
    monkeypatch.setattr(tts_mod, "_coqui_models", {})
    synthesize = tts_mod._coqui_provider()

    async def twice():
        return await synthesize("hello"), await synthesize("again")

    with stub_modules(_coqui_package(record, loads)):
        loop_thread, (first, second) = asyncio.run(_on_loop_thread(twice()))

    assert first and first.startswith(b"RIFF") and second
    assert len(loads) == 1
    assert loop_thread not in loads + record


def test_kokoro_synthesizes_in_a_thread():
    record: list[int] = []
    module = types.ModuleType("kokoro")

    def create(text, voice=None):
        record.append(threading.get_ident())
        return [4, 5], 24000

    module.create = create
    synthesize = tts_mod._kokoro_provider()
    with stub_modules({"kokoro": module, "soundfile": _soundfile()}):
        loop_thread, audio = asyncio.run(_on_loop_thread(synthesize("hi")))
    assert audio and audio.startswith(b"RIFF")
    assert record and loop_thread not in record


@pytest.mark.parametrize("provider", ["_kokoro_provider", "_coqui_provider"])
def test_a_missing_package_is_a_configuration_error(provider):
    synthesize = getattr(tts_mod, provider)()
    with stub_modules({"kokoro": None, "TTS": None, "TTS.api": None, "soundfile": None}):
        with pytest.raises(tts_mod.TTSError) as caught:
            asyncio.run(synthesize("hi"))
    assert caught.value.is_config is True
    assert "not installed" in str(caught.value)


def test_negative_control_the_old_coqui_import_fails_on_the_real_layout():
    with stub_modules(_coqui_package([], [])):
        with pytest.raises(ImportError):
            exec("import TTS.api.TTS as coqui_tts", {})  # noqa: S102 - the old import line
