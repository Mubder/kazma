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
    assert "held out" in text.lower() and "Arabic" in text
