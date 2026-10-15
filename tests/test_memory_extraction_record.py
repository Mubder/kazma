"""``scripts/memory_extraction_record.py``: the model's replies for the memory
extraction benchmark (Memory audit M24).

The recording asks the configured model once per benchmark turn and writes
the replies where ``--out`` says; the benchmark then replays them. A
recording the benchmark cannot replay, a run that writes somewhere it was
not told to, or an ``--out``-less run that falls back to the checkout it runs
in would each break the owner's live run (it runs inside the install).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import benchmark

REPO = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "_memory_extraction_record", REPO / "scripts" / "memory_extraction_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Model:
    """Stands in for the configured model: one fact for every turn, and a
    record of what it was asked."""

    model = "stand-in-model"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def chat(self, messages, **_kw):
        self.asked.append(messages[-1]["content"])
        return SimpleNamespace(content=json.dumps({"beliefs": [
            {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "دبي"}]},
            ensure_ascii=False))


@pytest.fixture
def model(monkeypatch):
    stand_in = _Model()
    registry = SimpleNamespace(get_client=lambda: stand_in, active_model="stand-in-model")
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    return stand_in


def _dataset(n: int) -> dict:
    data = json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))
    return {"version": data["version"], "turns": data["turns"][:n]}


def test_the_recording_replays_through_the_benchmark(model, tmp_path):
    data = _dataset(4)
    out = tmp_path / "nested" / "extraction_llm.json"

    _script().record_to(out, data)

    recorded = json.loads(out.read_text(encoding="utf-8"))
    assert recorded["model"] == "stand-in-model"
    assert recorded["extraction_version"] == data["version"]
    assert len(model.asked) == 4 and len(recorded["replies"]) == 4
    # Every turn is answered from the recording: none is reported missing.
    report = benchmark.run_extraction_benchmark(data, llm_replies=recorded)
    assert report["llm"], report
    assert not list(out.parent.glob("*.tmp")), "the temporary file was left behind"


def test_a_recording_missing_a_turn_is_refused_not_guessed(model, tmp_path):
    """The negative control: a recording of fewer turns than the benchmark
    asks is an error naming this script, never a pass scored as an outage."""
    out = tmp_path / "extraction_llm.json"
    _script().record_to(out, _dataset(2))
    recorded = json.loads(out.read_text(encoding="utf-8"))
    with pytest.raises(KeyError, match="memory_extraction_record"):
        benchmark.run_extraction_benchmark(_dataset(3), llm_replies=recorded)


def test_the_recording_names_the_model_of_a_provider_client(monkeypatch, tmp_path):
    """A provider client keeps its model on ``config`` (``LLMProvider``) and
    has no ``.model``: the first live recording (2026-10-15) said "unknown"."""
    class _Provider(_Model):
        model = None

        def __init__(self) -> None:
            super().__init__()
            self.config = SimpleNamespace(model="deepseek-flash")

    stand_in = _Provider()
    registry = SimpleNamespace(get_client=lambda: stand_in, active_model=None)
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)
    out = tmp_path / "extraction_llm.json"
    _script().record_to(out, _dataset(1))
    assert json.loads(out.read_text(encoding="utf-8"))["model"] == "deepseek-flash"


def test_out_is_required(model, monkeypatch, capsys):
    """Run inside an install, a default output would write into the
    install's own checkout."""
    loaded = []
    monkeypatch.setattr("kazma_core.env_files.load_env_files", lambda *a, **k: loaded.append(1))
    with pytest.raises(SystemExit) as exc:
        _script().main([])
    assert exc.value.code == 2
    assert "--out" in capsys.readouterr().err
    assert model.asked == [] and loaded == []
