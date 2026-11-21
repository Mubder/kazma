"""The Spaces R3 study's evidence stays reproducible.

``docs/audits/evidence/spaces_r3/study.py`` measured who "I" is in a shared
space on 2026-11-18, and ``results.json`` beside it is what it measured. It
reads the memory benchmark's code, so a change there broke it unseen once
(the replay client gained a parameter, and the extraction scoring changed:
memory track review of piece 1). The study now keeps the extraction scoring
it measured with, and this test runs it whole -- every speaker run, dataset
v5 with one member, the extraction passes, the deep pass, the correction
rule -- and compares it with the committed file.

When it fails, something the study reads changed. If that change is meant
(the quick pass reads more, say), run ``python
docs/audits/evidence/spaces_r3/study.py run``, read what moved, and commit
results.json with the reason; the study's report cites its numbers, so say
there which ones changed. About 50 seconds: no model, recorded vectors only.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "docs" / "audits" / "evidence" / "spaces_r3" / "study.py"


def _study():
    spec = importlib.util.spec_from_file_location("_spaces_r3_study", STUDY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _differences(ran: object, held: object, where: str = "") -> list[str]:
    if isinstance(ran, dict) and isinstance(held, dict):
        out = [f"{where}.{k}: only in the run" for k in ran.keys() - held.keys()]
        out += [f"{where}.{k}: only in results.json" for k in held.keys() - ran.keys()]
        for k in ran.keys() & held.keys():
            out += _differences(ran[k], held[k], f"{where}.{k}")
        return out
    if isinstance(ran, list) and isinstance(held, list) and len(ran) == len(held):
        out = []
        for i, (a, b) in enumerate(zip(ran, held)):
            out += _differences(a, b, f"{where}[{i}]")
        return out
    if _same_rounded_number(ran, held):
        return []
    return [] if ran == held else [f"{where}: {json.dumps(held, ensure_ascii=False)[:120]} -> "
                                   f"{json.dumps(ran, ensure_ascii=False)[:120]}"]


#: The study rounds its scores to four decimals. A score that lands on a
#: rounding boundary rounds one way on Windows and the other on Linux (the
#: vector arithmetic's last bits differ by platform: CI's Linux run read
#: 0.5813 where the Windows run committed 0.5812, 2026-11-21), so two
#: numbers one unit of the last decimal apart are the same measurement.
_LAST_DIGIT = 1e-4


def _same_rounded_number(ran: object, held: object) -> bool:
    numbers = (int, float)
    if isinstance(ran, bool) or isinstance(held, bool):
        return False
    if not (isinstance(ran, numbers) and isinstance(held, numbers)):
        return False
    return abs(float(ran) - float(held)) <= _LAST_DIGIT + 1e-12


def test_the_study_replays_the_messages_it_asked() -> None:
    """The deep pass's message changed after the study (the memory framework
    audit's part C2: form 4). The study replays the 30 turns it measured, each
    by the message it asked then (form 1). Negative control: today's message
    is in neither recording, so a study that asked it again would replay
    nothing."""
    from kazma_core.memory.benchmark import _llm_blob, _text_key

    study = _study()
    turns = study._v1_turns()
    assert len(turns) == 30 and not any(t.get("new_in") for t in turns)
    deep = json.loads(study.DEEP.read_text(encoding="utf-8"))["replies"]
    first = json.loads(study.LLM_REPLIES.read_text(encoding="utf-8"))["replies"]
    asked = {_text_key(study._asked(t["user"])) for t in turns}
    assert asked <= set(first)
    cases = json.loads(study.CASES.read_text(encoding="utf-8"))
    assert all(_text_key(j["blob"]) in deep for j in study.deep_jobs(cases))
    today = {_text_key(_llm_blob(t["user"])) for t in turns}
    assert not today & (set(first) | set(deep))


def test_the_r3_study_reproduces_its_results() -> None:
    study = _study()
    report = json.loads(json.dumps(study.build_report(study._Vectors(record=False)), ensure_ascii=False))
    committed = json.loads(study.RESULTS.read_text(encoding="utf-8"))
    differences = _differences(report, committed)
    assert not differences, ("the R3 study no longer reproduces results.json:\n  "
                             + "\n  ".join(differences[:20]))


def test_what_broke_the_study_and_what_catches_it() -> None:
    """The witness: the study called the benchmark's replay client with the
    replies alone, which it no longer takes. The control: a number planted in
    the committed results is reported by the comparison."""
    import pytest
    from kazma_core.memory import benchmark

    with pytest.raises(TypeError):
        benchmark._ReplayClient({})  # the study's old call
    study = _study()
    committed = json.loads(study.RESULTS.read_text(encoding="utf-8"))
    planted = json.loads(json.dumps(committed))
    planted["extraction"]["deep_untagged"]["llm.ar.recall"] = 0.5
    assert _differences(planted, committed) == [
        ".extraction.deep_untagged.llm.ar.recall: 0.9091 -> 0.5"]


def test_one_unit_of_the_last_decimal_is_the_same_measurement() -> None:
    """CI's Linux run read 0.5813 where the Windows run committed 0.5812
    (2026-11-21): a score on a rounding boundary. One unit of the fourth
    decimal is the same measurement; two units, a count or a flag are not.
    Control: the exact comparison of before reports the platform's rounding."""
    assert _differences({"p": 0.5813}, {"p": 0.5812}) == []
    assert _differences({"p": 0.5811}, {"p": 0.5812}) == []
    assert _differences({"p": 0.5814}, {"p": 0.5812}) == [".p: 0.5812 -> 0.5814"]
    assert _differences({"n": 3}, {"n": 2}) == [".n: 2 -> 3"]
    assert _differences({"f": True}, {"f": False}) == [".f: false -> true"]
    exact = [] if 0.5813 == 0.5812 else ["differs"]
    assert exact == ["differs"]
