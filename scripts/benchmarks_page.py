"""Write the public benchmarks page from the committed results (REMAINING 4.8).

``docs/docs/reference/benchmarks.md`` is made from the three result files the
test suite holds every change to, never typed by hand:

- memory recall and fact extraction: ``tests/fixtures/memory_bench/thresholds.json``
  (``scripts/memory_bench.py``, ``tests/test_memory_benchmark.py``);
- Knowledge Library search: ``tests/fixtures/knowledge_bench/thresholds.json``
  (``scripts/knowledge_bench.py``, ``tests/test_knowledge_benchmark.py``);
- reading documents, Arabic and English, with and without OCR:
  ``tests/fixtures/documents/fidelity_thresholds.json``
  (``scripts/document_fidelity.py``, ``tests/test_document_fidelity.py``).

    .venv/Scripts/python.exe scripts/benchmarks_page.py          # write it
    .venv/Scripts/python.exe scripts/benchmarks_page.py --check  # 1 when stale

``tests/test_benchmarks_page.py`` runs ``--check``: a lock that changes a
result changes the page in the same commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "docs" / "reference" / "benchmarks.md"
MEMORY = ROOT / "tests" / "fixtures" / "memory_bench" / "thresholds.json"
KNOWLEDGE = ROOT / "tests" / "fixtures" / "knowledge_bench" / "thresholds.json"
FIDELITY = ROOT / "tests" / "fixtures" / "documents" / "fidelity_thresholds.json"

_MEMORY_NAMES = {
    "fact": "A fact you told it",
    "single": "Something said in one turn",
    "multi": "Something spread over several turns",
    "update": "A fact that changed (the newest wins)",
    "paraphrase": "Asked in other words",
    "keyword": "Asked with the exact words",
    "assistant": "Something Kazma itself said",
    "overview": "Catch-me-up questions",
    "arabic": "Asked in Arabic",
    "mixed": "Arabic and English mixed",
    "noise": "A question with noise around it",
}
_KNOWLEDGE_NAMES = {
    "exact": "The exact words of the page",
    "paraphrase": "Asked in other words",
    "arabic": "Arabic",
    "arabic_paraphrase": "Arabic, in other words",
    "mixed": "Arabic and English mixed",
    "cross": "Asked in one language, written in the other",
    "multi": "An answer spread over several pages",
}
_FIDELITY_NAMES = {
    "token_recall": "words found", "segment_recall": "passages found", "order": "in order",
    "number_recall": "numbers exact", "table_cell_recall": "table cells", "punctuation": "punctuation",
    "cer": "character errors", "wer": "word errors",
}
_LOWER_IS_BETTER = {"cer", "wer"}


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f} %"


def _num(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


_LANGS = (("en", "English"), ("ar", "Arabic"), ("mixed", "mixed"))


def _spread_sentence(spread: dict) -> str:
    """How far the table's model-pass scores could move asked again: the
    widest spread the lock keeps for them (two standard errors over the
    askings)."""
    table = ("found", "named", "right", "quiet")
    widest = max((v for row in spread.values() for k, v in row.items() if k in table), default=0.0)
    return (f"so the lock keeps each score's spread: two standard errors over the askings, at most "
            f"{widest * 100:.1f} points for the scores in this table.")


def _c2_sentence(ex: dict) -> str:
    """What the model pass does with what memory gives it besides the turn
    (the memory framework audit's part C2), per language, from the lock."""
    parts = []
    for score, what in (("dated", "gives a fact's start right"), ("replaced", "names the held fact it replaces"),
                        ("ended", "reads that a fact ended")):
        langs = [f"{_pct(ex[f'llm.{lang}.{score}'])} in {name}" for lang, name in _LANGS
                 if f"llm.{lang}.{score}" in ex]
        if langs:
            parts.append(f"{what} {', '.join(langs)}")
    if not parts:
        return ""
    return ("On the ten turns that give it the turn's day, the turn before and the facts held, the model pass "
            + "; ".join(parts) + ".")


def _other_search(pg: dict) -> str:
    """The pgvector run's other search, as the lock keeps it for the record
    (benchmark audit R7: exact against approximate is measured, not assumed)."""
    mode = pg.get("mode") or "approximate"
    other = "exact" if mode == "approximate" else "approximate"
    held = pg.get(f"{other}_for_the_record") or {}
    if not held:
        return f"The {other} search was not measured in that run."
    floors = pg.get("floors") or {}
    differ = sorted(k for k in set(floors) | set(held) if floors.get(k) != held.get(k))
    name = "Comparing every row (exact search)" if other == "exact" else "pgvector's own index (approximate search)"
    if not differ:
        return f"{name} scores the same on every measure."
    return (f"{name} scores differently on {_count(len(differ), 'measure')}: "
            f"{_pct(held.get('overall.hit_rate'))} hit rate, {_num(held.get('overall.mrr'))} MRR and "
            f"{_pct(held.get('overall.precision'))} precision.")


def _rests_on(extraction: dict, kind: str, lang: str) -> str:
    """What one extraction row rests on, from the lock (the owner's decision
    6): a model-pass row the lock does not count is not printed as a number."""
    row = ((extraction.get("rests_on") or {}).get(kind) or {}).get(lang)
    if not row:
        raise ValueError(f"the lock does not say what {kind}.{lang} rests on: "
                         "python scripts/memory_bench.py lock")
    turns = int(row["turns"])
    if kind != "llm":
        return _count(turns, "turn")
    askings = int(row["askings"])
    each = askings // turns if turns else 0
    return f"{_count(askings, 'asking')} ({_count(turns, 'turn')}, {each} each)"


def _edges(data: dict) -> list[str]:
    """The hits that hold only by the recorded vectors' rounding (the edge
    list in the lock, benchmark audit R4)."""
    edges = data.get("edges") or {}
    out = []
    for hit in edges.get("hits") or []:
        out.append(
            f"One answer stands at the edge of what the recorded vectors can tell: \"{hit['question']}\" "
            f"({_MEMORY_NAMES.get(hit['category'], hit['category'])}) is "
            f"{'shown' if hit['hit'] else 'missed'} by {abs(hit['margin'])} of evidence, and with the vectors "
            f"moved inside their rounding it changes in {hit['flipped']} of {edges['draws']} replays. Its hit "
            "counts in the table above; the lock names it (`lock --edge`) instead of moving a threshold for it.")
    return out + [""] if out else []


def _sealed(data: dict) -> list[str]:
    """The sealed set's last look, apart from the gated scores (benchmark
    audit R8): it is scored only on request, every look logged in the lock."""
    looks = (data.get("sealed") or {}).get("looks") or []
    if not looks:
        return []
    last = looks[-1]
    s = last.get("scores") or {}
    return [
        f"**The sealed set**: {last.get('questions')} questions written by a session that does not build "
        f"recall, never in the per-commit gate and scored only on request: "
        f"{_pct(s.get('sealed.overall.hit_rate'))} hit rate, {_num(s.get('sealed.overall.mrr'))} MRR and "
        f"{_pct(s.get('sealed.overall.precision'))} precision at its last look ({last.get('date')}; "
        f"{_count(len(looks), 'look')} so far, each logged with its reason).",
        "",
    ]


def _memory(data: dict) -> list[str]:
    s = data["scores"]
    lines = [
        "## Memory: does it find what you told it? {#memory}",
        "",
        f"Dataset version {data['dataset_version']}: a private memory seeded the product's way (turns, facts, "
        "weekly summaries), then asked questions through the real `recall()`, with the meaning-search model's "
        "recorded vectors. **Hit rate**: the answer is among what Kazma shows the model. **MRR**: how high it "
        "ranks (1 = first). **Precision**: how much of what is shown is about the question.",
        "",
        "| Question | Hit rate | MRR | Precision |",
        "|---|---|---|---|",
    ]
    for key, name in _MEMORY_NAMES.items():
        if f"{key}.hit_rate" in s:
            lines.append(f"| {name} | {_pct(s.get(f'{key}.hit_rate'))} | {_num(s.get(f'{key}.mrr'))} | "
                         f"{_pct(s.get(f'{key}.precision'))} |")
    lines.append(f"| **All questions** | **{_pct(s.get('overall.hit_rate'))}** | **{_num(s.get('overall.mrr'))}** | "
                 f"**{_pct(s.get('overall.precision'))}** |")
    lines.append(f"| **Second set** (see below) | **{_pct(s.get('second.overall.hit_rate'))}** | "
                 f"**{_num(s.get('second.overall.mrr'))}** | **{_pct(s.get('second.overall.precision'))}** |")
    target = (data.get("targets") or {}).get("abstain.hit_rate")
    lines += [
        "",
        f"Questions it has no memory for are answered with nothing in {_pct(s.get('abstain.hit_rate'))} of cases "
        f"({_pct(s.get('second.abstain.hit_rate'))} in the second set); the goal is {_pct(target)}: the weakest "
        "number on this page.",
        "",
        "The second set was written after the settings were chosen, and no setting is chosen by sweeping it. "
        "It is not an unseen test: two decisions were taken by looking at it, and a change that lowers it "
        "fails the build like any other score, so it is a second check on the same memories. A set nobody "
        "has looked at would be a sealed one, scored only on request.",
        "",
        *_sealed(data),
        *_edges(data),
    ]
    extraction = data.get("extraction") or {}
    ex = extraction.get("scores") or {}
    if ex:
        recording = extraction.get("recording") or {}
        askings = int(recording.get("askings") or 1)
        pending = extraction.get("pending_recording") or {}
        model_label = (f"Model pass ({askings} asking{'' if askings == 1 else 's'} a turn"
                       f"{', previous prompt' if pending else ''})")
        lines += [
            "### Learning facts from a conversation {#memory-facts}",
            "",
            "Turns in English, Arabic and both, with the facts a person would expect remembered. **Quick pass**: "
            "Kazma's own rules, on every turn. **Model pass**: the model reading the turn afterwards (recorded "
            "replies). **Found**: the facts expected, kept with their whole value about the right person, whatever "
            "the model called them (a name that turns the fact around, such as \"dislikes\" or \"former\", does "
            "not count). **Same name**: of those, the share kept under a name on the list (a fact kept under two "
            "names is two facts to Kazma). **Right**: the facts kept that hold a value the turn gives, about the "
            "right person, and are not on the turn's list of what must not be kept; the name is scored apart. "
            "**Quiet**: turns with nothing to learn that taught nothing. **Rests on**: the turns behind the row; "
            "for the model pass, the askings (one asking is one model call for one turn).",
            "",
            "| Pass | Language | Found | Same name | Right | Quiet | Rests on |",
            "|---|---|---|---|---|---|---|",
        ]
        for kind, label in (("heuristic", "Quick pass"), ("llm", model_label)):
            for lang, lang_label in (("en", "English"), ("ar", "Arabic"), ("mixed", "Mixed")):
                if f"{kind}.{lang}.found" in ex:
                    lines.append(f"| {label} | {lang_label} | {_pct(ex[f'{kind}.{lang}.found'])} | "
                                 f"{_pct(ex[f'{kind}.{lang}.named'])} | {_pct(ex[f'{kind}.{lang}.right'])} | "
                                 f"{_pct(ex[f'{kind}.{lang}.quiet'])} | {_rests_on(extraction, kind, lang)} |")
        if recording:
            lines += [
                "",
                f"The model pass is {recording.get('model')}'s replies, recorded on {recording.get('recorded_on')}, "
                f"{askings} asking{'' if askings == 1 else 's'} a turn. Asked again, a model answers a little "
                "differently, " + (_spread_sentence(extraction.get("spread") or {}) if askings > 1 else
                                   "so a number from one asking can move by a few facts with nothing changed."),
            ]
            told = _c2_sentence(ex)
            if told:
                lines.append(told)
            if pending:
                changes = len(pending.get("changes") or [])
                lines.append(
                    f"Kazma's extraction prompt has changed since that recording ({changes} "
                    f"change{'' if changes == 1 else 's'} since {pending.get('since')}). The model-pass rows replay "
                    "the recording through today's code, which checks that the code reading the model's replies "
                    "still keeps what it kept; they do not measure the new prompt, which is measured when it is "
                    "recorded.")
        lines.append("")
    pg = data.get("pgvector") or {}
    floors = pg.get("floors") or {}
    if floors:
        scores = (f"{_pct(floors.get('overall.hit_rate'))} hit rate, {_num(floors.get('overall.mrr'))} MRR and "
                  f"{_pct(floors.get('overall.precision'))} precision")
        if pg.get("status") == "verified":
            lines += [f"With Postgres and pgvector as the memory store ({pg.get('run')}, {pg.get('mode')} "
                      f"search), the same questions score {scores}. {_other_search(pg)}", ""]
        else:
            lines += [f"With Postgres and pgvector as the memory store, a run made by hand on 2026-10-14 scored "
                      f"{scores}. CI's pgvector job now measures it, with pgvector's own search and with every "
                      "row compared, and these numbers are not locked from it yet.", ""]
    lines += ["Reproduce: `python scripts/memory_bench.py run` (add `--real` to use the real model), and "
              "`python -m pytest tests/test_memory_benchmark.py`.", ""]
    return lines


def _knowledge(data: dict) -> list[str]:
    s = data["scores"]
    lines = [
        "## Knowledge Library: does search find the right page? {#knowledge}",
        "",
        f"Dataset version {data['dataset_version']}: English, Arabic and mixed pages published into a private "
        "library the product's way, then searched the way the agent searches (**tool**) and the way a chat turn "
        "is given library text (**turn**), with meaning search and with keywords alone.",
        "",
        "| Question | Tool: hit rate | Tool: MRR | Turn: hit rate | Turn: precision |",
        "|---|---|---|---|---|",
    ]
    for key, name in _KNOWLEDGE_NAMES.items():
        if f"hybrid.explicit.{key}.hit_rate" in s:
            lines.append(f"| {name} | {_pct(s.get(f'hybrid.explicit.{key}.hit_rate'))} | "
                         f"{_num(s.get(f'hybrid.explicit.{key}.mrr'))} | {_pct(s.get(f'hybrid.inject.{key}.hit_rate'))} | "
                         f"{_pct(s.get(f'hybrid.inject.{key}.precision'))} |")
    lines += [
        "",
        "| All questions | Hit rate | MRR | Precision | Nothing shown when nothing answers |",
        "|---|---|---|---|---|",
    ]
    for prefix, label in (("hybrid.explicit", "Tool, meaning search"), ("hybrid.inject", "Turn, meaning search"),
                          ("keyword.explicit", "Tool, keywords only"), ("keyword.inject", "Turn, keywords only"),
                          ("hybrid.heldout.explicit", "Tool, held out"), ("hybrid.heldout.inject", "Turn, held out")):
        if f"{prefix}.overall.hit_rate" in s:
            lines.append(f"| {label} | {_pct(s.get(f'{prefix}.overall.hit_rate'))} | {_num(s.get(f'{prefix}.overall.mrr'))} | "
                         f"{_pct(s.get(f'{prefix}.overall.precision'))} | {_pct(s.get(f'{prefix}.overall.abstention'))} |")
    lines += ["", "Reproduce: `python scripts/knowledge_bench.py run` (`--real` for the real model, `--chroma` for "
              "the real vector store), and `python -m pytest tests/test_knowledge_benchmark.py`.", ""]
    return lines


def _fidelity_row(case: str, floors: dict, qualified: bool) -> str:
    cells = []
    for metric in ("token_recall", "number_recall", "punctuation", "table_cell_recall", "cer", "wer"):
        value = floors.get(metric)
        if value is None:
            cells.append("–")
        elif metric in _LOWER_IS_BETTER:
            cells.append(f"≤ {_pct(value)}")
        else:
            cells.append(f"≥ {_pct(value)}")
    return f"| `{case}` | {'yes' if qualified else 'no'} | " + " | ".join(cells) + " |"


def _fidelity(data: dict) -> list[str]:
    header = ("| Document | Qualified | Words found | Numbers exact | Punctuation | Table cells | Character errors | "
              "Word errors |")
    rule = "|---|---|---|---|---|---|---|---|"
    lines = [
        "## Reading documents, in Arabic and English {#documents}",
        "",
        f"Corpus version {data['corpus_version']}: documents Kazma builds itself (Word, PDF, HTML; scanned pages "
        "for OCR) from a known text, read back by the real parser, measured word by word. The numbers are the "
        "**floors** every change must reach (the measurement, less a small margin for OCR); a case is "
        "**qualified** when it meets the release targets. Measured on: " + data.get("measured_on", "") + ".",
        "",
        "### Text documents {#documents-text}",
        "",
        header,
        rule,
    ]
    qualified = set(data.get("qualified") or [])
    for case, floors in sorted((data.get("thresholds") or {}).items()):
        lines.append(_fidelity_row(case, floors, case in qualified))
    engines = data.get("ocr_engines") or {}
    for engine, label in (("neural", "Scanned pages, Kazma's neural OCR (the default)"),
                          ("tesseract", "Scanned pages, Tesseract alone")):
        block = engines.get(engine) if isinstance(engines, dict) else None
        if not block:
            continue
        lines += ["", f"### {label} {{#documents-ocr-{engine}}}", "", header, rule]
        q = set(block.get("qualified") or [])
        for case, floors in sorted((block.get("thresholds") or {}).items()):
            lines.append(_fidelity_row(case, floors, case in q))
        reasons = block.get("notes") or {}
        for case, why in sorted(reasons.items()):
            lines.append(f"\n- `{case}` is not qualified: {why}")
    lines += ["", "Reproduce: `python scripts/document_fidelity.py`, and "
              "`python -m pytest tests/test_document_fidelity.py`.", ""]
    return lines


def render() -> str:
    memory = json.loads(MEMORY.read_text(encoding="utf-8"))
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    fidelity = json.loads(FIDELITY.read_text(encoding="utf-8"))
    lines = [
        "---",
        "title: Benchmarks",
        "---",
        "",
        "# Benchmarks",
        "",
        "> This page is published at [kazma.ai/docs/benchmarks/](https://kazma.ai/docs/benchmarks/) "
        "([Arabic](https://kazma.ai/ar/docs/benchmarks/)).",
        "",
        "How well Kazma remembers, finds and reads, measured on versioned datasets kept in its repository and "
        "held by its tests: a change that makes a number worse fails the build. This page is written from those "
        "results (`scripts/benchmarks_page.py`), so it says what the code does today. Every dataset, script and "
        "result file is in the repository; the commands under each table reproduce it.",
        "",
        "The datasets are Kazma's own, built for these questions in English, Arabic and both; the memory and "
        "Knowledge datasets each keep a second set of questions apart from tuning. They are not a public "
        "leaderboard's: compare runs of Kazma with each other, not with numbers measured elsewhere.",
        "",
    ]
    lines += _memory(memory) + _knowledge(knowledge) + _fidelity(fidelity)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write docs/docs/reference/benchmarks.md from the results.")
    parser.add_argument("--check", action="store_true", help="exit 1 when the page is not what the results say")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = PAGE.read_text(encoding="utf-8") if PAGE.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{PAGE.relative_to(ROOT)} is stale: run scripts/benchmarks_page.py")
            return 1
        print("the benchmarks page matches the results")
        return 0
    PAGE.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
