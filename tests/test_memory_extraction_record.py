"""``scripts/memory_extraction_record.py``: the model's replies for the memory
extraction benchmark (Memory audit M24; the benchmark audit's R2).

The recording asks the configured model the deep pass's own question for
every benchmark turn, several times (``benchmark.ASKINGS_STANDARD``: asked
again, a model answers a little differently), and writes the replies where
``--out`` says; the benchmark then replays every asking. A recording the
benchmark cannot replay, a run that writes somewhere it was not told to, an
``--out``-less run that falls back to the checkout it runs in, or a run that
asks again what it has or past its call limit would each break the owner's
live run (it runs inside the install, on the owner's model account).
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
    """Stands in for the configured model: one fact for every turn, the city
    numbered by the asking, and a record of what it was asked."""

    model = "stand-in-model"

    def __init__(self, fail_after: int | None = None) -> None:
        self.asked: list[str] = []
        self._fail_after = fail_after

    async def chat(self, messages, **_kw):
        if self._fail_after is not None and len(self.asked) >= self._fail_after:
            raise ConnectionError("the provider went away")
        self.asked.append(messages[-1]["content"])
        city = ["دبي", "Doha", "Kuwait", "Riyadh", "Muscat"][len(self.asked) % 5]
        return SimpleNamespace(content=json.dumps({"beliefs": [
            {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": city,
             "confidence": 0.9}]}, ensure_ascii=False),
            # The provider's own counts (DeepSeek's usage fields), no price.
            usage={"prompt_tokens": 1500, "completion_tokens": 150, "prompt_cache_hit_tokens": 1200,
                   "prompt_cache_miss_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 60}},
            cost_usd=0.0, cost_known=False)


def _use(monkeypatch, stand_in) -> None:
    registry = SimpleNamespace(get_client=lambda: stand_in, active_model="stand-in-model")
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: registry)


@pytest.fixture
def model(monkeypatch):
    stand_in = _Model()
    _use(monkeypatch, stand_in)
    return stand_in


def _dataset(n: int) -> dict:
    data = json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))
    return {"version": data["version"], "turns": data["turns"][:n]}


def _read(out: Path) -> dict:
    return json.loads(out.read_text(encoding="utf-8"))


def test_every_turn_is_asked_each_time_and_every_asking_replays(model, tmp_path):
    data = _dataset(4)
    out = tmp_path / "nested" / "extraction_llm.json"

    _script().record_to(out, dataset=data, limit=8, askings=2)

    recorded = _read(out)
    assert recorded["model"] == "stand-in-model" and recorded["complete"] is True
    assert recorded["extraction_version"] == data["version"] and recorded["askings"] == 2
    assert len(model.asked) == 8 and all(len(v) == 2 for v in recorded["replies"].values())
    # Asked as the product asks: the deep pass's own message for each turn.
    assert sorted(set(model.asked)) == sorted(benchmark._llm_blob(t["user"], **benchmark._turn_context(t))
                                              for t in data["turns"])
    report = benchmark.run_extraction_benchmark(data, llm_replies=recorded)
    assert report["llm_recording"]["askings"] == 2 and report["llm_spread"], report
    assert not list(out.parent.glob("*.tmp")), "the temporary file was left behind"


def test_the_limit_stops_it_and_a_second_run_goes_on_without_asking_again(model, tmp_path):
    out = tmp_path / "extraction_llm.json"
    _script().record_to(out, dataset=_dataset(4), limit=5, askings=2)
    first = _read(out)
    # Every turn's first asking before any turn's second.
    assert first["complete"] is False and len(model.asked) == 5
    assert sorted(len(v) for v in first["replies"].values()) == [1, 1, 1, 2]
    # A turn not asked yet has no entry: a file cut short holds only replies.
    assert all(first["replies"].values())
    _script().record_to(out, dataset=_dataset(4), limit=100, askings=2)
    assert len(model.asked) == 8 and _read(out)["complete"] is True
    assert len(set(model.asked)) == 4                                   # nothing asked a third time
    # What the calls cost, as the provider counted them, over both runs.
    usage = _read(out)["usage"]
    assert usage["calls"] == 8 and usage["prompt_tokens"] == 8 * 1500 and usage["completion_tokens"] == 8 * 150
    assert usage["reasoning_tokens"] == 8 * 60 and usage["prompt_cache_hit_tokens"] == 8 * 1200
    assert usage["cost_known"] is False                                 # no price: unknown, never free


def test_a_failure_keeps_what_was_recorded(monkeypatch, tmp_path):
    _use(monkeypatch, _Model(fail_after=3))
    out = tmp_path / "extraction_llm.json"
    with pytest.raises(RuntimeError, match="did not answer"):
        _script().record_to(out, dataset=_dataset(4), limit=8, askings=2)
    assert sum(len(v) for v in _read(out)["replies"].values()) == 3


def test_a_file_of_another_prompt_is_not_continued(model, tmp_path, monkeypatch):
    out = tmp_path / "extraction_llm.json"
    _script().record_to(out, dataset=_dataset(2), limit=2, askings=1)
    from kazma_core.memory import belief_extractor

    monkeypatch.setattr(belief_extractor, "_EXTRACT_SYSTEM", belief_extractor._EXTRACT_SYSTEM + "\nChanged.")
    with pytest.raises(benchmark.StaleRecordingError, match="new file"):
        _script().record_to(out, dataset=_dataset(2), limit=2, askings=2)


def test_a_recording_missing_a_turn_is_refused_not_guessed(model, tmp_path):
    """The negative control: a recording of fewer turns than the benchmark
    asks is an error naming this script, never a pass scored as an outage."""
    out = tmp_path / "extraction_llm.json"
    _script().record_to(out, dataset=_dataset(2), limit=2, askings=1)
    with pytest.raises(KeyError, match="memory_extraction_record"):
        benchmark.run_extraction_benchmark(_dataset(3), llm_replies=_read(out))


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
    _script().record_to(out, dataset=_dataset(1), limit=1, askings=1)
    assert _read(out)["model"] == "deepseek-flash"


@pytest.mark.parametrize("argv", [[], ["--out", "x.json"]])
def test_out_and_limit_are_required(model, monkeypatch, capsys, argv):
    """Run inside an install, a default output would write into the
    install's own checkout, and no limit could spend the owner's account."""
    loaded = []
    monkeypatch.setattr("kazma_core.env_files.load_env_files", lambda *a, **k: loaded.append(1))
    with pytest.raises(SystemExit) as exc:
        _script().main(argv)
    assert exc.value.code == 2
    assert "--out" in capsys.readouterr().err or argv
    assert model.asked == [] and loaded == []
