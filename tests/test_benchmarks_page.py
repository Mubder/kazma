"""The public benchmarks page (REMAINING 4.8) is written from the committed
results, never by hand: a lock that changes a score changes the page in the
same commit (``scripts/benchmarks_page.py --check``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _page_module():
    spec = importlib.util.spec_from_file_location("benchmarks_page", ROOT / "scripts" / "benchmarks_page.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_page_says_what_the_results_say() -> None:
    assert _page_module().main(["--check"]) == 0, "run scripts/benchmarks_page.py"


def test_a_changed_score_makes_the_page_stale(tmp_path, monkeypatch) -> None:
    """The negative control: a score changed in the result file is caught."""
    import json

    module = _page_module()
    data = json.loads(module.MEMORY.read_text(encoding="utf-8"))
    data["scores"]["overall.hit_rate"] = 0.1234
    changed = tmp_path / "thresholds.json"
    changed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "MEMORY", changed)
    assert module.main(["--check"]) == 1
    assert "12.3 %" in module.render()


def test_every_number_comes_from_a_result_file() -> None:
    text = (ROOT / "docs" / "docs" / "reference" / "benchmarks.md").read_text(encoding="utf-8")
    for source in ("scripts/memory_bench.py", "scripts/knowledge_bench.py", "scripts/document_fidelity.py"):
        assert source.split("/")[-1].removesuffix(".py") in text
    assert "second set" in text.lower() and "Arabic" in text


def test_each_model_pass_row_says_how_many_askings_it_rests_on() -> None:
    """The owner's decision 6 (benchmark audit R12), from the lock."""
    text = (ROOT / "docs" / "docs" / "reference" / "benchmarks.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| Model pass")]
    assert len(rows) == 3
    for row in rows:
        assert row.rstrip(" |").split("|")[-1].strip().split(" ")[1] in ("asking", "askings"), row


def test_a_model_pass_row_the_lock_does_not_count_is_not_printed(tmp_path, monkeypatch) -> None:
    """Negative control: a lock without the count refuses the page, and a
    changed count changes it."""
    import json

    import pytest

    module = _page_module()
    data = json.loads(module.MEMORY.read_text(encoding="utf-8"))
    changed = tmp_path / "thresholds.json"
    turns = data["extraction"]["rests_on"]["llm"]["ar"]["turns"]
    data["extraction"]["rests_on"]["llm"]["ar"]["askings"] = turns * 7
    changed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "MEMORY", changed)
    assert f"{turns * 7} askings ({turns} turns, 7 each)" in module.render()
    assert module.main(["--check"]) == 1
    del data["extraction"]["rests_on"]
    changed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="rests on"):
        module.render()


def test_the_model_pass_says_its_spread_and_what_c2_asks(tmp_path, monkeypatch) -> None:
    """A recording of several askings prints its spread for the table's
    scores and the part C2 scores, from the lock; one asking keeps the old
    sentence and prints no spread."""
    import json

    module = _page_module()
    data = json.loads(module.MEMORY.read_text(encoding="utf-8"))
    data["extraction"]["recording"]["askings"] = 5
    data["extraction"]["spread"] = {"en": {"found": 0.031, "dated": 0.5}}
    data["extraction"]["scores"]["llm.en.dated"] = 0.9
    changed = tmp_path / "thresholds.json"
    changed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "MEMORY", changed)
    text = module.render()
    assert "at most 3.1 points for the scores in this table" in text          # not the 50 of a start
    assert "gives a fact's start right 90.0 % in English" in text
    data["extraction"]["recording"]["askings"] = 1
    changed.write_text(json.dumps(data), encoding="utf-8")
    assert "a number from one asking can move" in module.render()


def test_the_pgvector_line_says_both_searches(tmp_path, monkeypatch) -> None:
    """Benchmark audit R7: the page names the run and says what the other
    search scored. Negative control: a run whose exact search differs is
    printed as different, with its numbers; one without it says so."""
    import json

    module = _page_module()
    data = json.loads(module.MEMORY.read_text(encoding="utf-8"))
    pg = data["pgvector"]
    if pg.get("status") == "verified":
        assert pg["run"] in module.render()
    pg.update(status="verified", mode="approximate", run="CI run 1")
    pg["exact_for_the_record"] = dict(pg["floors"])
    changed = tmp_path / "thresholds.json"
    changed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "MEMORY", changed)
    assert "Comparing every row (exact search) scores the same on every measure." in module.render()
    pg["exact_for_the_record"]["overall.precision"] = 0.7
    changed.write_text(json.dumps(data), encoding="utf-8")
    text = module.render()
    assert "scores differently on 1 measure" in text and "70.0 % precision" in text
    del pg["exact_for_the_record"]
    changed.write_text(json.dumps(data), encoding="utf-8")
    assert "The exact search was not measured in that run." in module.render()


def test_the_sealed_set_is_printed_apart_with_its_looks(tmp_path, monkeypatch) -> None:
    """Benchmark audit R8: the sealed set's last look, apart from the gated
    scores, with how many looks; nothing while none was made."""
    import json

    module = _page_module()
    data = json.loads(module.MEMORY.read_text(encoding="utf-8"))
    if not (data.get("sealed") or {}).get("looks"):
        assert "The sealed set" not in module.render()
    data["sealed"] = {"looks": [{"date": "2026-12-01", "questions": 96, "scores": {
        "sealed.overall.hit_rate": 0.8125, "sealed.overall.mrr": 0.7, "sealed.overall.precision": 0.6}}]}
    changed = tmp_path / "thresholds.json"
    changed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "MEMORY", changed)
    text = module.render()
    assert "The sealed set**: 96 questions" in text and "81.2 %" in text and "1 look so far" in text
